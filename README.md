# 精灵图集打包

骨架版本按行排布，能算出坐标并输出 JSON，但产不出图集图片。

## 已知缺口

- 只做横向排列，一行放不下就换行，长条和小方块混排时图集下半部分大量空置。
- 不支持旋转，高瘦的条目只能横着占位置。
- 完全相同的尺寸不去重，`icon_sword` 与 `icon_shield` 在 512 宽的图集里各占一格。
- 边距只做在坐标上，`export_png` 没有实现，图集边缘是否存在渗色无从验证。
- `export_png` 抛 NotImplementedError。

## 运行

```
python gen_samples.py
python -m atlas.cli data/manifest.txt --width 512 --height 512 --padding 1
python -m pytest tests -q
```

清单里 18 个条目，尺寸从 12x16 到 160x64 不等，包含三张同尺寸的跑步序列帧。
