#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""GreenNotes 图标的两个备选方案。

B 方案「硬壳笔记本」：白封面 + 绿色书脊 + 右侧纸张厚度 + 垂出的书签丝带
C 方案「卡片笔记」：绿色标题栏的卡片 + 背后叠一张纸，层次感更强

输出到 desktop_app/variants/
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PIL import Image, ImageDraw, ImageFilter  # noqa: E402
import numpy as np  # noqa: E402

from icon_lib import (  # noqa: E402
    S, G_GREEN, PAPER, PAPER_B, LINE_B, px, plate, rounded_mask, poly_mask,
    rrect, tint, mul, sub, drop_shadow, save, vertical_gradient,
)

HERE = os.path.dirname(os.path.abspath(__file__))
OUTDIR = os.path.join(HERE, "variants")


def gradient_rect(box, radius, stops, clip=None):
    """圆角矩形内填充竖向渐变，返回掩膜。"""
    m = rounded_mask(box, radius)
    if clip is not None:
        m = mul(m, clip)
    img = vertical_gradient(stops)
    img.putalpha(m)
    return img, m


# ══════════════════════════════════════════════════════════
# A：折角笔记页
# ══════════════════════════════════════════════════════════
def design_a():
    canvas, _ = plate()

    PAGE = (344, 292, 680, 716)
    PR, FOLD_S = 36, 96
    A = (PAGE[2] - FOLD_S, PAGE[1])
    B = (PAGE[2], PAGE[1] + FOLD_S)

    page_mask = sub(
        rounded_mask(PAGE, PR),
        poly_mask([(A[0], A[1] - 2), (B[0] + 2, B[1] - 2),
                   (PAGE[2] + 2, PAGE[1] - 2)]))

    drop_shadow(canvas, page_mask, offset=(0, 10), blur=14, strength=0.34)
    img, _ = gradient_rect(PAGE, PR, [(0.28, PAPER), (0.70, PAPER_B)])
    img.putalpha(page_mask)
    canvas.alpha_composite(img)

    fold = mul(poly_mask([A, B, (A[0], B[1])]), page_mask)
    canvas.alpha_composite(tint(fold, (214, 235, 222, 255)))

    fold_line = Image.new("L", (S, S), 0)
    ImageDraw.Draw(fold_line).line(
        [(px(A[0]), px(A[1])), (px(B[0]), px(B[1]))],
        fill=110, width=int(px(2.4)))
    fold_line = mul(fold_line.filter(ImageFilter.GaussianBlur(px(1.6))), page_mask)
    canvas.alpha_composite(tint(fold_line, (0, 92, 44, 255)))

    LX, RX = 400, 624
    for x1, y, h, color in (
            (RX, 404, 38, G_GREEN),
            (RX - 24, 500, 26, LINE_B),
            (RX - 58, 556, 26, LINE_B),
            (RX - 102, 612, 26, LINE_B)):
        m = mul(rounded_mask((LX, y, x1, y + h), h / 2), page_mask)
        canvas.alpha_composite(tint(m, color))

    canvas.alpha_composite(tint(fold, (214, 235, 222, 255)))
    return canvas.resize((1024, 1024), Image.LANCZOS)


