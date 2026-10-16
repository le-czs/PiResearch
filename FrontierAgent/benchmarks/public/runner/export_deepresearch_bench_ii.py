#!/usr/bin/env python3
"""Export FrontierAgent trials as DeepResearch Bench II Markdown reports."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

from benchmarks.public.core.registry import dataset_root_for
from benchmarks.public.drb2_submission import select_submission

_SAFE_MODEL_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_canonical(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            row = json.loads(line)
            if not isinstance(row, dict) or "id" not in row or "question" not in row:
                raise ValueError(f"Invalid canonical row at {path}:{line_number}")
            rows.append(row)
    return rows


def _resolve_trials_dir(run_dir: Path) -> Path:
    if (run_dir / "trials").is_dir():
        return run_dir / "trials"
    runs = sorted(path for path in run_dir.glob("run_*") if (path / "trials").is_dir())
    if len(runs) == 1:
        return runs[0] / "trials"
    if len(runs) > 1:
        names = ", ".join(path.name for path in runs)
        raise ValueError(
            f"{run_dir} contains multiple runs ({names}); export one run_N directory at a time"
        )
    raise FileNotFoundError(f"No trials/ directory found under {run_dir}")


def export_run(
    run_dir: Path,
    output_root: Path,
    *,
    model_name: str = "frontier-agent",
    canonical_path: Path | None = None,
) -> dict[str, Any]:
    """Export non-empty predictions in canonical task order."""
    if not _SAFE_MODEL_NAME.fullmatch(model_name):
        raise ValueError(
            "model_name may contain only letters, numbers, dots, underscores, and hyphens"
        )

    run_dir = run_dir.resolve()
    trials_dir = _resolve_trials_dir(run_dir)
    canonical_path = canonical_path or (
        dataset_root_for("deepresearch_bench_ii") / "standardized_data.jsonl"
    )
    canonical = _load_canonical(canonical_path.resolve())

    results: dict[str, dict[str, Any]] = {}
    trial_dirs: dict[str, Path] = {}
    invalid_results: list[str] = []
    for result_path in sorted(trials_dir.glob("*/result.json")):
        try:
            result = _load_json(result_path)
            qid = str(result.get("question_id", result_path.parent.name)).strip()
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            invalid_results.append(str(result_path))
            continue
        if not qid:
            invalid_results.append(str(result_path))
            continue
        if qid in results:
            raise ValueError(f"Duplicate DeepResearch Bench II result id: {qid}")
        results[qid] = result
        trial_dirs[qid] = result_path.parent

    model_dir = output_root.resolve() / model_name
    model_dir.mkdir(parents=True, exist_ok=True)
    canonical_ids = {str(row["id"]) for row in canonical}
    unknown_ids = sorted(set(results) - canonical_ids, key=lambda value: int(value))
    missing_ids: list[str] = []
    empty_ids: list[str] = []
    exported_ids: list[str] = []

    for row in canonical:
        qid = str(row["id"])
        result = results.get(qid)
        if result is None:
            missing_ids.append(qid)
            continue
        chat = str(result.get("predicted_answer") or "").strip()
        trial_dir = trial_dirs.get(qid, trials_dir / qid)
        answer, _source = select_submission(chat, trial_dir / "sandbox" / "outputs")
        if not answer:
            empty_ids.append(qid)
            continue
        report_path = model_dir / f"idx-{qid}.md"
        report_path.write_text(answer.rstrip() + "\n", encoding="utf-8")
        exported_ids.append(qid)

    return {
        "run_dir": str(run_dir),
        "output_dir": str(model_dir),
        "canonical": len(canonical),
        "exported": len(exported_ids),
        "exported_ids": exported_ids,
        "missing_ids": missing_ids,
        "empty_ids": empty_ids,
        "unknown_ids": unknown_ids,
        "invalid_results": invalid_results,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir", type=Path, help="Run directory containing trials/")
    parser.add_argument("--out", type=Path, required=True, help="Report root directory")
    parser.add_argument("--model-name", default="frontier-agent")
    parser.add_argument("--canonical", type=Path, default=None)
    parser.add_argument(
        "--allow-partial",
        action="store_true",
        help="Allow missing or empty reports. Partial scores are not leaderboard-comparable.",
    )
    args = parser.parse_args()

    summary = export_run(
        args.run_dir,
        args.out,
        model_name=args.model_name,
        canonical_path=args.canonical,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if summary["invalid_results"] or summary["unknown_ids"]:
        raise SystemExit(2)
    incomplete = len(summary["missing_ids"]) + len(summary["empty_ids"])
    if incomplete and not args.allow_partial:
        print(
            f"ERROR: {incomplete} of {summary['canonical']} tasks have no usable report. "
            "Finish/resume generation, or pass --allow-partial for a smoke score.",
            file=sys.stderr,
        )
        raise SystemExit(3)


if __name__ == "__main__":
    main()
