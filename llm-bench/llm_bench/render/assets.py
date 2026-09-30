"""Assets from results.json (spec section 4): scorecard, charts, matrix, verdict, title, lower thirds, thumbnail."""

from __future__ import annotations

import json
import textwrap
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from PIL import Image

from .. import __version__
from ..textutil import slug
from . import style as S

# ---------------------------------------------------------------- data helpers


def label_of(m: dict[str, Any]) -> str:
    return m.get("label") or m["name"]


def short(text: str, n: int) -> str:
    return text if len(text) <= n else text[: n - 1] + "…"


def rung_label(v: str) -> str:
    try:
        n = int(v)
    except ValueError:
        return v
    return f"{n // 1024}k" if n >= 1024 and n % 1024 == 0 else str(n)


def ladder_rows(results: dict[str, Any]) -> tuple[list[str], dict[str, dict[str, dict[str, Any]]]]:
    """(sorted rung variants, {model: {rung: run}}) for task 1."""
    per: dict[str, dict[str, dict[str, Any]]] = {}
    rungs: set[int] = set()
    for r in results["runs"]:
        if r["task_id"] != 1:
            continue
        try:
            rungs.add(int(r["variant"]))
        except ValueError:
            continue
        per.setdefault(r["model"], {})[r["variant"]] = r
    return [str(x) for x in sorted(rungs)], per


def task_score(results: dict[str, Any], model: str, task: dict[str, Any]) -> float | None:
    from ..results import task_score as ts

    rows = [r for r in results["runs"] if r["model"] == model and r["task_id"] == task["id"]]
    return ts(rows, task.get("aggregate", "mean"))


def fmt_vram(m: dict[str, Any]) -> str:
    v = m.get("vram_after_load_mb")
    if v is None:
        return "API" if m.get("backend") == "openai_compat" else "n/a"
    return f"{v / 1024:.1f} GB"


def fmt_cost(m: dict[str, Any], s: dict[str, Any]) -> str:
    c = s.get("cost_usd_total")
    if c is not None:
        return f"${c:.4f}" if c < 1 else f"${c:.2f}"
    return "local" if m.get("backend") in ("ollama", "dryrun") else "n/a"


def subtitle(results: dict[str, Any]) -> str:
    return f"{results['title']}  ·  The Packet Pulse Proving Ground"


def no_data(ax, msg: str) -> None:
    ax.text(S.W / 2, S.H / 2, msg, fontsize=26, color=S.MUTED, ha="center", va="center")


# ---------------------------------------------------------------- scorecard


