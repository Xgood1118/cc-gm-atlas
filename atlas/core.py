# -*- coding: utf-8 -*-
"""精灵图集打包器。

只用 Python 标准库实现：

* ``Rect``          条目（名字 + 源尺寸 + 打包后的位置）
* ``load_manifest`` 读取 ``name width height`` 清单
* ``pack``          去重 + 旋转 + MaxRects 排布（返回是否全部放下）
* ``export_json``   输出帧坐标、旋转标记与复用关系
* ``export_png``    手写 PNG 编码器，输出逐字节可复现的图集

排布策略
--------
相同尺寸的条目只排一次，其余条目直接复用第一块区域（``duplicate_of``）。
非正方形条目允许旋转 90 度。对若干种排序 / 旋转策略 / MaxRects 启发式
分别排布，取“占用包围盒”最小且旋转最少的一组结果，因此结果与输入顺序
无关、可复现。
"""

import json
import struct
import zlib

_BACKGROUND = (24, 26, 32)

# MaxRects 的几种放置启发式，名字仅用于稳定地打破平局。
_HEURISTICS = ("bssf", "baf", "bl")


class Rect(object):
    """一个图集条目。

    打包后 ``w`` / ``h`` 表示该条目在**图集上实际占用的区域**，旋转过的
    条目宽高会互换，因此 ``(x, y, w, h)`` 永远是图集内的轴对齐矩形。
    清单中的原始尺寸保留在 ``source_w`` / ``source_h``。
    """

    def __init__(self, name, w, h):
        self.name = name
        self.source_w = int(w)
        self.source_h = int(h)
        self.w = self.source_w
        self.h = self.source_h
        self.x = 0
        self.y = 0
        self.rotated = False
        self.duplicate_of = None

    def area(self):
        """条目面积（旋转不改变面积）。"""
        return self.w * self.h

    def placed_size(self):
        """返回该条目在图集中实际占用的 ``(width, height)``。"""
        return self.w, self.h

    def __repr__(self):
        return "Rect(%s, %dx%d @ %d,%d)" % (self.name, self.w, self.h, self.x, self.y)


