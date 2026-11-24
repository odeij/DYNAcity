"""Small Pillow drawing helpers for evidence images (no matplotlib in the project's environment)."""
from __future__ import annotations

import numpy as np
from PIL import Image, ImageDraw, ImageFont

INK = (28, 32, 38)
MUTED = (96, 104, 116)
PAPER = (255, 255, 255)
GRID = (222, 226, 232)


def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for name in (("arialbd.ttf", "calibrib.ttf") if bold else ("arial.ttf", "calibri.ttf")) + ("DejaVuSans.ttf",):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def diverging(values: np.ndarray, limit: float) -> np.ndarray:
    """RGB for `values` clipped to +-limit: blue (negative), white (0), red (positive). NaN -> (0, 0, 0, masked by caller)."""
    t = np.clip(np.nan_to_num(values, nan=0.0) / limit, -1, 1)
    neg, pos = np.clip(-t, 0, 1), np.clip(t, 0, 1)
    r = 255 - 205 * neg - 35 * pos
    g = 255 - 150 * neg - 205 * pos
    b = 255 - 40 * neg - 225 * pos
    return np.stack([r, g, b], axis=-1).astype(np.uint8)


def sequential(values: np.ndarray, low: float, high: float) -> np.ndarray:
    """Pale yellow to dark green for canopy height."""
    t = np.clip((np.nan_to_num(values, nan=low) - low) / (high - low), 0, 1)
    stops = np.array([[196, 228, 110], [120, 196, 90], [40, 150, 80], [14, 100, 64], [4, 50, 38]], float)
    x = t * (len(stops) - 1)
    i = np.minimum(x.astype(int), len(stops) - 2)
    f = (x - i)[..., None]
    return (stops[i] * (1 - f) + stops[i + 1] * f).astype(np.uint8)


def text(draw: ImageDraw.ImageDraw, xy, s: str, size: int = 14, fill=INK, bold: bool = False, anchor: str = "la") -> None:
    draw.text(xy, s, font=font(size, bold), fill=fill, anchor=anchor)


def scale_bar(draw: ImageDraw.ImageDraw, x: int, y: int, metres_per_px: float, length_m: float, label: str | None = None) -> None:
    px = length_m / metres_per_px
    draw.rectangle([x, y, x + px, y + 5], fill=INK)
    draw.rectangle([x + px / 2, y, x + px, y + 5], fill=PAPER, outline=INK)
    text(draw, (x, y - 4), label or f"{length_m:g} m", 13, anchor="ls")


def colorbar(draw: ImageDraw.ImageDraw, image: Image.Image, x: int, y: int, w: int, h: int, colors, low: float, high: float,
             label: str, ticks: list[float]) -> None:
    """Horizontal colour bar; `colors(values)` maps a 1D array of values to RGB."""
    values = np.linspace(low, high, w)
    strip = colors(values)[None, :, :].repeat(h, axis=0)
    image.paste(Image.fromarray(strip), (x, y))
    draw.rectangle([x, y, x + w, y + h], outline=INK)
    for t in ticks:
        tx = x + (t - low) / (high - low) * w
        draw.line([tx, y + h, tx, y + h + 4], fill=INK)
        text(draw, (tx, y + h + 6), f"{t:g}", 12, anchor="ma")
    text(draw, (x, y - 6), label, 13, anchor="ls")


def line_chart(image: Image.Image, box, series: list[dict], xlim, ylim, xlabel: str, ylabel: str, title: str,
               vlines: list[tuple[float, str]] = (), xticks=None, yticks=None) -> None:
    """Lines in `box` = (x0, y0, x1, y1) pixels. Each series: {x, y, color, width, label, dash}."""
    draw = ImageDraw.Draw(image)
    x0, y0, x1, y1 = box
    sx = lambda v: x0 + (v - xlim[0]) / (xlim[1] - xlim[0]) * (x1 - x0)
    sy = lambda v: y1 - (v - ylim[0]) / (ylim[1] - ylim[0]) * (y1 - y0)
    for t in (yticks or []):
        draw.line([x0, sy(t), x1, sy(t)], fill=GRID)
        text(draw, (x0 - 8, sy(t)), f"{t:g}", 12, MUTED, anchor="rm")
    for t in (xticks or []):
        draw.line([sx(t), y0, sx(t), y1], fill=GRID)
        text(draw, (sx(t), y1 + 6), f"{t:g}", 12, MUTED, anchor="ma")
    draw.rectangle(box, outline=INK)
    for v, label in vlines:
        draw.line([sx(v), y0, sx(v), y1], fill=(180, 60, 60), width=2)
        text(draw, (sx(v) + 5, y0 + 4), label, 12, (150, 40, 40))
    for s in series:
        pts = [(sx(a), sy(b)) for a, b in zip(s["x"], s["y"]) if np.isfinite(b)]
        if s.get("dash") and len(pts) > 1:
            for k in range(0, len(pts) - 1, 2):
                draw.line([pts[k], pts[k + 1]], fill=s["color"], width=s.get("width", 2))
        elif len(pts) > 1:
            draw.line(pts, fill=s["color"], width=s.get("width", 2))
    text(draw, (x0, y0 - 24), title, 15, bold=True)
    text(draw, ((x0 + x1) / 2, y1 + 24), xlabel, 13, MUTED, anchor="ma")
    text(draw, (x0 - 48, (y0 + y1) / 2), ylabel, 13, MUTED, anchor="mm")
    lx, ly = x1 - 215, y0 + 10
    draw.rectangle([lx - 8, ly - 6, x1 - 6, ly + 21 * len(series) - 2], fill=(255, 255, 255), outline=GRID)
    for k, s in enumerate(series):
        yy = ly + 21 * k + 7
        draw.line([lx, yy, lx + 26, yy], fill=s["color"], width=s.get("width", 2))
        text(draw, (lx + 34, yy), s["label"], 12, anchor="lm")
