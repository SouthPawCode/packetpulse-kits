"""python -m llm_bench {preflight,run,score,render,validate}     (render --pre, alias pre-render: assets before the run)

Exit codes: 0 success, 2 failure (one-line reason on stderr), 3 the GPU is busy with a
non-benchmark model (preflight and run, unless --force).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence

from . import __version__
from .battery import load_battery, validate_battery
from .config import ConfigError, force_dry_run, load_instance
from .preflight import EXIT_FAIL, EXIT_GPU_BUSY, EXIT_OK, loaded_foreign_models, run_preflight

PROG = "python -m llm_bench"


def _err(msg: str) -> None:
    print(f"llm-bench: {msg}", file=sys.stderr)


def _pre_options(p: argparse.ArgumentParser, *, flag: bool) -> None:
    """Options of `render --pre` (and its alias `pre-render`)."""
    if flag:
        p.add_argument("--pre", action="store_true", help="render only what needs no results.json, from --config (opening, round, title, lower thirds, question, end and thumbnail cards)")
    p.add_argument("--config", default=None, metavar="instance.yaml", help="the instance to render from (required with --pre)")
    p.add_argument("--question", default=None, metavar="TEXT", help="the episode's question, for question_card.png (default: a placeholder)")
    p.add_argument("--round", dest="round_no", type=int, default=None, metavar="N", help="Proving Ground round number for round_card.png (default: the title card text)")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog=PROG, description="The Packet Pulse Proving Ground: a fixed benchmark battery for language models.")
    p.add_argument("--version", action="version", version=f"llm-bench {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True, metavar="{preflight,run,score,render,pre-render,validate}")

    pf = sub.add_parser("preflight", help="check endpoints, models, memory, the GPU guard and docker; prints a JSON report")
    pf.add_argument("--config", required=True, metavar="instance.yaml")
    pf.add_argument("--force", action="store_true", help="continue even if a non-benchmark model is loaded (exit 3 otherwise)")
    pf.add_argument("--dry-run", action="store_true", help="use the dryrun backend for every model; no network")

    rn = sub.add_parser("run", help="run the battery for every model; writes results.json incrementally")
    rn.add_argument("--config", required=True, metavar="instance.yaml")
    rn.add_argument("--out", default="results/", metavar="DIR")
    rn.add_argument("--tasks", default="", metavar="0,1,2", help="comma-separated task ids (default: all)")
    rn.add_argument("--force", action="store_true", help="run even if a non-benchmark model is loaded on the GPU")
    rn.add_argument("--dry-run", action="store_true", help="use the dryrun backend for every model; no network")

    sc = sub.add_parser("score", help="merge Mike's rubric scores (tasks 3, 9, 10) into results.json")
    sc.add_argument("--results", required=True, metavar="results/results.json")
    sc.add_argument("--rubric", required=True, metavar="results/rubric.yaml")

    rd = sub.add_parser("render", help="write the assets (PNG 1920x1080, SVG) and manifest.json; with --pre, the ones that need no results")
    rd.add_argument("--results", default=None, metavar="results/results.json", help="the run's results (required unless --pre)")
    rd.add_argument("--out", default=None, metavar="DIR", help="default: assets/, or assets-pre/ with --pre")
    rd.add_argument("--thumbnail-text", default=None, metavar="TEXT", help="text for thumbnail_text.png (default: results.thumbnail_text or the title)")
    _pre_options(rd, flag=True)

    pr = sub.add_parser("pre-render", help="alias of `render --pre`: the assets that need no results, from instance.yaml alone")
    pr.add_argument("--out", default=None, metavar="DIR", help="default: assets-pre/")
    pr.add_argument("--thumbnail-text", default=None, metavar="TEXT", help="text for thumbnail_text.png (default: the instance title)")
    _pre_options(pr, flag=False)

    va = sub.add_parser("validate", help="schema-check the instance and the battery; no network")
    va.add_argument("--config", required=True, metavar="instance.yaml")
    va.add_argument("--results", default=None, metavar="results.json", help="also validate a results file against the schema")
    return p


def _parse_tasks(s: str) -> list[int]:
    if not s.strip():
        return []
    try:
        return [int(x) for x in s.split(",") if x.strip() != ""]
    except ValueError:
        raise ConfigError(f"--tasks must be comma-separated integers, got {s!r}") from None


def cmd_validate(a: argparse.Namespace) -> int:
    inst = load_instance(a.config)
    battery = load_battery(inst)
    errs = validate_battery(battery)
    if errs:
        for e in errs:
            _err(e)
        return EXIT_FAIL
    if a.results:
        from .results import load_results, validate_results

        try:
            data = load_results(a.results)
        except (OSError, ValueError) as e:
            _err(f"cannot read results: {e}")
            return EXIT_FAIL
        rerrs = validate_results(data)
        if rerrs:
            _err("results.json does not match the schema: " + "; ".join(rerrs[:3]))
            return EXIT_FAIL
    print(f"ok: instance {inst.title!r} ({len(inst.models)} models, baseline {inst.baseline or 'none'}), battery {battery.name} v{battery.version} ({len(battery.tasks)} tasks)")
    return EXIT_OK


def cmd_preflight(a: argparse.Namespace) -> int:
    inst = load_instance(a.config)
    if a.dry_run:
        force_dry_run(inst)
    code, report = run_preflight(inst, force=a.force)
    print(json.dumps(report, indent=2))
    if code != EXIT_OK:
        _err(report["reason"])
    return code


def cmd_run(a: argparse.Namespace) -> int:
    from .runner import Runner

    inst = load_instance(a.config)
    if a.dry_run:
        force_dry_run(inst)
    battery = load_battery(inst)
    errs = validate_battery(battery)
    if errs:
        _err(errs[0])
        return EXIT_FAIL
    ids = _parse_tasks(a.tasks)
    battery.select(ids)  # raises ConfigError on unknown ids
    if not a.force:
        foreign, _ = loaded_foreign_models(inst)
        if foreign:
            names = ", ".join(f"{f['model']} @ {f['endpoint']}" for f in foreign)
            _err(f"non-benchmark model loaded ({names}); the GPU is shared with production. Rerun in the planned window or pass --force")
            return EXIT_GPU_BUSY
    runner = Runner(inst, battery, Path(a.out), task_ids=ids or None)
    results = runner.run()
    rows = results["runs"]
    print(json.dumps({"results": str(Path(a.out) / "results.json"), "runs": len(rows), "summary": results["summary"]}, indent=2))
    if rows and all(r.get("error") for r in rows):
        _err("every run failed; see results.json for the errors")
        return EXIT_FAIL
    return EXIT_OK


def cmd_score(a: argparse.Namespace) -> int:
    from .results import load_results, write_results
    from .scoring.rubric import RubricError, apply_rubric, load_rubric

    try:
        results = load_results(a.results)
    except (OSError, ValueError) as e:
        _err(f"cannot read results: {e}")
        return EXIT_FAIL
    try:
        outcome = apply_rubric(results, load_rubric(Path(a.rubric)))
    except RubricError as e:
        _err(str(e))
        return EXIT_FAIL
    write_results(results, Path(a.results))
    for w in outcome["warnings"]:
        print(f"warning: {w}", file=sys.stderr)
    print(json.dumps({"applied": outcome["applied"], "skipped_unscored": outcome["skipped"], "summary": results["summary"]}, indent=2))
    return EXIT_OK


def cmd_render(a: argparse.Namespace) -> int:
    if a.cmd == "pre-render" or getattr(a, "pre", False):
        return cmd_render_pre(a)
    from .render.assets import render_all
    from .results import load_results, validate_results

    if not a.results:
        _err("render needs --results results.json (or --pre --config instance.yaml for the assets that need no results)")
        return EXIT_FAIL
    try:
        results = load_results(a.results)
    except (OSError, ValueError) as e:
        _err(f"cannot read results: {e}")
        return EXIT_FAIL
    errs = validate_results(results)
    if errs:
        _err("results.json does not match the schema: " + "; ".join(errs[:3]))
        return EXIT_FAIL
    out = a.out or "assets/"
    manifest = render_all(results, Path(out), thumbnail_text=a.thumbnail_text, results_path=a.results)
    print(json.dumps({"assets": str(Path(out)), "files": len(manifest["files"])}, indent=2))
    return EXIT_OK


def cmd_render_pre(a: argparse.Namespace) -> int:
    from .render.pre import render_pre

    if not a.config:
        _err("render --pre needs --config instance.yaml")
        return EXIT_FAIL
    if a.round_no is not None and a.round_no < 1:
        _err("--round must be 1 or more")
        return EXIT_FAIL
    inst = load_instance(a.config)
    out = a.out or "assets-pre/"
    manifest = render_pre(inst, Path(out), question=a.question, round_no=a.round_no, thumbnail_text=a.thumbnail_text, config_path=a.config)
    print(json.dumps({"assets": str(Path(out)), "files": len(manifest["files"]), "phase": "pre"}, indent=2))
    return EXIT_OK


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    handlers = {"validate": cmd_validate, "preflight": cmd_preflight, "run": cmd_run, "score": cmd_score, "render": cmd_render, "pre-render": cmd_render}
    try:
        return handlers[args.cmd](args)
    except ConfigError as e:
        _err(str(e))
        return EXIT_FAIL
    except KeyboardInterrupt:
        _err("interrupted; partial results were kept")
        return 130
    except BrokenPipeError:
        return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
