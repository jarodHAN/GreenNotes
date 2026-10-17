#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""图标绘制的公共底座。

所有图标共用同一套「macOS squircle 底板 + 渐变 + 光照 + 投影」，
变体只需要在自己的画布上画前景元素。
"""
import os
import sys

try:
    from PIL import Image, ImageDraw, ImageFilter
    import numpy as np
except ImportError:
    print("需要 Pillow 与 numpy：pip install pillow numpy", file=sys.stderr)
    sys.exit(2)

OUT = 1024
SS = 3
S = OUT * SS

# 配色
G_GREEN = (0, 168, 45)
G_TOP = (58, 206, 122)
G_MID = (0, 168, 45)
G_BOT = (0, 116, 54)
PAPER = (253, 255, 254)
PAPER_B = (236, 245, 240)
FOLD = (214, 235, 222)
LINE_T = G_GREEN
LINE_B = (150, 178, 161)
PLATE = (100, 100, 924, 924)


def px(v):
    return v * SS


def vertical_gradient(stops):
    pos = [s[0] for s in stops]
    y = np.linspace(0.0, 1.0, S)
    arr = np.zeros((S, 3), dtype=np.float32)
    for i in range(3):
        arr[:, i] = np.interp(y, pos, [c[1][i] for c in stops])
    col = arr.astype(np.uint8)[:, None, :].repeat(S, axis=1)
    return Image.fromarray(col, "RGB").convert("RGBA")


def squircle_alpha(box, n=4.6):
    x0, y0, x1, y1 = [px(v) for v in box]
    cx, cy = (x0 + x1) / 2.0, (y0 + y1) / 2.0
    a, b = (x1 - x0) / 2.0, (y1 - y0) / 2.0
    yy, xx = np.mgrid[0:S, 0:S]
    X = (xx.astype(np.float32) - cx) / a
    Y = (yy.astype(np.float32) - cy) / b
    f = np.abs(X) ** n + np.abs(Y) ** n
    alpha = np.clip((1.0 - f) / 0.012 + 0.5, 0.0, 1.0)
    return Image.fromarray((alpha * 255).astype(np.uint8), "L")


def rounded_mask(box, radius):
    m = Image.new("L", (S, S), 0)
    ImageDraw.Draw(m).rounded_rectangle([px(v) for v in box],
                                        radius=px(radius), fill=255)
    return m


def radial_highlight(center, radius, strength):
    yy, xx = np.mgrid[0:S, 0:S]
    cx, cy = px(center[0]), px(center[1])
    d = np.sqrt((xx - cx) ** 2 + (yy - cy) ** 2) / px(radius)
    a = np.clip(1.0 - d, 0, 1) ** 2.2 * strength
    return Image.fromarray((a * 255).astype(np.uint8), "L")


def tint(alpha_img, color):
    if len(color) == 3:
        color = color + (255,)
    layer = Image.new("RGBA", (S, S), tuple(color))
    layer.putalpha(alpha_img)
    return layer


def mul(a, b):
    """两张 L 掩膜相乘（交集）。"""
    return Image.fromarray(
        (np.asarray(a, dtype=np.int16) * np.asarray(b, dtype=np.int16)
         // 255).astype(np.uint8), "L")


def sub(a, b):
    """掩膜相减（a 去掉 b）。"""
    return Image.fromarray(
        (np.asarray(a, dtype=np.int16) - np.asarray(b, dtype=np.int16)
         ).clip(0, 255).astype(np.uint8), "L")


def drop_shadow(canvas, mask, offset=(0, 12), blur=16, color=(14, 48, 30),
                strength=0.32):
    a = mask.filter(ImageFilter.GaussianBlur(px(blur)))
    a = a.point(lambda v: int(v * strength))
    layer = Image.new("RGBA", (S, S), color + (255,))
    layer.putalpha(a)
    canvas.alpha_composite(layer, (px(offset[0]), px(offset[1])))


def plate():
    """底板：squircle + 渐变 + 柔光 + 压暗 + 内描边。"""
    canvas = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    body = squircle_alpha(PLATE, n=4.6)

    drop_shadow(canvas, body, offset=(0, 14), blur=16, strength=0.30)

    grad = vertical_gradient([(0.00, G_TOP), (0.46, G_MID), (1.00, G_BOT)])
    grad.putalpha(body)
    canvas.alpha_composite(grad)

    gloss = mul(radial_highlight((360, 250), 620, 0.20), body)
    canvas.alpha_composite(tint(gloss, (255, 255, 255, 255)))

    depth = radial_highlight((512, 1080), 700, 0.22)
    canvas.alpha_composite(tint(depth, (0, 48, 24, 255)))

    eroded = body.filter(ImageFilter.MinFilter(7))
    edge = Image.fromarray(
        (np.asarray(body, dtype=np.int16) - np.asarray(eroded, dtype=np.int16)
         ).clip(0, 255).astype(np.uint8), "L").point(lambda v: int(v * 0.30))
    canvas.alpha_composite(tint(edge, (255, 255, 255, 255)))
    return canvas, body


def poly_mask(points, soft=0):
    m = Image.new("L", (S, S), 0)
    ImageDraw.Draw(m).polygon([(px(x), px(y)) for x, y in points], fill=255)
    if soft:
        m = m.filter(ImageFilter.GaussianBlur(px(soft)))
    return m


def rrect(canvas, box, radius, color, clip=None):
    m = rounded_mask(box, radius)
    if clip is not None:
        m = mul(m, clip)
    canvas.alpha_composite(tint(m, color))
    return m


def save(icon, path):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    icon.save(path, "PNG")
    print("wrote", path, os.path.getsize(path), "bytes")