def scorecard(results: dict[str, Any], out: Path) -> list[str]:
    fig, ax = S.new_card()
    S.header(ax, "Scorecard", subtitle(results))
    models, summary = results["models"], results["summary"]
    cols = [("MODEL", 90, "left"), ("SCORE", 810, "center"), ("PASSES", 1035, "center"), ("AVG TOK/S", 1190, "center"),
            ("AVG TTFT", 1385, "center"), ("VRAM", 1570, "center"), ("COST", 1745, "center")]
    top = 215
    for text, x, al in cols:
        ax.text(x, top, text, fontsize=15, color=S.MUTED, ha=al, va="center", fontweight="bold")
    ax.plot([80, 1840], [top + 26, top + 26], color=S.GRID, lw=2)
    n = max(len(models), 1)
    row_h = min(118, 690 / n)
    y0 = top + 44
    pending_any = False
    for i, m in enumerate(models):
        s = summary.get(m["name"], {})
        y = y0 + i * row_h
        base = results.get("baseline") == m["name"]
        S.panel(ax, 70, y, 1780, row_h - 12, fill=S.TEAL if base else S.PANEL, edge=S.TEAL_LIGHT if base else None)
        cy = y + (row_h - 12) / 2
        ax.text(90, cy - (10 if base else 0), short(label_of(m), 38), fontsize=24, fontweight="bold", color=S.TEXT, va="center")
        if base:
            ax.text(90, cy + 26, "BASELINE", fontsize=13, color=S.TEXT, va="center", fontweight="bold")
        unscored = s.get("unscored_task_ids") or []
        pending_any |= bool(unscored)
        total, mx = s.get("score_total"), s.get("score_max")
        score = "n/a" if total is None else f"{total:.2f}".rstrip("0").rstrip(".") + f" / {mx:g}" + ("*" if unscored else "")
        ax.text(810, cy, score, fontsize=30, fontweight="bold", color=S.TEAL_LIGHT if not base else S.TEXT, ha="center", va="center")
        ax.text(1035, cy, f"{s.get('passes', 0)} / {sum(1 for t in results['tasks'] if t['weight'] > 0)}", fontsize=24, ha="center", va="center")
        ax.text(1190, cy, "n/a" if s.get("avg_tps") is None else f"{s['avg_tps']:.1f}", fontsize=24, ha="center", va="center")
        ttft = s.get("avg_ttft_ms")
        ax.text(1385, cy, "n/a" if ttft is None else (f"{ttft / 1000:.1f} s" if ttft >= 1000 else f"{ttft:.0f} ms"), fontsize=24, ha="center", va="center")
        ax.text(1570, cy, fmt_vram(m), fontsize=24, ha="center", va="center")
        ax.text(1745, cy, fmt_cost(m, s), fontsize=24, ha="center", va="center")
    note = "Score = sum of task scores (0 to 1 each, weights in results.json). Tok/s and TTFT are averaged over every measured run."
    if pending_any:
        note = "* Some tasks are not scored yet (rubric pending or validator unavailable). " + note
    ax.text(80, 985, textwrap.fill(note, 170), fontsize=13, color=S.MUTED, va="center")
    S.footer(ax, results["hardware"])
    return S.save(fig, out / "scorecard")


# ---------------------------------------------------------------- charts


def tps_chart(results: dict[str, Any], out: Path) -> list[str]:
    fig, ax = S.new_card()
    S.header(ax, "Generation speed by prompt size", subtitle(results))
    rungs, per = ladder_rows(results)
    models = [m for m in results["models"] if m["name"] in per]
    S.footer(ax, results["hardware"])
    if not rungs or not models:
        no_data(ax, "No Speed Ladder data in this run")
        return S.save(fig, out / "tps_chart")
    cx = S.chart_axes(fig, (0.075, 0.17, 0.89, 0.62))
    n = len(models)
    width = 0.8 / n
    vmax = 0.0
    for j, m in enumerate(models):
        color = S.model_color(results, m["name"])
        for i, rung in enumerate(rungs):
            run = per[m["name"]].get(rung)
            v = run["tokens_per_second"] if run else None
            x = i - 0.4 + width * (j + 0.5)
            if v is None:
                cx.text(x, 0.5, "x", color=S.RED, ha="center", va="bottom", fontsize=16, fontweight="bold")
                continue
            vmax = max(vmax, v)
            cx.bar(x, v, width * 0.92, color=color, zorder=3)
            cx.text(x, v, f"{v:.0f}" if v >= 10 else f"{v:.1f}", ha="center", va="bottom", color=S.TEXT, fontsize=13 if n > 3 else 15, zorder=4)
    cx.set_xticks(range(len(rungs)))
    cx.set_xticklabels([rung_label(r) for r in rungs])
    cx.set_xlabel("prompt size (tokens)", color=S.MUTED, fontsize=17, labelpad=12)
    cx.set_ylabel("tokens per second (higher is better)", color=S.MUTED, fontsize=17, labelpad=12)
    cx.set_ylim(0, max(vmax * 1.18, 1))
    cx.set_xlim(-0.6, len(rungs) - 0.4)
    cx.grid(axis="x", visible=False)
    _legend(cx, results, models)
    return S.save(fig, out / "tps_chart")


