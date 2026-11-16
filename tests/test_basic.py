# -*- coding: utf-8 -*-
import hashlib
import json
import os
import struct
import subprocess
import sys
import zlib

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from atlas.core import (  # noqa: E402
    Rect,
    export_json,
    export_png,
    load_manifest,
    pack,
)

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MANIFEST = os.path.join(HERE, "data", "manifest.txt")
WIDTH = HEIGHT = 512
PADDING = 1


def _packed(manifest=MANIFEST, width=WIDTH, height=HEIGHT, padding=PADDING):
    rects = load_manifest(manifest)
    assert pack(rects, width, height, padding) is True
    return rects


def _decode_png(path):
    """极简 PNG 解码器：只接受本模块产出的 8-bit RGB、filter 0 的格式。"""
    with open(path, "rb") as f:
        data = f.read()
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    pos = 8
    idat = b""
    width = height = None
    while pos < len(data):
        length = struct.unpack(">I", data[pos:pos + 4])[0]
        tag = data[pos + 4:pos + 8]
        body = data[pos + 8:pos + 8 + length]
        crc = struct.unpack(">I", data[pos + 8 + length:pos + 12 + length])[0]
        assert crc == zlib.crc32(tag + body) & 0xFFFFFFFF, tag
        if tag == b"IHDR":
            width, height, depth, color_type = struct.unpack(">IIBB", body[:10])
            assert (depth, color_type) == (8, 2)
        elif tag == b"IDAT":
            idat += body
        pos += 12 + length
    raw = zlib.decompress(idat)
    stride = width * 3
    assert len(raw) == (stride + 1) * height
    rows = []
    for y in range(height):
        assert raw[y * (stride + 1)] == 0
        rows.append(raw[y * (stride + 1) + 1:(y + 1) * (stride + 1)])
    return width, height, rows


def _pixel(rows, x, y):
    return tuple(rows[y][x * 3:x * 3 + 3])


# ---------------------------------------------------------------------------
# 排布
# ---------------------------------------------------------------------------

def test_manifest_unchanged_and_parsed():
    with open(MANIFEST, "r", encoding="utf-8") as f:
        lines = [ln for ln in f.read().splitlines() if ln.strip() and not ln.startswith("#")]
    items = load_manifest(MANIFEST)
    assert len(items) == len(lines) == 18
    assert items[0].name == "icon_sword"


def _regions(rects):
    """按实际占用区域去重后的条目列表（复用条目共享同一区域）。"""
    regions = {}
    for r in rects:
        regions.setdefault((r.x, r.y, r.w, r.h), r)
    return list(regions.values())


def test_no_overlap():
    rects = _regions(_packed())
    for i in range(len(rects)):
        for j in range(i + 1, len(rects)):
            a, b = rects[i], rects[j]
            overlap = (a.x < b.x + b.w and b.x < a.x + a.w and
                       a.y < b.y + b.h and b.y < a.y + a.h)
            assert not overlap, (a, b)


def test_padding_respected():
    rects = _regions(_packed())
    for i in range(len(rects)):
        for j in range(i + 1, len(rects)):
            a, b = rects[i], rects[j]
            hgap = max(b.x - (a.x + a.w), a.x - (b.x + b.w))
            vgap = max(b.y - (a.y + a.h), a.y - (b.y + b.h))
            assert hgap >= PADDING or vgap >= PADDING, (a, b)


def test_rotated_stays_in_bounds():
    rects = _packed()
    rotated = [r for r in rects if r.rotated]
    assert rotated, "清单里应当至少有一个条目被旋转以提升利用率"
    for r in rects:
        assert r.x >= 0 and r.y >= 0
        assert r.x + r.w <= WIDTH and r.y + r.h <= HEIGHT
        if r.rotated:
            assert (r.w, r.h) == (r.source_h, r.source_w)


def test_rotation_is_declared_in_json(tmp_path):
    rects = _packed()
    out = tmp_path / "atlas.json"
    data = export_json(rects, (WIDTH, HEIGHT), str(out))
    assert any(f["rotated"] for f in data["frames"])
    for frame in data["frames"]:
        source = next(r for r in rects if r.name == frame["name"])
        assert frame["rotated"] is source.rotated


# ---------------------------------------------------------------------------
# 重复条目
# ---------------------------------------------------------------------------

def test_duplicates_reuse_same_region():
    rects = _packed()
    canonical = {}
    for r in rects:
        if r.duplicate_of is None:
            canonical[r.name] = r
    reused = [r for r in rects if r.duplicate_of is not None]
    assert len(reused) == 7
    for r in reused:
        first = canonical[r.duplicate_of]
        assert (r.x, r.y, r.w, r.h, r.rotated) == (
            first.x, first.y, first.w, first.h, first.rotated
        )


def test_duplicates_still_callable_by_name(tmp_path):
    rects = _packed()
    out = tmp_path / "atlas.json"
    data = export_json(rects, (WIDTH, HEIGHT), str(out))
    frames = {f["name"]: f for f in data["frames"]}
    assert set(frames) == {r.name for r in rects}
    for name in ("icon_shield", "sprite_run2", "sprite_run3", "tile_lava"):
        assert frames[name]["x"] >= 0 and frames[name]["y"] >= 0
        assert frames[name]["duplicate_of"] is not None


