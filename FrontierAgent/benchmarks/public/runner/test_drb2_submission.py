from __future__ import annotations

from pathlib import Path

from benchmarks.public.drb2_submission import (
    coverage_items_from_prompt,
    looks_like_pointer,
    persist_selected_report,
    select_submission,
)
from benchmarks.public.sandbox_profiles import apply_sandbox_profile, resolve_closed_book


def test_pointer_detection():
    assert looks_like_pointer("报告已完成并写入 `/outputs/report.md`。")
    assert looks_like_pointer("The report is complete and ready. No further action is required.")
    assert not looks_like_pointer("# 土地财政\n\n" + ("段落\n" * 200))


def test_selects_longest_markdown_and_ignores_scratch(tmp_path: Path):
    outputs = tmp_path / "outputs"
    outputs.mkdir()
    (outputs / "notes.txt").write_text("scratch", encoding="utf-8")
    (outputs / "nested").mkdir()
    body = "# Report\n" + ("data\n" * 50)
    (outputs / "nested" / "report.md").write_text(body, encoding="utf-8")

    chosen, source = select_submission("报告已完成并写入 `/outputs/nested/report.md`。", outputs)
    assert source == "sandbox"
    assert chosen.startswith("# Report")
    assert "data" in chosen


def test_keeps_longer_inline_report(tmp_path: Path):
    outputs = tmp_path / "outputs"
    outputs.mkdir()
    (outputs / "short.md").write_text("# file\n", encoding="utf-8")
    inline = "# Inline\n\n" + ("section\n" * 80)
    chosen, source = select_submission(inline, outputs)
    assert source == "chat"
    assert chosen.startswith("# Inline")


def test_coverage_items_come_from_prompt_not_hidden_text():
    question = (
        "Write a report on Indonesia, Malaysia, and Vietnam.\n"
        "1. Define land finance.\n"
        "2. Give the 2021 revenue figure.\n"
        "A) Compare four tax systems.\n"
    )
    items = coverage_items_from_prompt(question)
    assert any("land finance" in item.lower() for item in items)
    assert any("2021" in item for item in items)
    assert any("tax" in item.lower() for item in items)
    assert any("Indonesia" in item for item in items)
    assert any("Vietnam" in item for item in items)


def test_drb2_sandbox_profile_collects_outputs_and_stays_open_book():
    meta = {"benchmark": "deepresearch_bench_ii", "question_id": "1"}
    apply_sandbox_profile(meta)
    assert meta["_collect_outputs"] is True
    assert "gap fill" in meta["_sys_prompt_addendum"]
    assert "/outputs/report.md" in meta["_sys_prompt_addendum"]
    assert resolve_closed_book("deepresearch_bench_ii") is False


def test_persist_selected_report_writes_outputs(tmp_path: Path):
    path = persist_selected_report("# Body\n\ntext", tmp_path / "outputs")
    assert path is not None
    assert path.name == "report.md"
    assert path.read_text(encoding="utf-8").startswith("# Body")


def test_gap_fill_observer_bounces_first_drb2_finalize():
    import asyncio
    from types import SimpleNamespace

    from workflows.agent_team.observers.drb2_gap_fill import Drb2GapFillObserver

    async def _run() -> None:
        observer = Drb2GapFillObserver()
        ctx = SimpleNamespace(
            metadata={"benchmark": "deepresearch_bench_ii"},
            tool_calls=[],
            ai_text="# Report\n" + ("paragraph\n" * 80),
            turn=12,
            max_turns=100,
        )
        first = await observer.on_llm_response(ctx)
        assert first is not None
        assert first.continue_to_next_turn is True
        second = await observer.on_llm_response(ctx)
        assert second is None

    asyncio.run(_run())
