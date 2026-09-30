from __future__ import annotations

import copy
import json
import re
import shutil
from pathlib import Path

import pytest
from PIL import Image

from llm_bench.cli import main
from llm_bench.render import assets, style as S
from llm_bench.results import load_results, recompute_summary
from llm_bench.textutil import slug

SPEC_FILES = [
    "scorecard.png", "scorecard.svg", "tps_chart.png", "tps_chart.svg", "ttft_chart.png", "ttft_chart.svg",
    "vram_chart.png", "vram_chart.svg", "matrix.png", "matrix.svg", "title_card.png", "thumbnail_text.png",
]
MODELS = ["dry-baseline-27b", "dry-moe-35b", "dry-cloud-flash"]


@pytest.fixture(scope="module")
def rendered(dryrun_out, tmp_path_factory):
    out = tmp_path_factory.mktemp("assets")
    assert main(["render", "--results", str(dryrun_out / "results.json"), "--out", str(out)]) == 0
    return out


@pytest.fixture
def capture(monkeypatch):
    """Records every text drawn on each card, keyed by output file name."""
    seen: dict[str, list[str]] = {}
    orig = S.save

    def spy(fig, base, **kw):
        seen[Path(str(base)).name] = [t.get_text() for ax in fig.axes for t in ax.texts]
        return orig(fig, base, **kw)

    monkeypatch.setattr(S, "save", spy)
    return seen


def test_every_spec_asset_is_written(rendered):
    files = {p.name for p in rendered.iterdir()}
    for f in SPEC_FILES + ["manifest.json"]:
        assert f in files, f
    for m in MODELS:
        assert f"verdict_{m}.png" in files and f"lower_third_{m}.png" in files
    manifest = json.loads((rendered / "manifest.json").read_text())
    listed = {f["file"] for f in manifest["files"]}
    assert listed == files - {"manifest.json"}, "manifest lists every file, and only real files"


def test_manifest_roles_and_sizes(rendered):
    manifest = json.loads((rendered / "manifest.json").read_text())
    roles = {f["role"] for f in manifest["files"]}
    assert {"scorecard", "tps_chart", "ttft_chart", "vram_chart", "matrix", "verdict_card", "title_card", "lower_third", "thumbnail_text"} <= roles
    assert manifest["handle"] == "@packetpulsedev" and manifest["canvas"] == {"width": 1920, "height": 1080}
    assert manifest["hardware"].startswith("RTX 4070 Ti SUPER 16 GB") and manifest["kit_version"] == "0.2.0"
    for f in manifest["files"]:
        p = rendered / f["file"]
        assert p.stat().st_size == f["bytes"] > 1000, f["file"]
        assert f["format"] == p.suffix[1:]
        if f["format"] == "png":
            with Image.open(p) as im:
                assert im.size == (1920, 1080) and [f["width"], f["height"]] == [1920, 1080], f["file"]
    per_model = [f for f in manifest["files"] if f["role"] == "verdict_card"]
    assert sorted(f["model"] for f in per_model) == sorted(MODELS)


def test_svgs_are_valid_xml_1920x1080(rendered):
    import xml.etree.ElementTree as ET

    for svg in rendered.glob("*.svg"):
        root = ET.parse(svg).getroot()
        assert root.tag.endswith("svg")
        assert root.get("width") and root.get("height")
        assert svg.stat().st_size > 5000


def test_lower_third_has_transparent_variant(rendered):
    for m in MODELS:
        with Image.open(rendered / f"lower_third_{m}.png") as im:
            assert im.mode == "RGBA"
            assert im.getpixel((10, 10))[3] == 0, "outside the plate the overlay is fully transparent"
            assert im.getpixel((1800, 500))[3] == 0
            assert im.getpixel((600, 900))[3] > 200, "the plate itself is opaque enough to read"
        with Image.open(rendered / f"lower_third_{m}_card.png") as im:
            assert im.convert("RGBA").getpixel((10, 10))[3] == 255
    manifest = json.loads((rendered / "manifest.json").read_text())
    lt = {f["file"]: f for f in manifest["files"] if f["role"] == "lower_third"}
    assert all(f["transparent"] for f in lt.values())


def test_cards_are_dark_with_brand_accent(rendered):
    with Image.open(rendered / "scorecard.png") as im:
        assert im.convert("RGB").getpixel((1000, 800)) == (15, 21, 25), "card background #0F1519"
        assert im.convert("RGB").getpixel((5, 500)) == (14, 107, 103), "brand teal #0E6B67 edge bar"


