"""缺口定位算法（第4版）：手写掩膜 NCC（零均值归一化相关）。

原理：
    缺口 = 原图内容 × 变暗系数（半透明黑色遮罩），拼图块内容 = 原图内容。
    零均值归一化相关对“整体变亮/变暗”不敏感，因此正确位置的 NCC 应接近 1。

    另外用 1px 步长在 x 方向穷举、y 方向 ±4px 微调，保证精确到像素。

输出：round*_zoom4.png（3倍放大 + 绿色轮廓叠加）与控制台得分表。
"""

import glob
import os

import cv2
import numpy as np

base_dir = os.path.dirname(os.path.abspath(__file__))
dump_dir = os.path.join(base_dir, "captcha_debug")


def load_image(path):
    img = cv2.imread(path, cv2.IMREAD_UNCHANGED)
    if img is None:
        raise RuntimeError("读不到图片: " + path)
    if img.ndim == 2:
        img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGRA)
    if img.shape[2] == 3:
        img = cv2.cvtColor(img, cv2.COLOR_BGR2BGRA)
    return img


def get_piece(piece_bgra):
    alpha = piece_bgra[:, :, 3]
    mask = (alpha > 10).astype(np.uint8) * 255
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
    x, y, w, h = cv2.boundingRect(mask)
    content = piece_bgra[y:y + h, x:x + w, :3]
    return content, mask[y:y + h, x:x + w], (x, y, w, h)


def find_gap_ncc(bg_bgr, piece_content, piece_mask, py, ph, y_tolerance=4):
    """穷举 x、微调 y，用零均值 NCC 打分，返回 (最佳x, 最佳y, 得分, 前几名)"""
    bg_gray = cv2.cvtColor(bg_bgr, cv2.COLOR_BGR2GRAY).astype(np.float32)
    piece_gray = cv2.cvtColor(piece_content, cv2.COLOR_BGR2GRAY).astype(np.float32)

    mask_bool = piece_mask > 0
    tmpl = piece_gray[mask_bool]
    tmpl = tmpl - tmpl.mean()
    tmpl_norm = float(np.sqrt((tmpl ** 2).sum()))
    if tmpl_norm == 0:
        return None, None, 0.0, []

    results = []
    height, width = bg_gray.shape
    pw, ph2 = piece_mask.shape[1], piece_mask.shape[0]
    for y in range(max(0, py - y_tolerance), min(height - ph2 + 1, py + y_tolerance + 1)):
        for x in range(0, width - pw + 1):
            patch = bg_gray[y:y + ph2, x:x + pw]
            vals = patch[mask_bool]
            vals = vals - vals.mean()
            denom = float(np.sqrt((vals ** 2).sum())) * tmpl_norm
            if denom == 0:
                continue
            score = float((vals * tmpl).sum() / denom)
            results.append((score, x, y))

    if not results:
        return None, None, 0.0, []

    results.sort(key=lambda item: -item[0])
    best_score, best_x, best_y = results[0]
    return best_x, best_y, best_score, results[:5]


def main():
    samples = sorted(glob.glob(os.path.join(dump_dir, "round*_canvas0.png")))
    for bg_path in samples:
        piece_path = bg_path.replace("_canvas0.png", "_canvas1.png")
        if not os.path.exists(piece_path):
            continue
        name = os.path.basename(bg_path).replace("_canvas0.png", "")

        bg_bgra = load_image(bg_path)
        piece_bgra = load_image(piece_path)
        piece_content, piece_mask, (px, py, pw, ph) = get_piece(piece_bgra)

        gap_x, gap_y, score, top = find_gap_ncc(bg_bgra[:, :, :3], piece_content, piece_mask, py, ph)

        print("=== %s ===" % name)
        print("  拼图块起点: x=%d y=%d 尺寸=%dx%d" % (px, py, pw, ph))
        print("  最佳匹配  : x=%d y=%d NCC=%.5f  → 需要拖动 %.0f 像素" % (gap_x, gap_y, score, gap_x - px))
        for s, x, y in top[1:4]:
            print("     次优: x=%d y=%d NCC=%.5f" % (x, y, s))

        # 输出放大验证图：绿色轮廓叠加在匹配位置上
        canvas = bg_bgra[:, :, :3].copy()
        outline = cv2.Canny(piece_mask, 60, 160)
        ys, xs = np.where(outline > 0)
        for yy, xx in zip(ys, xs):
            ty, tx = gap_y + yy, gap_x + xx
            if 0 <= ty < canvas.shape[0] and 0 <= tx < canvas.shape[1]:
                canvas[ty, tx] = (0, 255, 0)
        x0 = max(0, gap_x - 30)
        x1 = min(canvas.shape[1], gap_x + pw + 30)
        y0 = max(0, gap_y - 25)
        y1 = min(canvas.shape[0], gap_y + ph + 25)
        zoom = cv2.resize(canvas[y0:y1, x0:x1], None, fx=3, fy=3, interpolation=cv2.INTER_NEAREST)
        cv2.imwrite(os.path.join(dump_dir, name + "_zoom4.png"), zoom)
        print()


if __name__ == "__main__":
    main()