def ttft_chart(results: dict[str, Any], out: Path) -> list[str]:
    fig, ax = S.new_card()
    S.header(ax, "Time to first token by prompt size", subtitle(results))
    rungs, per = ladder_rows(results)
    models = [m for m in results["models"] if m["name"] in per]
    S.footer(ax, results["hardware"])
    if not rungs or not models:
        no_data(ax, "No Speed Ladder data in this run")
        return S.save(fig, out / "ttft_chart")
    cx = S.chart_axes(fig, (0.075, 0.17, 0.89, 0.62))
    vals = [r["ttft_ms"] for p in per.values() for r in p.values() if r["ttft_ms"]]
    use_log = bool(vals) and max(vals) / max(min(vals), 1) > 12
    for m in models:
        color = S.model_color(results, m["name"])
        xs, ys = [], []
        for i, rung in enumerate(rungs):
            run = per[m["name"]].get(rung)
            if run and run["ttft_ms"]:
                xs.append(i)
                ys.append(run["ttft_ms"])
        if not xs:
            continue
        cx.plot(xs, ys, marker="o", ms=11, lw=4, color=color, zorder=3)
        j = models.index(m)
        for k, (x, y) in enumerate(zip(xs, ys)):
            if k not in (0, len(xs) - 1):
                continue
            label = f"{y / 1000:.1f} s" if y >= 1000 else f"{y:.0f} ms"
            dx, ha = (-14, "right") if k == 0 else (14, "left")
            cx.annotate(label, (x, y), textcoords="offset points", xytext=(dx, 18 * (j - (len(models) - 1) / 2) * -1 - 5), ha=ha, color=color, fontsize=15, fontweight="bold")
    if use_log:
        cx.set_yscale("log")
        from matplotlib.ticker import FuncFormatter, LogLocator

        cx.yaxis.set_major_locator(LogLocator(base=10, subs=(1.0, 2.0, 5.0)))
        cx.yaxis.set_minor_locator(LogLocator(base=10, subs=()))
        cx.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v / 1000:g} s" if v >= 1000 else f"{v:g} ms"))
        cx.set_ylim(max(min(vals) / 2, 1), max(vals) * 2.2)
        ylabel = "time to first token (log scale, lower is better)"
    else:
        cx.set_ylim(0, (max(vals) if vals else 1) * 1.25)
        ylabel = "time to first token in ms (lower is better)"
    cx.set_xticks(range(len(rungs)))
    cx.set_xticklabels([rung_label(r) for r in rungs])
    cx.set_xlim(-0.6, len(rungs) - 0.4)
    cx.set_xlabel("prompt size (tokens)", color=S.MUTED, fontsize=17, labelpad=12)
    cx.set_ylabel(ylabel, color=S.MUTED, fontsize=17, labelpad=12)
    _legend(cx, results, models)
    return S.save(fig, out / "ttft_chart")


def _legend(cx, results: dict[str, Any], models: list[dict[str, Any]]) -> None:
    from matplotlib.patches import Patch

    handles = [Patch(facecolor=S.model_color(results, m["name"]), label=short(label_of(m), 34)) for m in models]
    leg = cx.legend(
        handles=handles, loc="lower right", bbox_to_anchor=(1.0, 1.015), ncol=min(len(handles), 3), frameon=False,
        fontsize=16, labelcolor=S.TEXT, columnspacing=1.6, handlelength=1.2,
    )
    leg.set_zorder(10)


