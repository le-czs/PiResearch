from __future__ import annotations

from pathlib import Path

from workflows.agent_team.prompts import (
    get_main_system_prompt,
    get_subagent_system_prompt,
)

_PROFILES = Path(__file__).resolve().parents[1] / "workflows" / "agent_team" / "profiles"
_PROFILE_FILES = (
    "drb2_deepseek.yaml",
    "drb2_deepseek_v2.yaml",
    "tui.yaml",
    "benchmark.yaml",
    "simple.yaml",
)


def test_planning_and_execution_prompts_say_when_and_where_to_update() -> None:
    planning = get_main_system_prompt("2026-10-13", phase="planning")
    execution = get_main_system_prompt("2026-10-13", phase="execution")
    for prompt in (planning, execution):
        assert "update_constellation" in prompt
        assert "/workspace/constellation.json" in prompt
        assert "add_node" in prompt
        assert "set_agents" in prompt
        assert "Same turn as `add_task`" in prompt
        assert "fusion" not in prompt
        assert "error" in prompt
        assert "whose conclusion is wrong" in prompt
        assert "review_parents" in prompt
        assert "uncertain and keep the edge" in prompt
        assert "prerequisite" in prompt
    assert "update_constellation" not in get_subagent_system_prompt("researcher", "find sources")


def test_agent_team_profiles_allow_the_graph_tool() -> None:
    for name in _PROFILE_FILES:
        text = (_PROFILES / name).read_text(encoding="utf-8")
        line = next(
            row for row in text.splitlines() if row.strip().startswith("main_agent_tools:")
        )
        assert "update_constellation" in line
