#!/usr/bin/env python3
"""Stage only not-yet-judged DRB2 reports for resumable external scoring."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any

_REPORT_NAME = re.compile(r"^idx-(\d+)\.md$", re.IGNORECASE)


def _judged_pairs(path: Path) -> tuple[set[tuple[str, int]], list[int]]:
    pairs: set[tuple[str, int]] = set()
    invalid_lines: list[int] = []
    if not path.exists():
        return pairs, invalid_lines
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
                model = str(row["model"])
                idx = int(row["idx"])
            except (json.JSONDecodeError, KeyError, TypeError, ValueError):
                invalid_lines.append(line_number)
                continue
            pairs.add((model, idx))
    return pairs, invalid_lines


def stage_pending_reports(
    reports_root: Path,
    judged_jsonl: Path,
    output_root: Path,
) -> dict[str, Any]:
    """Copy reports without an existing (model, idx) verdict to a clean tree."""
    reports_root = reports_root.resolve()
    judged_jsonl = judged_jsonl.resolve()
    output_root = output_root.resolve()
    judged, invalid_lines = _judged_pairs(judged_jsonl)

    reports: list[tuple[str, int, Path]] = []
    for model_dir in sorted(path for path in reports_root.iterdir() if path.is_dir()):
        for report_path in sorted(model_dir.glob("idx-*.md")):
            match = _REPORT_NAME.fullmatch(report_path.name)
            if match:
                reports.append((model_dir.name, int(match.group(1)), report_path))

    pending = [item for item in reports if (item[0], item[1]) not in judged]
    output_root.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(
        tempfile.mkdtemp(dir=output_root.parent, prefix=f".{output_root.name}.")
    )
    try:
        for model, _idx, source in pending:
            target_dir = temporary / model
            target_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target_dir / source.name)
        if output_root.exists():
            shutil.rmtree(output_root)
        temporary.replace(output_root)
    except BaseException:
        shutil.rmtree(temporary, ignore_errors=True)
        raise

    return {
        "reports": len(reports),
        "already_judged": len(reports) - len(pending),
        "pending": len(pending),
        "pending_pairs": [{"model": model, "idx": idx} for model, idx, _ in pending],
        "invalid_judge_lines": invalid_lines,
        "output_dir": str(output_root),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reports", type=Path, required=True)
    parser.add_argument("--judged", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    summary = stage_pending_reports(args.reports, args.judged, args.out)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if summary["invalid_judge_lines"]:
        raise SystemExit(
            "Judge JSONL contains invalid lines; repair it before resuming to avoid duplicate scores"
        )


if __name__ == "__main__":
    main()