# ---------------------------------------------------------------------------
# 利用率
# ---------------------------------------------------------------------------

def _bbox_density(rects):
    uniques = [r for r in rects if r.duplicate_of is None]
    used = sum(r.w * r.h for r in uniques)
    x0 = min(r.x for r in uniques)
    y0 = min(r.y for r in uniques)
    x1 = max(r.x + r.w for r in uniques)
    y1 = max(r.y + r.h for r in uniques)
    return used / ((x1 - x0) * (y1 - y0))


def test_utilisation_lower_bound():
    rects = _packed()
    assert _bbox_density(rects) >= 0.85


# ---------------------------------------------------------------------------
# PNG
# ---------------------------------------------------------------------------

def test_png_size_and_pixel_alignment(tmp_path):
    rects = _packed()
    out = tmp_path / "atlas.png"
    export_png(rects, (WIDTH, HEIGHT), str(out))
    width, height, rows = _decode_png(str(out))
    assert (width, height) == (WIDTH, HEIGHT)

    colors = {}
    for r in rects:
        if r.duplicate_of is None:
            colors[r.name] = _pixel(rows, r.x, r.y)
    # 每个条目的填充色互不相同。
    assert len(set(colors.values())) == len(colors)
    for r in rects:
        if r.duplicate_of is not None:
            colors[r.name] = colors[r.duplicate_of]
        color = colors[r.name]
        for y in (r.y, r.y + r.h - 1):
            for x in (r.x, r.x + r.w - 1):
                assert _pixel(rows, x, y) == color, (r.name, x, y)
        # 区域外紧邻的一像素不是该条目的颜色。
        if r.x + r.w < WIDTH:
            assert _pixel(rows, r.x + r.w, r.y) != color


def test_png_deterministic(tmp_path):
    digests = []
    for i in range(2):
        rects = _packed()
        out = tmp_path / ("run%d.png" % i)
        export_png(rects, (WIDTH, HEIGHT), str(out))
        with open(str(out), "rb") as f:
            digests.append(hashlib.sha256(f.read()).hexdigest())
    assert digests[0] == digests[1]


def test_json_deterministic(tmp_path):
    outputs = []
    for i in range(2):
        rects = _packed()
        out = tmp_path / ("run%d.json" % i)
        export_json(rects, (WIDTH, HEIGHT), str(out))
        with open(str(out), "rb") as f:
            outputs.append(f.read())
    assert outputs[0] == outputs[1]


# ---------------------------------------------------------------------------
# 边界情况与命令行
# ---------------------------------------------------------------------------

def test_oversized_item_does_not_raise(tmp_path):
    rects = [Rect("huge", 600, 40)]
    assert pack(rects, 512, 512, 1) is False
    assert pack.last_failure.name == "huge"


def test_impossible_fit_reports_specific_item(tmp_path):
    # 两条尺寸不同、单条都放得下，但合起来放不进 512x512。
    rects = [Rect("a", 300, 300), Rect("b", 300, 299)]
    assert pack(rects, 512, 512, 1) is False
    assert pack.last_failure.name in ("a", "b")


def test_cli_nonzero_exit_on_failure(tmp_path):
    manifest = tmp_path / "small.txt"
    manifest.write_text("a 300 300\nb 300 299\n", encoding="utf-8")
    result = subprocess.run(
        [sys.executable, "-m", "atlas.cli", str(manifest),
         "--width", "512", "--height", "512", "--padding", "1",
         "--out-png", str(tmp_path / "o.png"), "--out-json", str(tmp_path / "o.json")],
        cwd=HERE, capture_output=True, text=True,
    )
    assert result.returncode != 0
    assert ("a" in result.stderr) or ("b" in result.stderr)


def test_cli_oversized_item_no_traceback(tmp_path):
    manifest = tmp_path / "one.txt"
    manifest.write_text("huge 600 40\n", encoding="utf-8")
    result = subprocess.run(
        [sys.executable, "-m", "atlas.cli", str(manifest),
         "--width", "512", "--height", "512",
         "--out-png", str(tmp_path / "o.png"), "--out-json", str(tmp_path / "o.json")],
        cwd=HERE, capture_output=True, text=True,
    )
    assert result.returncode != 0
    assert "huge" in result.stderr
    assert "Traceback" not in result.stderr


def test_cli_end_to_end(tmp_path):
    png = tmp_path / "atlas.png"
    js = tmp_path / "atlas.json"
    result = subprocess.run(
        [sys.executable, "-m", "atlas.cli", MANIFEST,
         "--width", "512", "--height", "512", "--padding", "1",
         "--out-png", str(png), "--out-json", str(js)],
        cwd=HERE, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    assert png.exists() and js.exists()
    with open(str(js), encoding="utf-8") as f:
        data = json.load(f)
    assert data["size"] == [512, 512]
    assert len(data["frames"]) == 18
