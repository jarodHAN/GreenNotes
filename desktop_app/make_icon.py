#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成本地 App 图标。

用法：
    python make_icon.py                # 默认方案 B（硬壳笔记本）
    python make_icon.py --variant A    # 折角笔记
    python make_icon.py --variant C    # 卡片笔记

也可以用环境变量 ICON_VARIANT=B（build.sh 支持）。
输出 desktop_app/AppIcon.png 与 app/static/icon-{64,128}.png。

绘制实现在 icon_variants.py（三种方案共用 icon_lib.py 的 squircle 底板）。
"""
import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

try:
    from PIL import Image
except ImportError:
    print("需要 Pillow / numpy，请用带依赖的解释器运行", file=sys.stderr)
    sys.exit(2)

import icon_variants as V  # noqa: E402

VARIANTS = {
    "A": ("折角笔记", V.design_a),
    "B": ("硬壳笔记本", V.design_b),
    "C": ("卡片笔记", V.design_c),
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--variant", default=os.environ.get("ICON_VARIANT", "B"))
    args = ap.parse_args()

    key = args.variant.strip().upper()
    if key not in VARIANTS:
        print(f"未知方案 {key}，可选 {', '.join(VARIANTS)}", file=sys.stderr)
        return 2

    label, fn = VARIANTS[key]
    icon = fn()

    master = os.path.join(HERE, "AppIcon.png")
    icon.save(master, "PNG")

    static = os.path.abspath(os.path.join(HERE, "..", "app", "static"))
    os.makedirs(static, exist_ok=True)
    icon.resize((128, 128), Image.LANCZOS).save(
        os.path.join(static, "icon-128.png"), "PNG")
    icon.resize((64, 64), Image.LANCZOS).save(
        os.path.join(static, "icon-64.png"), "PNG")

    print(f"图标方案 {key}（{label}）→ {master} {os.path.getsize(master)} bytes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
