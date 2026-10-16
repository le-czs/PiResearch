"""DeepResearch Bench II — prompt-only collection with external scoring."""

from __future__ import annotations

from benchmarks.public.core.registry import DatasetConfig

CONFIGS: list[tuple[str, DatasetConfig]] = [
    (
        "deepresearch_bench_ii",
        DatasetConfig(
            name="DeepResearch Bench II",
            key="deepresearch_bench_ii",
            answer_field="",
            scoring_mode="external",
            default_answer_type="report",
            default_pipeline="agent_team_report",
            extra_metadata_fields=("language",),
        ),
    ),
]
