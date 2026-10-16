from __future__ import annotations

import json
from pathlib import Path

import pytest

from benchmarks.public.core.registry import DatasetConfig, get_config
from benchmarks.public.runner.export_deepresearch_bench_ii import export_run
from benchmarks.public.runner.prepare_deepresearch_bench_ii_scoring import (
    stage_pending_reports,
)
from benchmarks.public.runner.run_subprocess import _load_resumable_result
from benchmarks.public.scripts.prepare_deepresearch_bench_ii import prepare_dataset


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )


def test_registry_declares_external_report_scoring():
    config = get_config("deepresearch_bench_ii")

    assert isinstance(config, DatasetConfig)
    assert config.answer_field == ""
    assert config.scoring_mode == "external"
    assert config.default_answer_type == "report"


def test_prepare_dataset_excludes_hidden_grading_data(tmp_path: Path):
    source = tmp_path / "tasks_and_rubrics.jsonl"
    output = tmp_path / "standardized_data.jsonl"
    _write_jsonl(
        source,
        [
            {
                "idx": 1,
                "prompt": "Research question one",
                "theme": "science",
                "language": "en",
                "content": {"rubric": {"secret": True}, "blocked": ["hidden"]},
            },
            {
                "idx": 2,
                "prompt": "研究问题二",
                "theme": "finance",
                "language": "zh",
                "content": {"rubric": {"secret": True}, "blocked": ["hidden"]},
            },
        ],
    )

    summary = prepare_dataset(source, output, expected_count=2)
    rows = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]

    assert summary["tasks"] == 2
    assert rows[0] == {
        "id": "1",
        "question": "Research question one",
        "answer_type": "report",
        "category": "science",
        "language": "en",
    }
    serialized = output.read_text(encoding="utf-8")
    assert "rubric" not in serialized
    assert "blocked" not in serialized
    assert "secret" not in serialized


def test_prepare_dataset_rejects_wrong_task_count(tmp_path: Path):
    source = tmp_path / "source.jsonl"
    _write_jsonl(source, [{"idx": 1, "prompt": "question"}])

    with pytest.raises(ValueError, match="Expected 132"):
        prepare_dataset(source, tmp_path / "out.jsonl")


def test_exporter_writes_official_report_names_and_reports_partial(tmp_path: Path):
    canonical = tmp_path / "standardized_data.jsonl"
    _write_jsonl(
        canonical,
        [
            {"id": "1", "question": "one"},
            {"id": "2", "question": "two"},
        ],
    )
    run_dir = tmp_path / "run"
    trial = run_dir / "trials" / "1"
    trial.mkdir(parents=True)
    (trial / "result.json").write_text(
        json.dumps({"question_id": "1", "predicted_answer": "# Full report"}),
        encoding="utf-8",
    )

    reports = tmp_path / "reports"
    summary = export_run(
        run_dir,
        reports,
        model_name="frontier-test",
        canonical_path=canonical,
    )

    assert summary["exported"] == 1
    assert summary["missing_ids"] == ["2"]
    assert (reports / "frontier-test" / "idx-1.md").read_text() == "# Full report\n"
    assert not (reports / "frontier-test" / "idx-2.md").exists()


def test_exporter_rejects_unsafe_model_name(tmp_path: Path):
    run_dir = tmp_path / "run"
    (run_dir / "trials").mkdir(parents=True)
    canonical = tmp_path / "standardized_data.jsonl"
    _write_jsonl(canonical, [])

    with pytest.raises(ValueError, match="model_name"):
        export_run(
            run_dir,
            tmp_path / "reports",
            model_name="../escape",
            canonical_path=canonical,
        )


def test_scoring_stage_skips_existing_verdicts(tmp_path: Path):
    reports = tmp_path / "reports"
    model_dir = reports / "frontier-agent"
    model_dir.mkdir(parents=True)
    (model_dir / "idx-1.md").write_text("one", encoding="utf-8")
    (model_dir / "idx-2.md").write_text("two", encoding="utf-8")
    judged = tmp_path / "judge.jsonl"
    _write_jsonl(judged, [{"model": "frontier-agent", "idx": 1, "result": {}}])

    summary = stage_pending_reports(reports, judged, tmp_path / "pending")

    assert summary["already_judged"] == 1
    assert summary["pending"] == 1
    assert not (tmp_path / "pending" / "frontier-agent" / "idx-1.md").exists()
    assert (tmp_path / "pending" / "frontier-agent" / "idx-2.md").exists()


def test_external_resume_retries_failures_without_a_report(tmp_path: Path):
    result_path = tmp_path / "result.json"
    result_path.write_text(
        json.dumps({"question_id": "1", "error": "worker_exit_1_no_result"}),
        encoding="utf-8",
    )

    assert _load_resumable_result(result_path, external_scoring=True) is None
    assert _load_resumable_result(result_path, external_scoring=False) is not None

    result_path.write_text(
        json.dumps({"question_id": "1", "predicted_answer": "complete report"}),
        encoding="utf-8",
    )
    assert _load_resumable_result(result_path, external_scoring=True) is not None