def vram_chart(results: dict[str, Any], out: Path) -> list[str]:
    fig, ax = S.new_card()
    S.header(ax, "VRAM after load", subtitle(results))
    S.footer(ax, results["hardware"])
    models = results["models"]
    with_vram = [m for m in models if m.get("vram_after_load_mb") is not None]
    if not models:
        no_data(ax, "No models in this run")
        return S.save(fig, out / "vram_chart")
    cx = S.chart_axes(fig, (0.26, 0.17, 0.70, 0.62))
    names = [short(label_of(m), 30) for m in models]
    ys = list(range(len(models)))
    vmax = 16.0
    for i, m in enumerate(models):
        v = m.get("vram_after_load_mb")
        if v is None:
            cx.text(0.3, i, "API model: no local VRAM" if m.get("backend") == "openai_compat" else "no VRAM reading", va="center", color=S.MUTED, fontsize=17)
            continue
        gb = v / 1024
        vmax = max(vmax, gb)
        color = S.model_color(results, m["name"])
        cx.barh(i, gb, 0.62, color=color, zorder=3)
        extra = []
        if m.get("size_gb"):
            extra.append(f"{m['size_gb']:g} GB on disk")
        if m.get("gpu_share_pct") is not None:
            extra.append(f"{m['gpu_share_pct']}% on GPU")
        if gb >= 6.5:
            cx.text(0.25, i - 0.09, f"{gb:.1f} GB", va="bottom", color=S.INK, fontsize=24, fontweight="bold", zorder=4)
            if extra:
                cx.text(0.25, i - 0.04, "  ·  ".join(extra), va="top", color=S.INK, fontsize=15, zorder=4)
        else:
            cx.text(gb + 0.25, i, f"{gb:.1f} GB" + (f"  ({', '.join(extra)})" if extra else ""), va="center", color=S.TEXT, fontsize=17, zorder=4)
    cx.axvline(16, color=S.AMBER, lw=3.5, ls="--", zorder=5)
    cx.text(16, len(models) - 0.45, " 16 GB card", color=S.AMBER, fontsize=17, fontweight="bold", va="bottom", ha="left", zorder=6)
    cx.set_xlim(0, max(vmax * 1.08, 18))
    cx.set_ylim(len(models) - 0.4, -0.6)
    cx.set_yticks(ys)
    cx.set_yticklabels(names, color=S.TEXT, fontsize=17)
    cx.set_xlabel("GB of GPU memory in use after the model loaded (nvidia-smi)", color=S.MUTED, fontsize=17, labelpad=12)
    cx.grid(axis="y", visible=False)
    if not with_vram:
        cx.text(0.5, 0.5, "", transform=cx.transAxes)
    return S.save(fig, out / "vram_chart")


# ---------------------------------------------------------------- matrix


def matrix(results: dict[str, Any], out: Path) -> list[str]:
    fig, ax = S.new_card()
    S.header(ax, "Pass / partial / fail", subtitle(results))
    S.footer(ax, results["hardware"])
    models, tasks = results["models"], results["tasks"]
    if not models or not tasks:
        no_data(ax, "No results")
        return S.save(fig, out / "matrix")
    left, right, top, bottom = 80, 1840, 200, 970
    label_w = 520
    col_w = (right - left - label_w) / len(models)
    row_h = (bottom - top - 70) / len(tasks)
    for j, m in enumerate(models):
        cx = left + label_w + col_w * (j + 0.5)
        ax.text(cx, top + 25, short(label_of(m), 26 if len(models) <= 3 else 18), fontsize=18 if len(models) <= 3 else 15, fontweight="bold", ha="center", va="center", color=S.model_color(results, m["name"]))
    by_model_task: dict[tuple[str, int], list[dict[str, Any]]] = {}
    for r in results["runs"]:
        by_model_task.setdefault((r["model"], r["task_id"]), []).append(r)
    for i, t in enumerate(tasks):
        y = top + 70 + i * row_h
        ax.text(left, y + row_h / 2, f"{t['id']}  {t['name']}", fontsize=19, color=S.TEXT, va="center")
        for j, m in enumerate(models):
            x = left + label_w + col_w * j
            rows = by_model_task.get((m["name"], t["id"]), [])
            score = task_score(results, m["name"], t)
            if t["weight"] == 0:
                txt, fill, fg = "VERDICT", S.PANEL_HI, S.MUTED
            elif not rows:
                txt, fill, fg = "NOT RUN", S.PANEL, S.MUTED
            elif score is None:
                note = (rows[0].get("details") or {}).get("score_note", "")
                txt, fill, fg = ("UNAVAILABLE" if "unavailable" in note else "PENDING"), S.PANEL_HI, S.AMBER
            elif score >= 0.999:
                txt, fill, fg = "PASS", S.TEAL_LIGHT, S.INK
            elif score > 0:
                txt, fill, fg = f"PARTIAL {score:.2f}", S.AMBER, S.INK
            else:
                txt, fill, fg = "FAIL", S.RED_DARK, S.TEXT
            S.chip(ax, x + 8, y + 5, col_w - 16, row_h - 10, txt, fill, fg, size=15 if len(models) > 3 else 17)
    return S.save(fig, out / "matrix")


