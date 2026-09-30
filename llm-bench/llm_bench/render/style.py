"""The Packet Pulse style. Every color, size and font decision for rendered assets lives here."""

from __future__ import annotations

from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib import font_manager  # noqa: E402
from matplotlib.patches import FancyBboxPatch, Rectangle  # noqa: E402

# --- canvas --------------------------------------------------------------------
W, H, DPI = 1920, 1080, 100
HANDLE = "@packetpulsedev"
WORDMARK = ("PACKET", "PULSE")  # drawn as two runs: off-white, then accent

# --- palette -------------------------------------------------------------------
CARD = "#0F1519"  # card background
PANEL = "#172027"  # raised panels and chart areas
CARD_GLASS = "#0F1519EE"  # card color at 93% opacity: lower-third plate over video
PANEL_HI = "#1F2C35"
GRID = "#2B3A44"
TEAL = "#0E6B67"  # brand accent
TEAL_LIGHT = "#4FC2BA"  # accent for text and highlights on dark
TEXT = "#E3E9ED"
MUTED = "#8FA1AB"
AMBER = "#E0A93B"
RED = "#E2554A"
RED_DARK = "#B8433B"
INK = "#0B1215"  # text on light fills

MODEL_COLORS = [TEAL_LIGHT, AMBER, "#9B8CFF", "#E27D6A", "#6FA8FF", "#B6D86A", "#D98BC2", "#7FD1E8"]

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
    ax.add_patch(Rectangle((0, 0), 14, H, color=TEAL, lw=0))
    ax.text(80, 88, title, fontsize=38, fontweight="bold", color=TEXT, va="center")
    if subtitle:
        ax.text(80, 146, subtitle, fontsize=19, color=TEAL_LIGHT, va="center")


def footer(ax, hardware: str, transparent: bool = False) -> None:
    """Hardware line bottom-left on every card, handle small bottom-right."""
    ax.text(80, H - 42, hardware, fontsize=16, color=MUTED, va="center")
    ax.text(W - 60, H - 42, HANDLE, fontsize=16, color=TEAL_LIGHT, va="center", ha="right", fontweight="bold")


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


# --- text runs, vitals strip and pulse trace (pre-run cards) ---------------------


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


def wordmark(fig, ax, x: float, y: float, size: int, *, align: str = "center") -> None:
    """PACKET PULSE, two-tone, bold."""
    a, b = WORDMARK
    runs(fig, ax, x, y, [(a + " ", TEXT), (b, TEAL_LIGHT)], align=align, fontsize=size, fontweight="bold")


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
    """Patient-monitor readout of the hardware line: monospace, teal values, muted separators.
    Shrinks (down to 16 pt) until the strip fits max_px; returns the segments drawn."""
    segs = vitals_segments(hardware)
    parts: list[tuple[str, str]] = []
    for i, seg in enumerate(segs):
        if i:
            parts.append(("  ·  ", MUTED))
        parts.append((seg, TEAL_LIGHT))
    style: dict[str, Any] = {"family": [mono_family(), FALLBACK_MONO], "fontweight": "bold"}
    while size > 16 and sum(text_width(fig, ax, t, fontsize=size, **style) for t, _ in parts) > max_px:
        size -= 2
    if parts:
        runs(fig, ax, W / 2, y, parts, fontsize=size, **style)
    return segs


def pulse_trace(ax, y: float, *, spike_x: float = 1180.0, amp: float = 190.0) -> None:
    """A flat line that kicks into one ECG-style beat (P wave, QRS spike, T wave) and runs flat again."""
    pts = [(0, 0), (spike_x - 330, 0), (spike_x - 300, -14), (spike_x - 270, -24), (spike_x - 240, -14), (spike_x - 210, 0),
           (spike_x - 120, 0), (spike_x - 92, 22), (spike_x - 55, -amp), (spike_x - 8, amp * 0.42), (spike_x + 22, 0),
           (spike_x + 110, 0), (spike_x + 160, -34), (spike_x + 215, -46), (spike_x + 270, -34), (spike_x + 320, 0), (W, 0)]
    xs, ys = [p[0] for p in pts], [y + p[1] for p in pts]
    for lw, alpha in ((16, 0.06), (9, 0.10), (4.5, 0.95)):  # soft glow under the trace
        ax.plot(xs, ys, color=TEAL_LIGHT, lw=lw, alpha=alpha, solid_joinstyle="round", solid_capstyle="round", zorder=2)
