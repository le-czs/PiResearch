from __future__ import annotations

import asyncio
import json
from pathlib import Path

from frontier_agent.core.execution_context import (
    ExecutionScope,
    reset_current_execution_scope,
    set_current_execution_scope,
)
from plugins.tools.constellation import (
    finish_blocked,
    init_constellation,
    repair_missing_parents,
    update_constellation,
)
from plugins.tools.task_board import add_task, clear_board, finish_planning, is_planning_allowed


def _scope(directory: Path, task_id: str = "run-1") -> ExecutionScope:
    return ExecutionScope(
        task_id=task_id,
        phase_id="phase",
        role_id="agent_team_main",
        metadata={"constellation_dir": str(directory)},
    )


def _use(directory: Path, task_id: str = "run-1"):
    token = set_current_execution_scope(_scope(directory, task_id))
    return token


def test_legal_ops_bump_version_and_illegal_ops_keep_the_file(tmp_path: Path) -> None:
    init_constellation(tmp_path, "土地财政怎么转型")
    token = _use(tmp_path)
    try:
        asyncio.run(add_task.func([{"description": "检索 2021 年土地出让收入"}]))
        first = asyncio.run(update_constellation.func([
            {"op": "add_node", "id": "t1"},
            {"op": "add_edge", "from": "goal", "to": "t1", "relation": "parent"},
        ]))
        assert not first.startswith("Error")
        saved = json.loads((tmp_path / "constellation.json").read_text())
        assert saved["version"] == 2
        assert saved["nodes"][0]["title"] == "检索 2021 年土地出让收入"
        before = (tmp_path / "constellation.json").read_text()
        rejected = asyncio.run(update_constellation.func([
            {"op": "add_edge", "from": "t1", "to": "missing", "relation": "support"},
        ]))
        assert rejected.startswith("Error")
        assert (tmp_path / "constellation.json").read_text() == before
    finally:
        reset_current_execution_scope(token)
        clear_board("run-1")


def test_parent_can_be_replaced_in_one_call_and_goal_cannot_be_removed(tmp_path: Path) -> None:
    init_constellation(tmp_path, "题目")
    token = _use(tmp_path)
    try:
        asyncio.run(add_task.func([
            {"description": "总问题"},
            {"description": "从总问题拆出的子问题"},
        ]))
        asyncio.run(update_constellation.func([
            {"op": "add_node", "id": "t1"},
            {"op": "add_edge", "from": "goal", "to": "t1", "relation": "parent"},
            {"op": "add_node", "id": "t2"},
            {"op": "add_edge", "from": "goal", "to": "t2", "relation": "parent"},
        ]))
        replaced = asyncio.run(update_constellation.func([
            {"op": "remove_edge", "from": "goal", "to": "t2", "relation": "parent"},
            {"op": "add_edge", "from": "t1", "to": "t2", "relation": "parent"},
        ]))
        assert not replaced.startswith("Error")
        edges = json.loads((tmp_path / "constellation.json").read_text())["edges"]
        assert {"from": "t1", "to": "t2", "relation": "parent"} in edges
        removed = asyncio.run(update_constellation.func([{"op": "remove_node", "id": "goal"}]))
        assert "goal" in removed
    finally:
        reset_current_execution_scope(token)
        clear_board("run-1")


def test_finish_planning_rejects_a_task_without_a_parent_and_repair_links_goal(tmp_path: Path) -> None:
    init_constellation(tmp_path, "题目")
    token = _use(tmp_path)
    try:
        asyncio.run(add_task.func([{"description": "还没挂上的任务"}]))
        blocked = asyncio.run(finish_planning.func())
        assert blocked.startswith("Error")
        assert "t1" in blocked
        repair_missing_parents(tmp_path, {"t1": {"description": "还没挂上的任务"}})
        assert finish_blocked(tmp_path, ["t1"]) is None
        edges = json.loads((tmp_path / "constellation.json").read_text())["edges"]
        assert {"from": "goal", "to": "t1", "relation": "parent"} in edges
    finally:
        reset_current_execution_scope(token)
        clear_board("run-1")


def test_task_board_snapshot_keeps_notes(tmp_path: Path) -> None:
    from plugins.tools.task_board import update_task

    init_constellation(tmp_path, "题目")
    token = _use(tmp_path)
    try:
        asyncio.run(add_task.func([{"description": "检索收入"}]))
        asyncio.run(update_task.func([{"id": "t1", "resolution": "resolved", "notes": "87051 亿元"}]))
        board = json.loads((tmp_path / "task_board.json").read_text())
        assert board["tasks"]["t1"]["notes"] == "87051 亿元"
        assert board["tasks"]["t1"]["resolution"] == "resolved"
    finally:
        reset_current_execution_scope(token)
        clear_board("run-1")


def test_update_constellation_is_allowed_while_planning() -> None:
    assert is_planning_allowed("update_constellation")
