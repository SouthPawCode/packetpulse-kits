"""The Packet Pulse style. Every color, size and font decision for rendered assets lives here."""

from __future__ import annotations

from functools import lru_cache
from importlib import resources
from typing import Any

import matplotlib
import numpy as np
from PIL import Image

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib import font_manager  # noqa: E402
from matplotlib.patches import FancyBboxPatch, Rectangle  # noqa: E402

# --- canvas --------------------------------------------------------------------
W, H, DPI = 1920, 1080, 100
HANDLE = "@packetpulsedev"
BRAND_DIR = "brand"  # render/brand/: logo.png, logo_dark.png, icon.png (shipped as package data)

# --- palette -------------------------------------------------------------------
# Sampled from the Packet Pulse logo: charcoal background, electric cyan accent, white. The brand images are keyed
# out of a flat charcoal background and have slight edge fringing, so they are only ever composited on dark fills.
CARD = "#282828"  # card background: the logo's charcoal
PANEL = "#1F1F1F"  # darker panels: chart plot areas, table rows, lower-third preview plate
PANEL_HI = "#333333"  # raised chips for states without a verdict (pending, not run)
CARD_GLASS = "#282828EE"  # card color at 93% opacity: lower-third plate over video
GRID = "#3A3A3A"  # separators, grid lines, axis spines
ACCENT = "#01E8FC"  # brand accent: electric cyan
ACCENT_BAND = "#01E8FC2E"  # the accent at 18% opacity: baseline-row highlight
TEXT = "#FEFEFE"
MUTED = "#B8BEC4"  # secondary text
PASS = "#3DDC97"
AMBER = "#F5B342"  # partial
RED = "#FF5C5C"  # fail
INK = "#141414"  # text on light or bright fills

MODEL_COLORS = [ACCENT, AMBER, "#B39DFF", "#FF7A7A", "#6FA8FF", "#B6D86A", "#D98BC2", "#7FD1E8"]

FONT_CANDIDATES = ["IBM Plex Sans", "IBM Plex Sans Condensed"]
FALLBACK_FONT = "DejaVu Sans"
MONO_CANDIDATES = ["IBM Plex Mono"]
FALLBACK_MONO = "DejaVu Sans Mono"


def font_family() -> str:
    names = {f.name for f in font_manager.fontManager.ttflist}
    for c in FONT_CANDIDATES:
        if c in names:
            return c
    return FALLBACK_FONT


def mono_family() -> str:
    names = {f.name for f in font_manager.fontManager.ttflist}
    for c in MONO_CANDIDATES:
        if c in names:
            return c
    return FALLBACK_MONO


def setup() -> None:
    plt.rcParams.update(
        {
            "font.family": [font_family(), FALLBACK_FONT],
            "svg.fonttype": "path",  # text becomes outlines: the SVG looks the same everywhere
            "axes.unicode_minus": False,
            "text.color": TEXT,
        }
    )


def model_color(results: dict[str, Any], name: str) -> str:
    models = results["models"]
    order = [m["name"] for m in models]
    base = results.get("baseline")
    if base and name == base:
        return MODEL_COLORS[0]
    others = [n for n in order if n != base]
    i = others.index(name) if name in others else 0
    return MODEL_COLORS[(i + (1 if base else 0)) % len(MODEL_COLORS)]


def new_card(transparent: bool = False):
    """A 1920x1080 figure with one pixel-coordinate axes (origin top-left) covering it."""
    setup()
    fig = plt.figure(figsize=(W / DPI, H / DPI), dpi=DPI)
    if transparent:
        fig.patch.set_alpha(0.0)
    else:
        fig.patch.set_facecolor(CARD)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, W)
    ax.set_ylim(H, 0)
    ax.axis("off")
    ax.patch.set_alpha(0.0)
    return fig, ax


def header(ax, title: str, subtitle: str | None = None) -> None:
    ax.add_patch(Rectangle((0, 0), 14, H, color=ACCENT, lw=0))
    ax.text(80, 88, title, fontsize=38, fontweight="bold", color=TEXT, va="center")
    if subtitle:
        ax.text(80, 146, subtitle, fontsize=19, color=ACCENT, va="center")


def footer(ax, hardware: str, transparent: bool = False) -> None:
    """Hardware line bottom-left and the corner mark bottom-right, on every card."""
    ax.text(80, H - 42, hardware, fontsize=16, color=MUTED, va="center")
    corner_mark(ax)


# --- brand images ---------------------------------------------------------------

CORNER_ICON_H = 110  # px tall, bottom-right of every card
CORNER_BOTTOM = 18  # px between the icon and the bottom edge


@lru_cache(maxsize=None)
def brand_image(name: str) -> Image.Image:
    """A brand image from the package (render/brand/<name>.png) as RGBA, read through importlib.resources so it
    also loads from an installed wheel. Fully transparent margins are trimmed (the supplied PNGs carry empty
    padding below the artwork), so sizes and positions refer to the visible mark."""
    res = resources.files(__package__).joinpath(BRAND_DIR, f"{name}.png")
    with resources.as_file(res) as path, Image.open(path) as im:
        rgba = im.convert("RGBA")
    box = rgba.getchannel("A").point(lambda a: 255 if a > 0 else 0).getbbox()
    return rgba.crop(box) if box else rgba


@lru_cache(maxsize=None)
def _sized(name: str, w: int, h: int) -> np.ndarray:
    return np.asarray(brand_image(name).resize((w, h), Image.LANCZOS))