# ---------------------------------------------------------------- cards


QUESTION_LABELS = [
    ("latency_sensitive", "Latency-sensitive use"),
    ("quality_sensitive", "Quality-sensitive use"),
    ("vram_constrained", "A 16 GB, shared box"),
    ("would_i_run_it", "Would I run it for a customer?"),
]


def verdict_card(results: dict[str, Any], m: dict[str, Any], out: Path) -> list[str]:
    fig, ax = S.new_card()
    S.header(ax, short(label_of(m), 44), "Customer verdict  ·  The Packet Pulse Proving Ground")
    S.footer(ax, results["hardware"])
    v = (results.get("verdicts") or {}).get(m["name"]) or {}
    s = (results.get("summary") or {}).get(m["name"], {})
    bits = []
    if m.get("size_gb"):
        bits.append(f"{m['size_gb']:g} GB")
    if m.get("quant"):
        bits.append(m["quant"])
    if s.get("score_total") is not None:
        bits.append(f"score {s['score_total']:.2f}".rstrip("0").rstrip(".") + f" / {s['score_max']:g}")
    if m.get("backend") == "openai_compat":
        bits.append(f"cost {fmt_cost(m, s)}")  # "n/a" when the instance had no pricing, never $0
    if bits:
        ax.text(80, 205, "  ·  ".join(bits), fontsize=22, color=S.MUTED, va="center")
    colors = {"yes": (S.TEAL_LIGHT, S.INK), "no": (S.RED_DARK, S.TEXT), "maybe": (S.AMBER, S.INK)}
    for i, (key, label) in enumerate(QUESTION_LABELS):
        y = 280 + i * 130
        final = key == "would_i_run_it"
        S.panel(ax, 80, y, 1760, 112, fill=S.TEAL if final else S.PANEL, edge=S.TEAL_LIGHT if final else None)
        ax.text(120, y + 56, label, fontsize=30 if final else 27, fontweight="bold" if final else "normal", color=S.TEXT, va="center")
        ans = (v.get(key) or "").lower()
        fill, fg = colors.get(ans, (S.PANEL_HI, S.MUTED))
        S.chip(ax, 1500, y + 22, 300, 68, ans.upper() if ans else "PENDING", fill, fg, size=26)
    note = v.get("note")
    if note:
        ax.text(80, 845, textwrap.fill(f"“{note}”", 110), fontsize=21, color=S.TEXT, va="top", style="italic")
    return S.save(fig, out / f"verdict_{slug(m['name'])}", svg=False)


def title_card(results: dict[str, Any], out: Path) -> list[str]:
    fig, ax = S.new_card()
    ax.add_patch(S.Rectangle((0, 0), 14, S.H, color=S.TEAL, lw=0))
    ax.text(110, 250, "THE PACKET PULSE PROVING GROUND", fontsize=22, color=S.TEAL_LIGHT, fontweight="bold", va="center")
    lines, size = fit_text(results["title"], max_lines=3, sizes=[84, 72, 64, 56, 48], char_em=0.66)
    for i, ln in enumerate(lines):
        ax.text(110, 390 + i * (size * 1.75), ln, fontsize=size, fontweight="bold", color=S.TEXT, va="center")
    y = 390 + len(lines) * size * 1.75 + 40
    ax.plot([110, 610], [y, y], color=S.TEAL_LIGHT, lw=5)
    ax.text(110, y + 70, results["hardware"], fontsize=30, color=S.TEXT, va="center")
    ax.text(110, y + 135, "  ·  ".join(short(label_of(m), 28) for m in results["models"][:4]), fontsize=22, color=S.MUTED, va="center")
    ax.text(S.W - 60, S.H - 42, S.HANDLE, fontsize=16, color=S.TEAL_LIGHT, va="center", ha="right", fontweight="bold")
    return S.save(fig, out / "title_card", svg=False)