# ══════════════════════════════════════════════════════════
# B：硬壳笔记本 + 书签丝带
# ══════════════════════════════════════════════════════════
def design_b():
    canvas, _ = plate()

    BOOK = (356, 296, 656, 716)
    RIB_W = 48
    RIB_X = 542
    RIB_BOT = 848

    # 1) 书签丝带（先画，上半段被书体压住，只露出垂下的部分）
    rib = poly_mask([(RIB_X, 560), (RIB_X + RIB_W, 560),
                     (RIB_X + RIB_W, RIB_BOT),
                     (RIB_X + RIB_W / 2, RIB_BOT - 34),
                     (RIB_X, RIB_BOT)], soft=0.4)
    drop_shadow(canvas, rib, offset=(5, 12), blur=12, strength=0.32)
    canvas.alpha_composite(tint(rib, (252, 254, 253, 255)))

    # 2) 右侧纸张厚度（先画，被封面压住左半）
    page_blk = rounded_mask((626, 306, 690, 712), 16)
    drop_shadow(canvas, page_blk, offset=(6, 10), blur=12, strength=0.30)
    canvas.alpha_composite(tint(page_blk, (216, 232, 222, 255)))
    for yy in (316, 328, 340):            # 纸张分层
        rrect(canvas, (638, yy, 684, yy + 4), 2, (243, 249, 245), clip=page_blk)

    # 3) 封面
    cover_mask = rounded_mask(BOOK, 24)
    drop_shadow(canvas, cover_mask, offset=(0, 12), blur=16, strength=0.34)
    img, _ = gradient_rect(BOOK, 24, [(0.0, (255, 255, 255)), (0.75, PAPER_B)])
    canvas.alpha_composite(img)

    # 4) 书脊：左侧薄荷色竖条（比底板亮，才不会被绿底吞掉）
    spine = mul(rounded_mask(BOOK, 24),
                poly_mask([(0, 0), (396, 0), (396, 1024), (0, 1024)]))
    g, _ = gradient_rect((356, 296, 396, 716), 24,
                         [(0.0, (163, 224, 186)), (1.0, (118, 199, 152))])
    g.putalpha(spine)
    canvas.alpha_composite(g)

    # 书脊与封面之间的高光接缝
    rrect(canvas, (394, 302, 398, 710), 2, (255, 255, 255, 235), clip=cover_mask)
    rrect(canvas, (399, 302, 403, 710), 2, (0, 90, 44, 55), clip=cover_mask)

    return canvas.resize((1024, 1024), Image.LANCZOS)


# ══════════════════════════════════════════════════════════
# C：卡片式笔记（绿标题栏）+ 叠页
# ══════════════════════════════════════════════════════════
def design_c():
    canvas, _ = plate()

    CARD = (360, 300, 656, 692)

    # 1) 背后那张纸（左下偏移，做出"多篇笔记"的层次）
    back = rounded_mask((296, 358, 592, 750), 26)
    drop_shadow(canvas, back, offset=(0, 10), blur=14, strength=0.32)
    canvas.alpha_composite(tint(back, (223, 239, 230, 255)))

    # 2) 前面那张卡片
    card_mask = rounded_mask(CARD, 26)
    drop_shadow(canvas, card_mask, offset=(0, 12), blur=15, strength=0.34)
    img, _ = gradient_rect(CARD, 26, [(0.25, (255, 255, 255)), (0.80, PAPER_B)])
    canvas.alpha_composite(img)

    # 3) 顶部深绿标题栏（上圆角、下平直）—— 用比底板更深的绿，才不会糊在一起
    band_mask = mul(card_mask, poly_mask([(0, 0), (1024, 0), (1024, 386), (0, 386)]))
    g, _ = gradient_rect((360, 300, 656, 386), 26,
                         [(0.0, (0, 118, 66)), (1.0, (0, 84, 46))])
    g.putalpha(band_mask)
    canvas.alpha_composite(g)

    # 4) 内容：标题栏里一条白色短线，正文四条递减的灰绿线
    rrect(canvas, (400, 326, 566, 360), 17, (255, 255, 255, 245), clip=band_mask)
    rrect(canvas, (400, 432, 620, 456), 12, LINE_B, clip=card_mask)
    rrect(canvas, (400, 488, 588, 512), 12, LINE_B, clip=card_mask)
    rrect(canvas, (400, 544, 546, 568), 12, LINE_B, clip=card_mask)
    rrect(canvas, (400, 600, 494, 624), 12, LINE_B, clip=card_mask)

    return canvas.resize((1024, 1024), Image.LANCZOS)


if __name__ == "__main__":
    save(design_a(), os.path.join(OUTDIR, "icon-A-page.png"))
    save(design_b(), os.path.join(OUTDIR, "icon-B-notebook.png"))
    save(design_c(), os.path.join(OUTDIR, "icon-C-card.png"))