def load_manifest(path):
    """读取清单。每行 ``name width height``，忽略空行与 ``#`` 注释。"""
    items = []
    with open(path, "r", encoding="utf-8") as f:
        for line_no, line in enumerate(f, 1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split()
            if len(parts) < 3:
                raise ValueError(
                    "%s:%d: 需要 name width height，实际是 %r" % (path, line_no, line)
                )
            name, w, h = parts[0], int(parts[1]), int(parts[2])
            if w < 0 or h < 0:
                raise ValueError("%s:%d: 尺寸不能为负" % (path, line_no))
            items.append(Rect(name, w, h))
    return items


# ---------------------------------------------------------------------------
# 排布
# ---------------------------------------------------------------------------

def _prune_free(free):
    """去掉被其它空闲矩形完全包含的矩形。"""
    result = []
    n = len(free)
    for i in range(n):
        a = free[i]
        ax, ay, aw, ah = a
        if aw <= 0 or ah <= 0:
            continue
        keep = True
        for j in range(n):
            if i == j:
                continue
            b = free[j]
            bx, by, bw, bh = b
            if bw <= 0 or bh <= 0:
                continue
            if ax >= bx and ay >= by and ax + aw <= bx + bw and ay + ah <= by + bh:
                if a == b:
                    # 完全相同的矩形只保留索引靠前的那一个。
                    if j < i:
                        keep = False
                        break
                    continue
                keep = False
                break
        if keep:
            result.append(a)
    return result


def _split_free(free, x, y, w, h):
    """从空闲区域中挖掉 ``(x, y, w, h)`` 的占用足迹。"""
    out = []
    for fx, fy, fw, fh in free:
        if x >= fx + fw or x + w <= fx or y >= fy + fh or y + h <= fy:
            out.append((fx, fy, fw, fh))
            continue
        if x > fx:
            out.append((fx, fy, x - fx, fh))
        if x + w < fx + fw:
            out.append((x + w, fy, fx + fw - (x + w), fh))
        if y > fy:
            out.append((fx, fy, fw, y - fy))
        if y + h < fy + fh:
            out.append((fx, y + h, fw, fy + fh - (y + h)))
    return _prune_free(out)


def _placement_score(free_rect, fw, fh, heuristic):
    fx, fy, frw, frh = free_rect
    dw = frw - fw
    dh = frh - fh
    if heuristic == "baf":
        return (frw * frh - fw * fh, min(dw, dh), max(dw, dh), fy, fx)
    if heuristic == "bl":
        return (fy + fh, fx, min(dw, dh), max(dw, dh))
    # bssf：短边贴合
    return (min(dw, dh), max(dw, dh), fy, fx)


def _best_placement(free, fw, fh, heuristic):
    best = None
    for index, fr in enumerate(free):
        if fw <= fr[2] and fh <= fr[3]:
            score = _placement_score(fr, fw, fh, heuristic)
            key = score + (index,)
            if best is None or key < best[0]:
                best = (key, fr[0], fr[1])
    return best


def _sort_keys():
    """一组候选排序方式；每一种都返回可比较的降序键。"""
    return (
        lambda r: (-r.area(), -max(r.w, r.h), r.name),
        lambda r: (-max(r.w, r.h), -min(r.w, r.h), r.name),
        lambda r: (-r.h, -r.w, r.name),
        lambda r: (-r.w, -r.h, r.name),
        lambda r: (-(r.w + r.h), -r.area(), r.name),
        lambda r: (-abs(r.w - r.h), -r.area(), r.name),
    )


def _pack_layout(specs, canvas_w, canvas_h, padding, sort_key, rotate_policy,
                 heuristic):
    """用一种策略排布 ``specs``（每个元素是 ``(w, h)``）。

    返回 ``(positions, failure)``：成功时 ``positions`` 是按 ``specs`` 顺序
    的 ``(x, y, rotated, placed_w, placed_h)``，``failure`` 为 ``None``；
    失败时 ``positions`` 为已放下的部分，``failure`` 是放不下的下标。
    """
    order = sorted(range(len(specs)), key=lambda i: sort_key(_SpecView(specs[i])))
    free = [(0, 0, canvas_w, canvas_h)]
    positions = [None] * len(specs)
    for index in order:
        w, h = specs[index]
        options = [(w, h, False)]
        if w != h and rotate_policy == "all":
            options.append((h, w, True))
        elif w != h and rotate_policy == "tall" and h > w:
            options.append((h, w, True))

        best = None
        for ow, oh, rotated in options:
            fw, fh = ow + padding, oh + padding
            if fw > canvas_w or fh > canvas_h:
                continue
            placement = _best_placement(free, fw, fh, heuristic)
            if placement is None:
                continue
            score, x, y = placement
            # 平局时优先不旋转。
            key = score + (1 if rotated else 0,)
            if best is None or key < best[0]:
                best = (key, x, y, ow, oh, rotated)

        if best is None:
            return positions, index

        _, x, y, ow, oh, rotated = best
        free = _split_free(free, x, y, ow + padding, oh + padding)
        positions[index] = (x, y, rotated, ow, oh)
    return positions, None


class _SpecView(object):
    """让排序函数能同时看到 ``(w, h)`` 和面积。"""

    __slots__ = ("w", "h", "name")

    def __init__(self, spec):
        self.w, self.h = spec
        self.name = ""

    def area(self):
        return self.w * self.h


def _candidate_bins(specs, atlas_w, atlas_h, padding):
    """生成候选排布区域。

    图集本身可能远大于清单的实际需求，直接铺满 512x512 会让条目散布在
    大块空白里。这里按“理论所需面积的若干倍”枚举一组长宽比，得到更紧凑
    的包围盒；所有候选都不会超过图集尺寸。
    """
    needed = sum(w * h for w, h in specs)
    max_w = max((w for w, _ in specs), default=0) + padding
    max_h = max((h for _, h in specs), default=0) + padding

    bins = [(atlas_w, atlas_h, True)]
    seen = set(bins)
    if needed <= 0:
        return bins

    aspect_ratios = (1.0, 1.2, 1.5, 2.0, 2.5, 3.0, 0.83, 0.67, 0.5, 0.4)
    # 从 1.0 倍开始，逐步放大到 2 倍，给排布留出浪费空间。
    for fill in (1.0, 1.04, 1.08, 1.14, 1.22, 1.35, 1.5, 1.75, 2.0):
        for ratio in aspect_ratios:
            area = needed * fill
            w = int(round((area * ratio) ** 0.5))
            h = int(round((area / ratio) ** 0.5))
            w = min(atlas_w, max(w + padding, max_w))
            h = min(atlas_h, max(h + padding, max_h))
            if w * h < needed:
                continue
            key = (w, h, False)
            if key not in seen:
                seen.add(key)
                bins.append(key)
    return bins


def _choose_layout(groups, atlas_w, atlas_h, padding):
    """在候选区域内尝试多种策略，返回 ``(positions, failure_index)``。"""
    specs = [(g[0].source_w, g[0].source_h) for g in groups]
    bins = _candidate_bins(specs, atlas_w, atlas_h, padding)

    best = None
    best_failure = None
    for bin_w, bin_h, _full in bins:
        for sort_key in _sort_keys():
            for policy in ("none", "tall", "all"):
                for heuristic in _HEURISTICS:
                    positions, failure = _pack_layout(
                        specs, bin_w, bin_h, padding,
                        sort_key, policy, heuristic,
                    )
                    if failure is None:
                        score = _layout_score(positions, specs)
                        # 密度优先，其次包围盒更小、旋转更少。
                        x0, y0, x1, y1 = _bbox(positions, specs)
                        bbox_area = max(1, (x1 - x0) * (y1 - y0))
                        used = sum(p[3] * p[4] for p in positions)
                        density = used / bbox_area
                        key = (-round(density, 9), bbox_area,
                               score[3], bin_w * bin_h, bin_w)
                        if best is None or key < best[0]:
                            best = (key, positions)
                    else:
                        placed = sum(1 for p in positions if p is not None)
                        if best_failure is None or placed > best_failure[0]:
                            best_failure = (placed, failure)

    if best is not None:
        return best[1], None
    return None, best_failure[1] if best_failure is not None else 0


def _bbox(positions, specs):
    xs = []
    ys = []
    xe = []
    ye = []
    for (x, y, _rotated, w, h), spec in zip(positions, specs):
        xs.append(x)
        ys.append(y)
        xe.append(x + w)
        ye.append(y + h)
    if not xs:
        return 0, 0, 0, 0
    return min(xs), min(ys), max(xe), max(ye)


def _layout_score(positions, specs):
    x0, y0, x1, y1 = _bbox(positions, specs)
    w = x1 - x0
    h = y1 - y0
    rotated = sum(1 for p in positions if p[2])
    return (w * h, h, w, rotated)


def pack(rects, atlas_width, atlas_height, padding=0):
    """排布条目，返回是否全部放下。

    相同尺寸的条目共享同一块区域；非正方形条目可能旋转 90 度。
    失败时把放不下的条目记录在 ``pack.last_failure`` 上（``Rect`` 或
    ``None``），不抛异常。
    """
    pack.last_failure = None
    atlas_width = int(atlas_width)
    atlas_height = int(atlas_height)
    padding = max(0, int(padding))

    for r in rects:
        r.w = r.source_w
        r.h = r.source_h
        r.x = 0
        r.y = 0
        r.rotated = False
        r.duplicate_of = None

    if not rects:
        return True

    if atlas_width <= 0 or atlas_height <= 0:
        pack.last_failure = rects[0]
        return False

    # 按源尺寸分组，保持清单中的首次出现顺序。
    groups = []
    group_index = {}
    for r in rects:
        key = (r.source_w, r.source_h)
        if key not in group_index:
            group_index[key] = len(groups)
            groups.append([r])
        else:
            groups[group_index[key]].append(r)

    # 先处理单条就比图集大的情况（考虑旋转后仍放不下）。
    for group in groups:
        r = group[0]
        fits = ((r.source_w + padding <= atlas_width
                 and r.source_h + padding <= atlas_height)
                or (r.source_h + padding <= atlas_width
                    and r.source_w + padding <= atlas_height))
        if not fits:
            pack.last_failure = r
            return False

    positions, failure = _choose_layout(
        groups, atlas_width, atlas_height, padding
    )
    if failure is not None:
        pack.last_failure = groups[failure][0]
        return False

    for group, (x, y, rotated, pw, ph) in zip(groups, positions):
        for member in group:
            member.x = x
            member.y = y
            member.w = pw
            member.h = ph
            member.rotated = rotated
            member.duplicate_of = None
        for duplicate in group[1:]:
            duplicate.duplicate_of = group[0].name
    return True


pack.last_failure = None


# ---------------------------------------------------------------------------
# 导出
# ---------------------------------------------------------------------------

def export_json(rects, atlas_size, path):
    """导出帧坐标；复用条目带 ``duplicate_of`` 指向首个条目。"""
    frames = []
    for r in rects:
        pw, ph = r.placed_size()
        frames.append({
            "name": r.name,
            "x": int(r.x),
            "y": int(r.y),
            "w": int(pw),
            "h": int(ph),
            "rotated": bool(r.rotated),
            "duplicate_of": r.duplicate_of,
        })
    data = {"size": [int(atlas_size[0]), int(atlas_size[1])], "frames": frames}
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
    return data


def _hsv_to_rgb(h, s, v):
    h = h % 1.0
    i = int(h * 6.0)
    f = h * 6.0 - i
    p = v * (1.0 - s)
    q = v * (1.0 - f * s)
    t = v * (1.0 - (1.0 - f) * s)
    i %= 6
    if i == 0:
        r, g, b = v, t, p
    elif i == 1:
        r, g, b = q, v, p
    elif i == 2:
        r, g, b = p, v, t
    elif i == 3:
        r, g, b = p, q, v
    elif i == 4:
        r, g, b = t, p, v
    else:
        r, g, b = v, p, q
    return (int(round(r * 255)), int(round(g * 255)), int(round(b * 255)))


def _palette(rects):
    """按清单顺序给每个独立条目分配可区分的颜色；复用条目沿用首个颜色。"""
    colors = {}
    unique_index = 0
    for r in rects:
        if r.duplicate_of is not None:
            continue
        hue = (unique_index * 0.618033988749895) % 1.0
        colors[r.name] = _hsv_to_rgb(hue, 0.62, 0.95)
        unique_index += 1
    return colors


def _png_chunk(tag, payload):
    return (
        struct.pack(">I", len(payload))
        + tag
        + payload
        + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF)
    )


