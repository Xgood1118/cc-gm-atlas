# -*- coding: utf-8 -*-
import os

HERE = os.path.dirname(os.path.abspath(__file__))


def main():
    lines = [
        "# name width height",
        "icon_sword 64 64",
        "icon_shield 64 64",
        "icon_potion 32 48",
        "icon_scroll 48 64",
        "tex_grass 96 96",
        "tex_stone 96 96",
        "ui_panel 160 64",
        "ui_button 72 24",
        "ui_bar 128 16",
        "sprite_run1 48 72",
        "sprite_run2 48 72",
        "sprite_run3 48 72",
        "fx_spark 24 24",
        "fx_smoke 32 32",
        "fx_glow 64 64",
        "tile_water 64 64",
        "tile_lava 64 64",
        "font_digit 12 16",
    ]
    os.makedirs(os.path.join(HERE, "data"), exist_ok=True)
    path = os.path.join(HERE, "data", "manifest.txt")
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines) + "\n")
    print("已生成", path, len(lines) - 1, "个条目")


if __name__ == "__main__":
    main()
