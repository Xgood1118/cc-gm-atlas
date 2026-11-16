# -*- coding: utf-8 -*-
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from atlas.core import Rect, load_manifest, pack  # noqa: E402


def test_rect_area():
    r = Rect("a", 8, 4)
    assert r.area() == 32


def test_pack_no_overlap():
    rects = [Rect("a", 40, 40), Rect("b", 40, 40), Rect("c", 40, 40)]
    assert pack(rects, 200, 100, 0) is True
    for i in range(len(rects)):
        for j in range(i + 1, len(rects)):
            a, b = rects[i], rects[j]
            overlap = (a.x < b.x + b.w and b.x < a.x + a.w and
                       a.y < b.y + b.h and b.y < a.y + a.h)
            assert not overlap


def test_pack_too_small():
    rects = [Rect("a", 300, 40)]
    assert pack(rects, 200, 100, 0) is False


def test_manifest_parse():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    path = os.path.join(root, "data", "manifest.txt")
    if not os.path.exists(path):
        return
    items = load_manifest(path)
    assert len(items) >= 10