def paste_brand(ax, name: str, x: float, y: float, *, width: int | None = None, height: int | None = None,
                align: str = "left", zorder: float = 5) -> tuple[int, int]:
    """Paste a brand image with its alpha at canvas pixel (x, y) (top-left, or top-centre with align="center"),
    scaled to `width` or to `height` (aspect kept). Returns the pasted (width, height) in canvas pixels."""
    src = brand_image(name)
    if width is None and height is None:
        width = src.width
    if width is None:
        width = round(src.width * height / src.height)
    if height is None:
        height = round(src.height * width / src.width)
    left = round(x - width / 2) if align == "center" else round(x)
    top = round(y)
    ax.imshow(_sized(name, width, height), extent=(left, left + width, top + height, top), aspect="auto",
              interpolation="none", zorder=zorder)
    ax.set_xlim(0, W)  # imshow rewrites the limits
    ax.set_ylim(H, 0)
    return width, height


def corner_mark(ax) -> None:
    """The icon, then the handle in the accent, bottom-right. Drawn on every card."""
    fig = ax.figure
    right = W - 60
    handle_w = text_width(fig, ax, HANDLE, fontsize=20, fontweight="bold")
    icon_w = round(brand_image("icon").width * CORNER_ICON_H / brand_image("icon").height)
    top = H - CORNER_BOTTOM - CORNER_ICON_H
    paste_brand(ax, "icon", right - handle_w - 16 - icon_w, top, height=CORNER_ICON_H)
    ax.text(right, top + CORNER_ICON_H / 2, HANDLE, fontsize=20, color=ACCENT, va="center", ha="right", fontweight="bold")


def chip(ax, x: float, y: float, w: float, h: float, text: str, fill: str, fg: str = INK, size: int = 18) -> None:
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0,rounding_size=10", fc=fill, ec="none"))
    ax.text(x + w / 2, y + h / 2, text, fontsize=size, color=fg, ha="center", va="center", fontweight="bold")


def panel(ax, x: float, y: float, w: float, h: float, fill: str = PANEL, edge: str | None = None) -> None:
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0,rounding_size=12", fc=fill, ec=edge or "none", lw=2 if edge else 0))


def chart_axes(fig, rect: tuple[float, float, float, float]):
    """Inner axes in figure fractions, styled for the dark card with visible scales."""
    ax = fig.add_axes(rect)
    ax.set_facecolor(PANEL)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(GRID)
    ax.tick_params(colors=MUTED, labelsize=16, length=6, width=1.2)
    ax.grid(True, color=GRID, lw=1.0, alpha=0.9)
    ax.set_axisbelow(True)
    return ax


def save(fig, base: Any, *, png: bool = True, svg: bool = True, transparent: bool = False) -> list[str]:
    """Save base.png (1920x1080) and base.svg; returns the formats written."""
    written: list[str] = []
    kw: dict[str, Any] = {"transparent": True} if transparent else {"facecolor": fig.get_facecolor()}
    if png:
        fig.savefig(f"{base}.png", dpi=DPI, format="png", **kw)
        written.append("png")
    if svg:
        fig.savefig(f"{base}.svg", format="svg", **kw)
        written.append("svg")
    plt.close(fig)
    return written


# --- text runs and vitals strip (pre-run cards) ---------------------


def text_width(fig, ax, s: str, **kw: Any) -> float:
    """Rendered width in canvas pixels of `s` drawn with these text properties."""
    t = ax.text(0, 0, s, **kw)
    w = t.get_window_extent(renderer=fig.canvas.get_renderer()).width
    t.remove()
    return w * (W / (fig.get_figwidth() * fig.dpi))


def runs(fig, ax, x: float, y: float, parts: list[tuple[str, str]], *, align: str = "center", **kw: Any) -> list[Any]:
    """Draw consecutive text runs [(text, color)] on one baseline, centred or left-aligned at x."""
    widths = [text_width(fig, ax, t, **kw) for t, _ in parts]
    cx = x - sum(widths) / 2 if align == "center" else x
    drawn = []
    for (t, color), w in zip(parts, widths):
        drawn.append(ax.text(cx, y, t, color=color, ha="left", va="center", **kw))
        cx += w
    return drawn


def vitals_segments(hardware: str) -> list[str]:
    """The hardware line as monitor segments: split on ' · ', and a leading 'GPU name N GB'
    becomes 'GPU name' and 'N GB VRAM'."""
    import re

    segs = [p.strip() for p in hardware.split("·") if p.strip()]
    if segs:
        m = re.match(r"^(.*\S)\s+(\d+(?:\.\d+)?)\s*GB$", segs[0], re.I)
        if m and "ram" not in segs[0].lower():
            segs[0:1] = [m.group(1), f"{m.group(2)} GB VRAM"]
    return segs


def vitals_strip(fig, ax, hardware: str, y: float, *, size: int = 30, max_px: float = 1760.0) -> list[str]:
    """Patient-monitor readout of the hardware line: monospace, accent values, muted separators.
    Shrinks (down to 16 pt) until the strip fits max_px; returns the segments drawn."""
    segs = vitals_segments(hardware)
    parts: list[tuple[str, str]] = []
    for i, seg in enumerate(segs):
        if i:
            parts.append(("  ·  ", MUTED))
        parts.append((seg, ACCENT))
    style: dict[str, Any] = {"family": [mono_family(), FALLBACK_MONO], "fontweight": "bold"}
    while size > 16 and sum(text_width(fig, ax, t, fontsize=size, **style) for t, _ in parts) > max_px:
        size -= 2
    if parts:
        runs(fig, ax, W / 2, y, parts, fontsize=size, **style)
    return segs
