#!/usr/bin/env python3
"""Build a prompt-only DeepResearch Bench II dataset for agent collection.

The official source also contains rubrics and blocked references. Those fields
are deliberately excluded so the research agent cannot inspect hidden grading
data. Keep the original source outside FrontierAgent's dataset directory and
give it only to the external evaluator.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any

_FRONTIER_ROOT = Path(__file__).resolve().parents[3]
_WORKSPACE_ROOT = _FRONTIER_ROOT.parent
DEFAULT_SOURCE = _WORKSPACE_ROOT / "drb2-evaluator" / "tasks_and_rubrics.jsonl"
DEFAULT_OUTPUT = (
    _FRONTIER_ROOT
    / "benchmarks"
    / "public"
    / "datasets"
    / "deepresearch_bench_ii"
    / "standardized_data.jsonl"
)


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSON at {path}:{line_number}: {exc}") from exc
            if not isinstance(row, dict):
                raise ValueError(f"Expected an object at {path}:{line_number}")
            rows.append(row)
    return rows


def prepare_dataset(
    source: Path,
    output: Path,
    *,
    expected_count: int | None = 132,
) -> dict[str, Any]:
    """Write an atomically replaced prompt-only JSONL and return its summary."""
    source = source.resolve()
    output = output.resolve()
    if source == output:
        raise ValueError("Source and output must be different files")

    source_rows = _load_jsonl(source)
    prepared: list[dict[str, str]] = []
    seen_ids: set[str] = set()

    for row_number, row in enumerate(source_rows, start=1):
        raw_id = row.get("idx")
        prompt = row.get("prompt")
        if raw_id is None:
            raise ValueError(f"Source row {row_number} has no idx")
        qid = str(raw_id).strip()
        if not qid or not qid.isdigit():
            raise ValueError(f"Source row {row_number} has invalid idx: {raw_id!r}")
        if qid in seen_ids:
            raise ValueError(f"Duplicate idx in source: {qid}")
        if not isinstance(prompt, str) or not prompt.strip():
            raise ValueError(f"Source row {row_number} has an empty prompt")
        seen_ids.add(qid)
        prepared.append(
            {
                "id": qid,
                "question": prompt,
                "answer_type": "report",
                "category": str(row.get("theme") or ""),
                "language": str(row.get("language") or ""),
            }
        )

    if expected_count is not None and len(prepared) != expected_count:
        raise ValueError(
            f"Expected {expected_count} DeepResearch Bench II tasks, found {len(prepared)}"
        )

    output.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(
        dir=output.parent,
        prefix=f".{output.name}.",
        suffix=".tmp",
        text=True,
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            for row in prepared:
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, output)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(temporary_name)
        raise

    return {
        "source": str(source),
        "output": str(output),
        "tasks": len(prepared),
        "fields": sorted(prepared[0]) if prepared else [],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--expected-count",
        type=int,
        default=132,
        help="Expected number of tasks; set 0 to accept any non-empty count.",
    )
    args = parser.parse_args()

    expected = args.expected_count or None
    summary = prepare_dataset(args.source, args.out, expected_count=expected)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