def _plate(results: dict[str, Any], title: str, line: str, transparent: bool) -> Any:
    """The lower-third plate: a large name, one accent line under it, the hardware line and the handle."""
    fig, ax = S.new_card(transparent=transparent)
    S.panel(ax, 80, 800, 1000, 170, fill=S.CARD_GLASS if transparent else S.PANEL)
    ax.add_patch(S.Rectangle((80, 800), 12, 170, color=S.TEAL_LIGHT, lw=0))
    ax.text(124, 850, short(title, 40), fontsize=34, fontweight="bold", color=S.TEXT, va="center")
    ax.text(124, 900, line, fontsize=22, color=S.TEAL_LIGHT, va="center")
    ax.text(124, 944, short(results["hardware"], 70), fontsize=13, color=S.MUTED, va="center")
    ax.text(1060, 950, S.HANDLE, fontsize=13, color=S.TEAL_LIGHT, va="bottom", ha="right", fontweight="bold")
    return fig


def _lower_third(results: dict[str, Any], m: dict[str, Any], transparent: bool) -> Any:
    bits = []
    if m.get("size_gb"):
        bits.append(f"{m['size_gb']:g} GB")
    elif m.get("params_label"):  # before the run the size on disk is unknown; the name may carry the parameter count
        bits.append(str(m["params_label"]))
    if m.get("quant"):
        bits.append(str(m["quant"]))
    if m.get("gpu_share_pct") is not None:
        bits.append(f"{m['gpu_share_pct']}% GPU")
    bits.append("API" if m.get("backend") == "openai_compat" else "local")
    return _plate(results, label_of(m), "  ·  ".join(bits), transparent)


def lower_third(results: dict[str, Any], m: dict[str, Any], out: Path, base: str | None = None) -> tuple[list[str], list[str]]:
    base = base or slug(m["name"])
    a = S.save(_lower_third(results, m, True), out / f"lower_third_{base}", svg=False, transparent=True)
    b = S.save(_lower_third(results, m, False), out / f"lower_third_{base}_card", svg=False)
    return a, b


def lower_third_named(results: dict[str, Any], title: str, line: str, base: str, out: Path) -> tuple[list[str], list[str]]:
    """A lower third for a person or a label rather than a model: lower_third_<base>.png and its _card preview."""
    a = S.save(_plate(results, title, line, True), out / f"lower_third_{base}", svg=False, transparent=True)
    b = S.save(_plate(results, title, line, False), out / f"lower_third_{base}_card", svg=False)
    return a, b


def thumbnail(results: dict[str, Any], out: Path, text: str) -> list[str]:
    fig, ax = S.new_card()
    ax.add_patch(S.Rectangle((0, 0), S.W, 18, color=S.TEAL, lw=0))
    lines, size = fit_text(text.upper(), max_lines=4, sizes=[150, 130, 112, 96, 84, 72, 60], char_em=0.80, avail_px=1680)
    step = size * 1.38
    total = len(lines) * step
    y0 = S.H / 2 - total / 2 + step / 2 + 20
    for i, ln in enumerate(lines):
        ax.text(110, y0 + i * step, ln, fontsize=size, fontweight="bold", color=S.TEXT if i < len(lines) - 1 or len(lines) == 1 else S.TEAL_LIGHT, va="center")
    ax.plot([110, 700], [y0 + total - step * 0.2, y0 + total - step * 0.2], color=S.TEAL_LIGHT, lw=6)
    ax.text(110, 110, "PACKET PULSE PROVING GROUND", fontsize=22, color=S.TEAL_LIGHT, fontweight="bold", va="center")
    S.footer(ax, results["hardware"])
    return S.save(fig, out / "thumbnail_text", svg=False)


# ---------------------------------------------------------------- text fitting