def _encode_png(width, height, raw):
    """把 ``raw``（每行 filter 0 + RGB）编码成 PNG 字节串。"""
    signature = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    idat = zlib.compress(bytes(raw), 9)
    return (
        signature
        + _png_chunk(b"IHDR", ihdr)
        + _png_chunk(b"IDAT", idat)
        + _png_chunk(b"IEND", b"")
    )


def export_png(rects, atlas_size, path):
    """把图集写成 PNG，尺寸严格等于 ``atlas_size``。

    每个独立条目填充一种确定性颜色，复用条目与首个条目同色。像素按
    ``x, y, w, h`` 精确填充，越界部分会被裁掉，不会抛异常。输出不含
    时间戳等易变信息，同样输入会得到逐字节相同的文件。
    """
    width, height = int(atlas_size[0]), int(atlas_size[1])
    if width <= 0 or height <= 0:
        raise ValueError("图集尺寸必须为正数: %r" % (atlas_size,))

    row_bytes = width * 3
    background = bytes(_BACKGROUND) * width
    raw = bytearray()
    for _ in range(height):
        raw.append(0)  # filter type 0 (None)
        raw.extend(background)

    colors = _palette(rects)
    for r in rects:
        if r.duplicate_of is not None:
            continue  # 与首个条目重合，不必重复绘制
        pw, ph = r.placed_size()
        x0 = max(0, min(width, int(r.x)))
        y0 = max(0, min(height, int(r.y)))
        x1 = max(0, min(width, int(r.x) + pw))
        y1 = max(0, min(height, int(r.y) + ph))
        if x1 <= x0 or y1 <= y0:
            continue
        color = bytes(colors.get(r.name, (255, 0, 255)))
        span = color * (x1 - x0)
        for y in range(y0, y1):
            offset = y * (row_bytes + 1) + 1 + x0 * 3
            raw[offset:offset + len(span)] = span

    png = _encode_png(width, height, raw)
    with open(path, "wb") as f:
        f.write(png)
    return png


