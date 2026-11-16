# -*- coding: utf-8 -*-
"""精灵图集打包骨架。

当前策略是最朴素的横向排布：从左到右依次摆放，一行放不下就换行，
不旋转、不去重、不留边距。
"""

import json


class Rect(object):
    def __init__(self, name, w, h):
        self.name = name
        self.w = int(w)
        self.h = int(h)
        self.x = 0
        self.y = 0
        self.rotated = False

    def area(self):
        return self.w * self.h

    def __repr__(self):
        return "Rect(%s, %dx%d @ %d,%d)" % (self.name, self.w, self.h, self.x, self.y)


def load_manifest(path):
    """读取清单。每行 name width height。"""
    items = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split()
            items.append(Rect(parts[0], int(parts[1]), int(parts[2])))
    return items


def pack(rects, atlas_width, atlas_height, padding=0):
    """横向排列，越界就换行。返回是否全部放下。"""
    x = 0
    y = 0
    row_h = 0
    for r in rects:
        if r.w + 2 * padding > atlas_width or r.h + 2 * padding > atlas_height:
            return False
        if x + r.w + 2 * padding > atlas_width:
            x = 0
            y += row_h
            row_h = 0
        if y + r.h + 2 * padding > atlas_height:
            return False
        r.x = x + padding
        r.y = y + padding
        r.rotated = False
        x += r.w + 2 * padding
        if r.h > row_h:
            row_h = r.h + 2 * padding
    return True


def export_json(rects, atlas_size, path):
    data = {
        "size": list(atlas_size),
        "frames": [
            {"name": r.name, "x": r.x, "y": r.y, "w": r.w, "h": r.h, "rotated": r.rotated}
            for r in rects
        ],
    }
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return data


def export_png(rects, atlas_size, path):
    """把图集写成 PNG。"""
    raise NotImplementedError("图集 PNG 输出尚未实现")


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(prog="atlas")
    ap.add_argument("manifest")
    ap.add_argument("--width", type=int, default=512)
    ap.add_argument("--height", type=int, default=512)
    ap.add_argument("--padding", type=int, default=1)
    ap.add_argument("--out-png", default="atlas.png")
    ap.add_argument("--out-json", default="atlas.json")
    args = ap.parse_args(argv)
    rects = load_manifest(args.manifest)
    ok = pack(rects, args.width, args.height, args.padding)
    if not ok:
        print("放不下")
        return 1
    export_png(rects, (args.width, args.height), args.out_png)
    export_json(rects, (args.width, args.height), args.out_json)
    used = sum(r.area() for r in rects)
    total = args.width * args.height
    print("打包 %d 个，利用率 %.2f%%" % (len(rects), used * 100.0 / total))
    return 0
