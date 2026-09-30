"""`render --pre`: the assets that need no results, rendered from instance.yaml alone."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from PIL import Image

from llm_bench.cli import main
from llm_bench.config import load_instance
from llm_bench.render import assets, pre, style as S
from llm_bench.textutil import slug

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"
EXAMPLE = EXAMPLES / "instance.yaml"
QUESTION = "can a thirty-five-billion mixture-of-experts beat the twenty-seven-billion model I already run"

MODEL_NAMES = ["qwen3.8-27B-gsq-rco:64k", "hf.co/bartowski/Ornith-1.5-35B-A3B-GGUF:Q4_K_M", "deepseek-v4.1-flash"]
SLUGS = [slug(n) for n in MODEL_NAMES]
ALWAYS = ["opening_vitals.png", "round_card.png", "title_card.png", "lower_third_mike.png", "lower_third_mike_card.png",
          "question_card.png", "end_card.png", "thumbnail_text.png"]
MODEL_FILES = [f"lower_third_{s}{suffix}.png" for s in SLUGS for suffix in ("", "_card")]


@pytest.fixture(scope="module")
def rendered(tmp_path_factory):
    out = tmp_path_factory.mktemp("pre")
    rc = main(["render", "--pre", "--config", str(EXAMPLE), "--out", str(out), "--round", "4", "--question", QUESTION,
               "--thumbnail-text", "Will it fit in 16 GB?"])
    assert rc == 0
    return out


@pytest.fixture
def capture(monkeypatch):
    """Every text drawn on each card, keyed by output file name."""
    seen: dict[str, list[str]] = {}
    orig = S.save

    def spy(fig, base, **kw):
        seen[Path(str(base)).name] = [t.get_text() for ax in fig.axes for t in ax.texts]
        return orig(fig, base, **kw)

    monkeypatch.setattr(S, "save", spy)
    return seen


def manifest_of(d: Path) -> dict:
    return json.loads((d / "manifest.json").read_text())


def test_every_pre_file_exists_at_1920x1080(rendered):
    files = {p.name for p in rendered.iterdir()}
    for f in ALWAYS + MODEL_FILES + ["manifest.json"]:
        assert f in files, f
    for f in ALWAYS + MODEL_FILES:
        with Image.open(rendered / f) as im:
            assert im.size == (1920, 1080), f
        assert (rendered / f).stat().st_size > 1000, f


def test_manifest_lists_every_file_with_a_role(rendered):
    m = manifest_of(rendered)
    assert m["phase"] == "pre" and m["kit_version"] == "0.2.0" and m["round"] == 4
    assert m["handle"] == "@packetpulsedev" and m["canvas"] == {"width": 1920, "height": 1080}
    assert m["hardware"].startswith("RTX 4070 Ti SUPER 16 GB") and m["title"] == "Ornith 1.5 at the Proving Ground"
    assert m["question"].startswith("Can a thirty-five-billion") and m["question"].endswith("?")
    listed = {f["file"]: f for f in m["files"]}
    assert set(listed) == {p.name for p in rendered.iterdir()} - {"manifest.json"}
    assert all(f["role"] for f in listed.values())
    roles = {f["role"] for f in listed.values()}
    assert {"opening_vitals", "round_card", "title_card", "lower_third_mike", "lower_third_mike_preview", "lower_third",
            "lower_third_preview", "question_card", "end_card", "thumbnail_text"} <= roles
    for name, f in listed.items():
        assert f["bytes"] == (rendered / name).stat().st_size and [f["width"], f["height"]] == [1920, 1080]
        assert f["format"] == "png" and f["phase"] == "pre"
    per_model = sorted(f["model"] for f in listed.values() if f["role"] == "lower_third")
    assert per_model == sorted(MODEL_NAMES)
    assert listed["lower_third_mike.png"]["transparent"] is True and listed["lower_third_mike_card.png"]["transparent"] is False
    assert listed["lower_third_mike.png"]["model"] is None


def test_needs_no_results_json(tmp_path):
    cfg = tmp_path / "instance.yaml"
    cfg.write_text(EXAMPLE.read_text())
    assert main(["pre-render", "--config", str(cfg), "--out", str(tmp_path / "a")]) == 0  # the alias
    assert not list(tmp_path.glob("**/results.json"))
    assert (tmp_path / "a" / "opening_vitals.png").is_file()


def test_lower_thirds_are_transparent_overlays(rendered):
    for f in ["lower_third_mike.png"] + [f"lower_third_{s}.png" for s in SLUGS]:
        with Image.open(rendered / f) as im:
            assert im.mode == "RGBA"
            assert im.getpixel((10, 10))[3] == 0 and im.getpixel((1800, 500))[3] == 0
            assert im.getpixel((600, 900))[3] > 200
    for f in ["lower_third_mike_card.png"] + [f"lower_third_{s}_card.png" for s in SLUGS]:
        with Image.open(rendered / f) as im:
            assert im.convert("RGBA").getpixel((10, 10))[3] == 255


def test_opening_frame_has_a_pulse_trace_in_the_lower_third(rendered):
    with Image.open(rendered / "opening_vitals.png") as im:
        rgb = im.convert("RGB")
        assert rgb.getpixel((900, 20)) == (15, 21, 25), "dark card"
        assert rgb.getpixel((5, 500)) == (14, 107, 103), "brand teal bar"
        teal_rows = [y for y in range(720, 1080) if any(abs(rgb.getpixel((x, y))[1] - 194) < 24 and rgb.getpixel((x, y))[0] < 120 for x in range(0, 1920, 6))]
        assert teal_rows, "the trace is drawn in the lower third"
        assert max(teal_rows) - min(teal_rows) > 100, "flat line plus one spike"


def test_texts_on_the_cards(capture, tmp_path):
    inst = load_instance(EXAMPLE)
    pre.render_pre(inst, tmp_path, question=QUESTION, round_no=2, thumbnail_text="Will it fit?", config_path=EXAMPLE)
    hw = inst.hardware
    # vitals strip: the hardware line split into segments, GPU and VRAM apart
    segs = S.vitals_segments(hw)
    assert segs == ["RTX 4070 Ti SUPER", "16 GB VRAM", "64 GB RAM", "Ollama"]
    for card in ("opening_vitals", "end_card"):
        joined = " ".join(capture[card])
        assert all(s in capture[card] for s in segs) or all(s in joined for s in segs), card
        assert "PACKET " in capture[card] and "PULSE" in capture[card]
    assert {"github.com/SouthPawCode/packetpulse-kits", "packetpulse.dev", "@packetpulsedev"} <= set(capture["end_card"])
    assert "Proving Ground · Round 2" in capture["round_card"] and inst.title in capture["round_card"]
    assert "Mike" in capture["lower_third_mike"] and "Packet Pulse" in capture["lower_third_mike"]
    assert "Ornith 1.5 35B-A3B" in capture[f"lower_third_{SLUGS[1]}"]
    assert "35B (3B active)  \u00b7  Q4_K_M  \u00b7  local" in capture[f"lower_third_{SLUGS[1]}"]
    assert "DeepSeek V4.1 Flash (API)" in capture[f"lower_third_{SLUGS[2]}"] and "API" in capture[f"lower_third_{SLUGS[2]}"][1]
    assert "WILL IT FIT?" in " ".join(capture["thumbnail_text"])
    assert inst.title in " ".join(capture["title_card"]) and hw in capture["title_card"]
    qc = capture["question_card"]
    assert "THIS WEEK'S QUESTION" in qc and "@packetpulsedev" in qc and hw in qc
    assert "Can a thirty-five-billion" in " ".join(qc) and "model I already run?" in " ".join(qc)


def test_round_card_without_a_round_uses_the_title_and_packet_pulse(capture, tmp_path):
    inst = load_instance(EXAMPLE)
    pre.render_pre(inst, tmp_path)
    assert "Packet Pulse" in capture["round_card"]
    assert not any("Round" in t for t in capture["round_card"])
    assert " ".join(capture["round_card"]).count("Ornith") >= 1
    assert manifest_of(tmp_path)["round"] is None and manifest_of(tmp_path)["question"] is None


def test_question_placeholder_and_thumbnail_fallback(capture, tmp_path):
    pre.render_pre(load_instance(EXAMPLE), tmp_path)
    assert "This week's question" in capture["question_card"]
    assert "THIS WEEK'S QUESTION" not in capture["question_card"], "no label over the placeholder"
    assert "ORNITH 1.5 AT THE" in " ".join(capture["thumbnail_text"]), "falls back to the instance title"


def test_question_text_polish():
    assert pre.question_text("can it fit") == ("Can it fit?", True)
    assert pre.question_text("Is it 9B?") == ("Is it 9B?", True)
    assert pre.question_text("  ") == ("This week's question", False)
    assert pre.question_text(None) == ("This week's question", False)


def test_name_parsing():
    assert pre.quant_of("hf.co/bartowski/Ornith-1.5-35B-A3B-GGUF:Q4_K_M") == "Q4_K_M"
    assert pre.quant_of("model:iq3_s") == "IQ3_S"
    assert pre.quant_of("qwen3.8-27B-gsq-rco:64k") is None
    assert pre.params_label("hf.co/bartowski/Ornith-1.5-35B-A3B-GGUF:Q4_K_M") == "35B (3B active)"
    assert pre.params_label("qwen3.8-27B-gsq-rco:64k") == "27B"
    assert pre.params_label("deepseek-v4.1-flash") is None


def test_hardware_segments():
    assert S.vitals_segments("RTX 4070 Ti SUPER 16 GB · 64 GB RAM · Ollama") == ["RTX 4070 Ti SUPER", "16 GB VRAM", "64 GB RAM", "Ollama"]
    assert S.vitals_segments("Mac Studio · 128 GB unified") == ["Mac Studio", "128 GB unified"]
    assert S.vitals_segments("64 GB RAM") == ["64 GB RAM"]


def test_no_results_in_the_pre_run_code():
    src = Path(pre.__file__).read_text()
    import re
    assert not re.search(r"#[0-9A-Fa-f]{6}", src), "colors belong in render/style.py"
    assert "load_results" not in src and "validate_results" not in src


def test_render_pre_needs_a_config(capsys):
    assert main(["render", "--pre", "--out", "x"]) == 2
    assert "--config" in capsys.readouterr().err
    assert main(["render", "--out", "x"]) == 2
    assert "--results" in capsys.readouterr().err
    assert main(["render", "--pre", "--config", str(EXAMPLE), "--round", "0", "--out", "x"]) == 2


def test_missing_config_is_a_one_line_error(tmp_path, capsys):
    assert main(["render", "--pre", "--config", str(tmp_path / "nope.yaml"), "--out", str(tmp_path / "a")]) == 2
    assert "cannot read" in capsys.readouterr().err


def test_a_model_named_like_a_reserved_file_does_not_collide(tmp_path):
    inst = load_instance(EXAMPLES / "instance-dryrun.yaml")
    inst.models[0].name = "Mike"
    m = pre.render_pre(inst, tmp_path)
    names = [f["file"] for f in m["files"]]
    assert len(names) == len(set(names))
    assert "lower_third_mike-2.png" in names and "lower_third_mike.png" in names


# ------------------------------------------------ pre and post side by side


def test_post_run_render_leaves_pre_files_alone_in_the_same_dir(dryrun_out, tmp_path):
    inst = load_instance(EXAMPLES / "instance-dryrun.yaml")
    assert pre.render_pre(inst, tmp_path, question="does it fit", thumbnail_text="PRE THUMB")
    before = {f: (tmp_path / f).read_bytes() for f in ("title_card.png", "thumbnail_text.png", "lower_third_dry-moe-35b.png", "opening_vitals.png")}
    assert main(["render", "--results", str(dryrun_out / "results.json"), "--out", str(tmp_path), "--thumbnail-text", "POST THUMB"]) == 0
    for f, data in before.items():
        assert (tmp_path / f).read_bytes() == data, f"{f} was overwritten by the post-run render"
    m = manifest_of(tmp_path)
    by = {f["file"]: f for f in m["files"]}
    assert by["opening_vitals.png"]["phase"] == "pre" and "phase" not in by["scorecard.png"]
    assert {"scorecard.png", "matrix.png", "verdict_dry-moe-35b.png", "question_card.png", "end_card.png"} <= set(by)
    assert {p.name for p in tmp_path.iterdir()} - {"manifest.json"} == set(by), "manifest lists every file, and only real files"
    # a second post-run render still respects them
    assert main(["render", "--results", str(dryrun_out / "results.json"), "--out", str(tmp_path)]) == 0
    assert (tmp_path / "thumbnail_text.png").read_bytes() == before["thumbnail_text.png"]


def test_pre_render_into_a_post_dir_keeps_post_entries(dryrun_out, tmp_path):
    assert main(["render", "--results", str(dryrun_out / "results.json"), "--out", str(tmp_path)]) == 0
    assert pre.render_pre(load_instance(EXAMPLES / "instance-dryrun.yaml"), tmp_path)
    by = {f["file"]: f for f in manifest_of(tmp_path)["files"]}
    assert {"scorecard.png", "tps_chart.png", "opening_vitals.png", "lower_third_mike.png"} <= set(by)
    assert by["title_card.png"]["phase"] == "pre", "the pre version now owns the shared name"


def test_post_run_manifest_is_unchanged_in_a_clean_dir(dryrun_out, tmp_path):
    assert main(["render", "--results", str(dryrun_out / "results.json"), "--out", str(tmp_path)]) == 0
    m = manifest_of(tmp_path)
    assert "phase" not in m and all("phase" not in f for f in m["files"])
    assert "opening_vitals.png" not in {f["file"] for f in m["files"]}