# ---------------------------------------------------------------------------
# 命令行
# ---------------------------------------------------------------------------

def _utilization(rects, width, height):
    """返回 (唯一占用面积, 画布面积, 占用包围盒紧凑度)。"""
    unique_area = 0
    xs = []
    ys = []
    xe = []
    ye = []
    for r in rects:
        if r.duplicate_of is not None:
            continue
        pw, ph = r.placed_size()
        unique_area += pw * ph
        xs.append(r.x)
        ys.append(r.y)
        xe.append(r.x + pw)
        ye.append(r.y + ph)
    canvas = width * height
    if not xs:
        return 0, canvas, 0.0
    bbox = (max(xe) - min(xs)) * (max(ye) - min(ys))
    density = unique_area / bbox if bbox else 0.0
    return unique_area, canvas, density


def main(argv=None):
    import argparse
    import sys

    ap = argparse.ArgumentParser(prog="atlas")
    ap.add_argument("manifest")
    ap.add_argument("--width", type=int, default=512)
    ap.add_argument("--height", type=int, default=512)
    ap.add_argument("--padding", type=int, default=1)
    ap.add_argument("--out-png", default="atlas.png")
    ap.add_argument("--out-json", default="atlas.json")
    args = ap.parse_args(argv)

    try:
        rects = load_manifest(args.manifest)
    except (OSError, ValueError) as exc:
        print("错误: 读取清单失败: %s" % exc, file=sys.stderr)
        return 1

    ok = pack(rects, args.width, args.height, args.padding)
    if not ok:
        failed = getattr(pack, "last_failure", None)
        name = failed.name if failed is not None else "<未知>"
        print(
            "错误: 条目 %r 放不下（图集 %dx%d，边距 %d）"
            % (name, args.width, args.height, args.padding),
            file=sys.stderr,
        )
        return 1

    export_png(rects, (args.width, args.height), args.out_png)
    export_json(rects, (args.width, args.height), args.out_json)

    unique_area, canvas, density = _utilization(rects, args.width, args.height)
    reused = sum(1 for r in rects if r.duplicate_of is not None)
    rotated = sum(1 for r in rects if r.rotated)
    print("打包 %d 个条目（独立 %d 个，复用 %d 个，旋转 %d 个）"
          % (len(rects), len(rects) - reused, reused, rotated))
    print("利用率 %.2f%%（唯一占用 %d / 画布 %d 像素）"
          % (unique_area * 100.0 / canvas, unique_area, canvas))
    print("占用包围盒紧凑度 %.2f%%" % (density * 100.0))
    return 0