def test_every_card_carries_hardware_line_and_handle(dryrun_out, tmp_path, capture):
    res = load_results(dryrun_out / "results.json")
    assets.render_all(res, tmp_path)
    expected = {"scorecard", "tps_chart", "ttft_chart", "vram_chart", "matrix", "title_card", "thumbnail_text"}
    expected |= {f"verdict_{m}" for m in MODELS} | {f"lower_third_{m}" for m in MODELS} | {f"lower_third_{m}_card" for m in MODELS}
    assert expected <= set(capture), expected - set(capture)
    for name in expected:
        texts = capture[name]
        assert res["hardware"] in texts, f"{name} is missing the hardware line"
        assert "@packetpulsedev" in texts, f"{name} is missing the handle"


def test_single_style_module_holds_all_colors():
    src = Path(assets.__file__).read_text()
    assert not re.search(r"#[0-9A-Fa-f]{6}", src), "colors belong in render/style.py"
    style_src = Path(S.__file__).read_text()
    for token in ("#0F1519", "#0E6B67", "#4FC2BA", "#E3E9ED"):
        assert token in style_src
    assert S.HANDLE == "@packetpulsedev" and (S.W, S.H) == (1920, 1080)


def test_font_falls_back_to_dejavu_when_plex_is_absent():
    assert S.font_family() in ("IBM Plex Sans", "DejaVu Sans")


def test_vram_chart_draws_the_16gb_line_and_scale(dryrun_out, tmp_path, capture, monkeypatch):
    res = load_results(dryrun_out / "results.json")
    lines = {}
    orig = S.chart_axes

    def spy_axes(fig, rect):
        ax = orig(fig, rect)
        lines.setdefault("axes", []).append(ax)
        return ax

    monkeypatch.setattr(S, "chart_axes", spy_axes)
    assets.vram_chart(res, tmp_path)
    ax = lines["axes"][-1]
    assert any(abs(l.get_xdata()[0] - 16) < 1e-9 for l in ax.lines), "the 16 GB card line"
    assert any("16 GB card" in t.get_text() for t in ax.texts)
    assert ax.get_xlim()[1] >= 18 and ax.get_xlabel() and len(ax.get_xticks()) > 3, "visible scale"


def test_charts_have_axis_labels_and_ticks(dryrun_out, tmp_path, monkeypatch):
    res = load_results(dryrun_out / "results.json")
    axes = []
    orig = S.chart_axes
    monkeypatch.setattr(S, "chart_axes", lambda fig, rect: axes.append(orig(fig, rect)) or axes[-1])
    assets.tps_chart(res, tmp_path)
    assets.ttft_chart(res, tmp_path)
    for ax in axes:
        assert ax.get_xlabel() and ax.get_ylabel()
        assert [t.get_text() for t in ax.get_xticklabels()] == ["512", "4k", "16k", "32k", "64k"]


def test_matrix_states(dryrun_out, tmp_path, capture):
    res = load_results(dryrun_out / "results.json")
    assets.matrix(res, tmp_path)
    t = capture["matrix"]
    for word in ("PASS", "PARTIAL", "FAIL", "PENDING", "UNAVAILABLE", "VERDICT"):
        assert any(word in x for x in t), word


def test_scorecard_highlights_baseline_and_flags_pending(dryrun_out, tmp_path, capture):
    res = load_results(dryrun_out / "results.json")
    assets.scorecard(res, tmp_path)
    t = capture["scorecard"]
    assert t.count("BASELINE") == 1 and any(x.endswith("*") for x in t) and any("not scored yet" in x for x in t)
    assert "Baseline 27B (dry run)" in t


# ---------------------------------------------------------------- cost: n/a, never $0


def api_results(res, priced: bool):
    r = copy.deepcopy(res)
    for m in r["models"]:
        if m["name"] == "dry-cloud-flash":
            m["backend"] = "openai_compat"
    for run in r["runs"]:
        if run["model"] == "dry-cloud-flash" and not priced:
            run["cost_usd"] = None
    recompute_summary(r)
    return r


def test_unknown_cost_renders_na_never_zero(dryrun_out, tmp_path, capture):
    res = api_results(load_results(dryrun_out / "results.json"), priced=False)
    assert res["summary"]["dry-cloud-flash"]["cost_usd_total"] is None
    assets.render_all(res, tmp_path)
    card = capture["scorecard"]
    assert "n/a" in card
    assert not any(x.startswith("$") for x in card), "no dollar figure when the price is unknown"
    verdict = capture["verdict_dry-cloud-flash"]
    assert any("cost n/a" in x for x in verdict)
    assert not any("$" in x for x in verdict)
    assert not any("$" in x for x in capture["verdict_dry-baseline-27b"])


def test_known_cost_renders_dollars(dryrun_out, tmp_path, capture):
    res = api_results(load_results(dryrun_out / "results.json"), priced=True)
    assets.render_all(res, tmp_path)
    assert any(x.startswith("$0.0") for x in capture["scorecard"])
    assert any("cost $0.0" in x for x in capture["verdict_dry-cloud-flash"])


# ---------------------------------------------------------------- thumbnail text flag


def test_thumbnail_text_flag_and_fallback(dryrun_out, tmp_path, capture):
    a = tmp_path / "a"
    assert main(["render", "--results", str(dryrun_out / "results.json"), "--out", str(a), "--thumbnail-text", "Will it fit in 16 GB?"]) == 0
    joined = " ".join(capture["thumbnail_text"])
    assert "WILL IT FIT" in joined
    capture.clear()
    b = tmp_path / "b"
    assert main(["render", "--results", str(dryrun_out / "results.json"), "--out", str(b)]) == 0
    joined = " ".join(capture["thumbnail_text"])
    assert "DRY RUN AT THE" in joined, "falls back to results.title"


def test_thumbnail_text_can_come_from_results_json(dryrun_out, tmp_path, capture):
    res = load_results(dryrun_out / "results.json")
    res["thumbnail_text"] = "Custom from results"
    assets.render_all(res, tmp_path)
    assert "CUSTOM FROM" in " ".join(capture["thumbnail_text"])


def test_long_titles_wrap_inside_the_canvas():
    lines, size = assets.fit_text("A very long thumbnail headline about running a thirty five billion parameter model", max_lines=4, sizes=[150, 130, 112, 96, 84, 72, 60], char_em=0.8, avail_px=1680)
    assert len(lines) <= 4 and size <= 130
    assert max(len(l) for l in lines) * 0.8 * size * (100 / 72) <= 1680 * 1.05


# ---------------------------------------------------------------- degraded inputs


def test_render_without_speed_ladder_or_vram_does_not_crash(tmp_path):
    cfg = Path(__file__).resolve().parent.parent / "examples" / "instance-dryrun.yaml"
    out = tmp_path / "r"
    assert main(["run", "--config", str(cfg), "--out", str(out), "--tasks", "2"]) == 0
    res = load_results(out / "results.json")
    for m in res["models"]:
        m["vram_after_load_mb"] = None
    assert main(["render", "--results", str(out / "results.json"), "--out", str(tmp_path / "a")]) == 0
    assert (tmp_path / "a" / "tps_chart.png").stat().st_size > 1000


def test_render_with_a_single_model_and_unicode_name(tmp_path):
    res = {
        "schema_version": 1, "title": "Solo: Qwen/Ünïcode", "hardware": "box", "started_at": "x", "finished_at": "y", "kit_version": "0.1.0", "battery_version": "0.1.0",
        "models": [{"name": "hf.co/Org/Model-7B:Q4_K_M", "label": "", "backend": "ollama", "size_gb": 4.4, "vram_after_load_mb": 5200, "gpu_share_pct": 100, "load_seconds": 2.0, "quant": "Q4_K_M"}],
        "tasks": [{"id": 0, "name": "Specs & Load", "kind": "auto", "weight": 1.0}], "runs": [], "summary": {}, "verdicts": {},
    }
    m = assets.render_all(res, tmp_path)
    assert {f["file"] for f in m["files"]} >= {"verdict_hf-co-org-model-7b-q4-k-m.png", "lower_third_hf-co-org-model-7b-q4-k-m.png"}
    assert slug("hf.co/Org/Model-7B:Q4_K_M") == "hf-co-org-model-7b-q4-k-m"


def test_render_rejects_results_that_break_the_schema(tmp_path, capsys):
    bad = tmp_path / "r.json"
    bad.write_text(json.dumps({"schema_version": 2}))
    assert main(["render", "--results", str(bad), "--out", str(tmp_path / "a")]) == 2
    assert "schema" in capsys.readouterr().err
    assert main(["render", "--results", str(tmp_path / "nope.json"), "--out", str(tmp_path / "a")]) == 2