def fit_text(text: str, *, max_lines: int, sizes: list[int], char_em: float, avail_px: float = 1700.0) -> tuple[list[str], int]:
    """Largest font size (pt) at which `text` wraps into at most max_lines lines within avail_px.

    char_em is the average glyph width in em for the font and case in use; one pt is 100/72 px here.
    """
    for size in sizes:
        width = max(6, int(avail_px / (char_em * size * (S.DPI / 72.0))))
        lines = textwrap.wrap(text, width) or [""]
        if len(lines) <= max_lines:
            return lines, size
    size = sizes[-1]
    width = max(6, int(avail_px / (char_em * size * (S.DPI / 72.0))))
    lines = textwrap.wrap(text, width)[:max_lines]
    return lines, size


# ---------------------------------------------------------------- driver


def manifest_entries(out: Path, name: str, role: str, fmts: list[str], model: str | None = None, transparent: bool = False) -> list[dict[str, Any]]:
    """One manifest entry per written format of `name` (file, role, format, model, transparent, size, bytes)."""
    entries: list[dict[str, Any]] = []
    for f in fmts:
        p = out / f"{name}.{f}"
        entry: dict[str, Any] = {"file": p.name, "role": role, "format": f, "model": model, "transparent": transparent}
        if f == "png":
            with Image.open(p) as im:
                entry["width"], entry["height"] = im.size
        else:
            entry["width"], entry["height"] = S.W, S.H
        entry["bytes"] = p.stat().st_size
        entries.append(entry)
    return entries


def earlier_manifest(out: Path, phase: str) -> list[dict[str, Any]]:
    """Entries of a manifest.json already in `out` that belong to `phase` ("pre" entries carry "phase": "pre";
    post-run entries carry no phase) and whose file is still there."""
    try:
        old = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(old, dict):
        return []
    return [f for f in old.get("files", []) if isinstance(f, dict) and f.get("file") and (f.get("phase") or "post") == phase
            and (out / f["file"]).is_file()]


def render_all(results: dict[str, Any], out_dir: Path, *, thumbnail_text: str | None = None, results_path: str | Path | None = None) -> dict[str, Any]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    files: list[dict[str, Any]] = []
    # Files an earlier `render --pre` left in this directory are the pre-run versions: leave them alone.
    kept = earlier_manifest(out, "pre")
    owned = {f["file"] for f in kept}

    def register(name: str, role: str, fmts: list[str], model: str | None = None, transparent: bool = False) -> None:
        files.extend(manifest_entries(out, name, role, fmts, model, transparent))

    register("scorecard", "scorecard", scorecard(results, out))
    register("tps_chart", "tps_chart", tps_chart(results, out))
    register("ttft_chart", "ttft_chart", ttft_chart(results, out))
    register("vram_chart", "vram_chart", vram_chart(results, out))
    register("matrix", "matrix", matrix(results, out))
    for m in results["models"]:
        sl = slug(m["name"])
        register(f"verdict_{sl}", "verdict_card", verdict_card(results, m, out), model=m["name"])
    if "title_card.png" not in owned:
        register("title_card", "title_card", title_card(results, out))
    for m in results["models"]:
        sl = slug(m["name"])
        if f"lower_third_{sl}.png" in owned:
            continue
        a, b = lower_third(results, m, out)
        register(f"lower_third_{sl}", "lower_third", a, model=m["name"], transparent=True)
        register(f"lower_third_{sl}_card", "lower_third_preview", b, model=m["name"])
    if "thumbnail_text.png" not in owned:
        text = thumbnail_text or results.get("thumbnail_text") or results["title"]
        register("thumbnail_text", "thumbnail_text", thumbnail(results, out, text))
    files += [f for f in kept if f["file"] not in {x["file"] for x in files}]

    manifest = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "kit_version": __version__,
        "title": results["title"],
        "hardware": results["hardware"],
        "handle": S.HANDLE,
        "source": Path(results_path).name if results_path else None,
        "canvas": {"width": S.W, "height": S.H},
        "files": files,
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest
