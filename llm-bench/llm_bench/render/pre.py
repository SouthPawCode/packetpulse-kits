"""Assets that do not depend on benchmark results (`render --pre`): rendered before the run from instance.yaml alone.

Opening vitals frame, round card, title card, lower thirds (Mike and every model), question card, end card and
the thumbnail text card, plus manifest.json (every file with a role). All cards share the look in style.py and
reuse the post-run renderers where the card is the same (title card, lower thirds, thumbnail).
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .. import __version__
from ..config import Instance
from ..textutil import slug
from . import style as S
from .assets import LOGO_SMALL_W, earlier_manifest, fit_text, label_of, lower_third, lower_third_named, manifest_entries, short, thumbnail, title_card

KIT_URL = "github.com/SouthPawCode/packetpulse-kits"
SITE_URL = "packetpulse.dev"
PLACEHOLDER_QUESTION = "This week's question"
ROUND_HEADLINE = "Proving Ground \u00b7 Round {n}"
OPENING_LOGO_W = 1400  # px wide, centred on the opening frame
END_LOGO_W = 1100  # px wide, top of the end card

_QUANT = re.compile(r"(?<![A-Za-z0-9])((?:I?Q\d(?:_[A-Z0-9]+)*)|BF16|FP16|F16|FP8|MXFP4)(?![A-Za-z0-9])", re.I)
_PARAMS = re.compile(r"(?<![A-Za-z0-9.])(\d+(?:\.\d+)?)B(?![A-Za-z])", re.I)
_ACTIVE = re.compile(r"(?<![A-Za-z0-9])A(\d+(?:\.\d+)?)B(?![A-Za-z])", re.I)


def quant_of(name: str) -> str | None:
    """A quantization tag in a model name (Q4_K_M, IQ3_S, BF16, ...), upper-cased, else None."""
    m = _QUANT.search(name)
    return m.group(1).upper() if m else None


def params_label(name: str) -> str | None:
    """'35B' or '35B (3B active)' from a name like 'Ornith-1.5-35B-A3B-GGUF', else None."""
    m = _PARAMS.search(name)
    if not m:
        return None
    a = _ACTIVE.search(name)
    return f"{m.group(1)}B" + (f" ({a.group(1)}B active)" if a else "")


def question_text(q: str | None) -> tuple[str, bool]:
    """(text for the card, is_real_question). The spoken slot is lower case with no question mark; the card
    capitalises it and closes it with '?'. A blank question falls back to the placeholder."""
    q = " ".join((q or "").split())
    if not q:
        return PLACEHOLDER_QUESTION, False
    q = q[0].upper() + q[1:]
    return (q if q[-1] in "?!." else q + "?"), True


def _results_view(inst: Instance) -> dict[str, Any]:
    """The slice of a results dict the shared card renderers read, built from the instance."""
    models = []
    for m in inst.models:
        models.append({
            "name": m.name,
            "label": m.label,
            "backend": m.backend,
            "quant": quant_of(m.name),
            "params_label": params_label(m.name),
        })
    return {"title": inst.title, "hardware": inst.hardware, "models": models, "baseline": inst.baseline}


# ---------------------------------------------------------------- cards


def opening_vitals(view: dict[str, Any], out: Path) -> list[str]:
    """The full logo (mark, wordmark, tagline) centred, the LAB VITALS label and a patient-monitor vitals strip under it."""
    fig, ax = S.new_card()
    ax.add_patch(S.Rectangle((0, 0), 14, S.H, color=S.ACCENT, lw=0))
    S.paste_brand(ax, "logo", S.W / 2, 330, width=OPENING_LOGO_W, align="center")
    ax.text(S.W / 2, 730, "LAB VITALS", fontsize=16, color=S.MUTED, ha="center", va="center", family=[S.mono_family(), S.FALLBACK_MONO], fontweight="bold")
    S.vitals_strip(fig, ax, view["hardware"], 800, size=32)
    S.corner_mark(ax)
    return S.save(fig, out / "opening_vitals", svg=False)


def round_card(view: dict[str, Any], out: Path, round_no: int | None) -> list[str]:
    """'Proving Ground · Round N' over the instance title, or the title over 'Packet Pulse' with no round."""
    headline, sub = (ROUND_HEADLINE.format(n=round_no), view["title"]) if round_no else (view["title"], "Packet Pulse")
    fig, ax = S.new_card()
    ax.add_patch(S.Rectangle((0, 0), 14, S.H, color=S.ACCENT, lw=0))
    S.paste_brand(ax, "logo", 70, 40, width=LOGO_SMALL_W)
    size = next((sz for sz in (96, 84, 72, 64, 56) if S.text_width(fig, ax, headline, fontsize=sz, fontweight="bold") <= 1700), 56)
    if round_no and S.text_width(fig, ax, headline, fontsize=size, fontweight="bold") <= 1700:
        lines = [headline]  # "Proving Ground · Round N" stays on one line, at whatever size fits
    else:
        lines, size = fit_text(headline, max_lines=3, sizes=[96, 84, 72, 64, 56, 48], char_em=0.66)
    step = size * 1.75
    top = 470 - (len(lines) - 1) * step / 2
    for i, ln in enumerate(lines):
        ax.text(110, top + i * step, ln, fontsize=size, fontweight="bold", color=S.TEXT, va="center")
    y = top + (len(lines) - 1) * step + size * 1.1 + 30
    ax.plot([110, 610], [y, y], color=S.ACCENT, lw=5)
    sub_lines, sub_size = fit_text(sub, max_lines=2, sizes=[40, 36, 32, 28], char_em=0.56)
    for i, ln in enumerate(sub_lines):
        ax.text(110, y + 70 + i * sub_size * 1.75, ln, fontsize=sub_size, color=S.ACCENT, va="center")
    S.footer(ax, view["hardware"])
    return S.save(fig, out / "round_card", svg=False)


def question_card(view: dict[str, Any], out: Path, question: str | None) -> list[str]:
    text, real = question_text(question)
    fig, ax = S.new_card()
    ax.add_patch(S.Rectangle((0, 0), 14, S.H, color=S.ACCENT, lw=0))
    S.paste_brand(ax, "logo", 70, 40, width=LOGO_SMALL_W)
    lines, size = fit_text(text, max_lines=4, sizes=[68, 62, 56, 50, 44, 40], char_em=0.68, avail_px=1500)
    step = size * 1.6
    top = 585 - (len(lines) - 1) * step / 2
    if real:
        ax.text(S.W / 2, max(top - step / 2 - 75, 230), "THIS WEEK'S QUESTION", fontsize=20, color=S.ACCENT, ha="center", va="center",
                family=[S.mono_family(), S.FALLBACK_MONO], fontweight="bold")
    for i, ln in enumerate(lines):
        ax.text(S.W / 2, top + i * step, ln, fontsize=size, fontweight="bold", color=S.TEXT, ha="center", va="center")
    y = top + (len(lines) - 1) * step + size * 1.1 + 20
    ax.plot([S.W / 2 - 130, S.W / 2 + 130], [y, y], color=S.ACCENT, lw=5)
    S.footer(ax, view["hardware"])
    return S.save(fig, out / "question_card", svg=False)


def end_card(view: dict[str, Any], out: Path) -> list[str]:
    fig, ax = S.new_card()
    ax.add_patch(S.Rectangle((0, 0), 14, S.H, color=S.ACCENT, lw=0))
    S.paste_brand(ax, "logo", S.W / 2, 90, width=END_LOGO_W, align="center")
    S.vitals_strip(fig, ax, view["hardware"], 425, size=28)
    ax.plot([S.W / 2 - 170, S.W / 2 + 170], [500, 500], color=S.ACCENT, lw=5)
    mono = [S.mono_family(), S.FALLBACK_MONO]
    ax.text(S.W / 2, 625, KIT_URL, fontsize=40, color=S.TEXT, ha="center", va="center", family=mono, fontweight="bold")
    ax.text(S.W / 2, 745, SITE_URL, fontsize=40, color=S.TEXT, ha="center", va="center", family=mono, fontweight="bold")
    ax.text(S.W / 2, 865, S.HANDLE, fontsize=40, color=S.ACCENT, ha="center", va="center", family=mono, fontweight="bold")
    return S.save(fig, out / "end_card", svg=False)


# ---------------------------------------------------------------- driver


def render_pre(inst: Instance, out_dir: str | Path, *, question: str | None = None, round_no: int | None = None,
               thumbnail_text: str | None = None, config_path: str | Path | None = None) -> dict[str, Any]:
    """Render every pre-run asset into out_dir from the instance alone; write and return manifest.json."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    view = _results_view(inst)
    files: list[dict[str, Any]] = []

    def register(name: str, role: str, fmts: list[str], model: str | None = None, transparent: bool = False) -> None:
        for e in manifest_entries(out, name, role, fmts, model, transparent):
            e["phase"] = "pre"
            files.append(e)

    register("opening_vitals", "opening_vitals", opening_vitals(view, out))
    register("round_card", "round_card", round_card(view, out, round_no))
    register("title_card", "title_card", title_card(view, out))
    a, b = lower_third_named(view, "Mike", "Packet Pulse", "mike", out)
    register("lower_third_mike", "lower_third_mike", a, transparent=True)
    register("lower_third_mike_card", "lower_third_mike_preview", b)
    used = {"mike"}
    for m in view["models"]:
        sl = base = slug(m["name"])
        n = 2
        while sl in used:  # two names that slug alike (or a model called "mike") must not share files
            sl, n = f"{base}-{n}", n + 1
        used.add(sl)
        a, b = lower_third(view, m, out, base=sl)
        register(f"lower_third_{sl}", "lower_third", a, model=m["name"], transparent=True)
        register(f"lower_third_{sl}_card", "lower_third_preview", b, model=m["name"])
    register("question_card", "question_card", question_card(view, out, question))
    register("end_card", "end_card", end_card(view, out))
    thumb = (thumbnail_text or "").strip() or inst.title
    register("thumbnail_text", "thumbnail_text", thumbnail(view, out, thumb))

    new_names = {f["file"] for f in files}
    files += [f for f in earlier_manifest(out, "post") if f["file"] not in new_names]  # results-based files from a post-run render here

    qtext, real = question_text(question)
    manifest = {
        "schema_version": 1,
        "phase": "pre",
        "generated_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "kit_version": __version__,
        "title": inst.title,
        "hardware": inst.hardware,
        "handle": S.HANDLE,
        "round": round_no,
        "question": qtext if real else None,
        "thumbnail_text": thumb,
        "source": Path(config_path).name if config_path else None,
        "canvas": {"width": S.W, "height": S.H},
        "files": files,
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest
