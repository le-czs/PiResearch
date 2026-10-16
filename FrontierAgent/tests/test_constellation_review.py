from __future__ import annotations

import asyncio
import json
from copy import deepcopy

import pytest

from frontier_agent.core.execution_context import (
    ExecutionScope,
    reset_current_execution_scope,
    set_current_execution_scope,
)
from plugins.tools._constellation_review import review_blocked
from plugins.tools.assign_task import assign_task
from plugins.tools.constellation import apply_ops, init_constellation, update_constellation
from plugins.tools.task_board import (
    add_task,
    clear_board,
    finish_planning,
    force_finish_planning,
)


def _case():
    descriptions = {
        "t1": "Identify the latest model name and release date",
        "t2": "Collect independent benchmark results for the latest model",
        "t3": "Evaluate real-world reliability: tool use and long-context errors",
        "t4": "Investigate tool use failures in real-world usage",
    }
    document = {
        "version": 7, "goal": {"id": "goal", "title": "Evaluate model capabilities"},
        "nodes": [{"id": key, "title": text, "agents": ["researcher"]} for key, text in descriptions.items()],
        "edges": [
            {"from": "goal", "to": "t1", "relation": "parent"},
            {"from": "t1", "to": "t2", "relation": "parent"},
            {"from": "goal", "to": "t3", "relation": "parent"},
            {"from": "t3", "to": "t4", "relation": "parent"},
            {"from": "t2", "to": "t4", "relation": "support"},
        ],
    }
    reviews = [
        {"child": "t2", "parent": "t1", "verdict": "prerequisite", "confidence": "high",
         "reason": "Naming the model sets a shared subject; independent measurements do not fulfill identifying its name.",
         "parent_quote": descriptions["t1"], "child_quote": descriptions["t2"]},
        {"child": "t4", "parent": "t3", "verdict": "decomposition", "confidence": "high",
         "reason": "The parent explicitly includes tool use; failure investigation fulfills that part of reliability research.",
         "parent_quote": descriptions["t3"], "child_quote": descriptions["t4"]},
    ]
    return document, descriptions, {"op": "review_parents", "expected_version": 7, "reviews": reviews}


def test_review_fixes_wrong_parent_and_preserves_valid_subtree_and_other_relations():
    document, descriptions, op = _case()
    before = deepcopy(document)
    assert "review required" in review_blocked(document, descriptions)
    updated, error = apply_ops(document, [op], descriptions)
    assert error is None
    assert document == before
    assert updated["nodes"] == document["nodes"]
    assert {"from": "goal", "to": "t2", "relation": "parent"} in updated["edges"]
    assert {"from": "t3", "to": "t4", "relation": "parent"} in updated["edges"]
    assert updated["edges"][-1] == document["edges"][-1]
    assert updated["hierarchy_review"]["changes"][0]["from"] == "t1"
    assert updated["hierarchy_review_history"][-1] == updated["hierarchy_review"]
    assert review_blocked(updated, descriptions) is None


@pytest.mark.parametrize("verdict,confidence", [
    ("uncertain", "high"), ("prerequisite", "medium"), ("parallel", "low"), ("decomposition", "high"),
])
def test_uncertain_low_confidence_or_valid_relations_are_not_changed(verdict, confidence):
    document, descriptions, op = _case()
    op["reviews"][0].update(verdict=verdict, confidence=confidence)
    updated, error = apply_ops(document, [op], descriptions)
    assert error is None
    assert updated["edges"] == document["edges"]
    assert not updated["hierarchy_review"]["changes"]
    assert review_blocked(updated, descriptions) is None


@pytest.mark.parametrize("bad_case", ["stale", "partial", "invented_quote", "empty_quote", "duplicate", "root", "reason", "verdict"])
def test_incomplete_or_unfounded_reviews_are_rejected_atomically(bad_case):
    document, descriptions, op = _case()
    if bad_case == "stale":
        op["expected_version"] = 6
    elif bad_case == "partial":
        op["reviews"].pop()
    elif bad_case == "invented_quote":
        op["reviews"][0]["parent_quote"] = "Does all research"
    elif bad_case == "empty_quote":
        op["reviews"][0]["child_quote"] = ""
    elif bad_case == "duplicate":
        op["reviews"].append(op["reviews"][0])
    elif bad_case == "root":
        op["reviews"][0].update(child="t1", parent="goal")
    elif bad_case == "reason":
        op["reviews"][0]["reason"] = ""
    elif bad_case == "verdict":
        op["reviews"][0]["verdict"] = "looks_uneven"
    before = deepcopy(document)
    updated, error = apply_ops(document, [op], descriptions)
    assert updated is None and error
    assert document == before


def test_review_is_separate_from_edits_and_invalidates_only_for_scope_or_structure():
    document, descriptions, op = _case()
    mixed, error = apply_ops(document, [op, {"op": "set_agents", "id": "t1", "agents": []}], descriptions)
    assert mixed is None and "only op" in error
    updated, _ = apply_ops(document, [op], descriptions)
    reassigned, error = apply_ops(updated, [{"op": "set_agents", "id": "t4", "agents": ["other"]}], descriptions)
    assert error is None and review_blocked(reassigned, descriptions) is None
    changed_scope = dict(descriptions, t4="Investigate API pricing")
    assert "review required" in review_blocked(reassigned, changed_scope)
    reparented, error = apply_ops(reassigned, [
        {"op": "remove_edge", "from": "goal", "to": "t2", "relation": "parent"},
        {"op": "add_edge", "from": "t3", "to": "t2", "relation": "parent"},
    ], descriptions)
    assert error is None and "review required" in review_blocked(reparented, descriptions)


def test_cycles_rejected_even_with_one_parent_each():
    document, descriptions, _ = _case()
    updated, error = apply_ops(document, [
        {"op": "remove_edge", "from": "goal", "to": "t1", "relation": "parent"},
        {"op": "add_edge", "from": "t2", "to": "t1", "relation": "parent"},
    ], descriptions)
    assert updated is None and "cycle" in error


def test_ordinary_edge_ops_cannot_bypass_evidence_to_flatten_a_valid_subtree():
    document, descriptions, _ = _case()
    before = deepcopy(document)
    updated, error = apply_ops(document, [
        {"op": "remove_edge", "from": "t3", "to": "t4", "relation": "parent"},
        {"op": "add_edge", "from": "goal", "to": "t4", "relation": "parent"},
    ], descriptions)
    assert updated is None and "only through review_parents" in error
    assert document == before


def test_single_root_with_genuine_decomposition_is_preserved():
    document, descriptions, op = _case()
    document["edges"] = [edge for edge in document["edges"] if edge["to"] in ("t3", "t4")]
    document["nodes"] = [node for node in document["nodes"] if node["id"] in ("t3", "t4")]
    op["reviews"] = [op["reviews"][1]]
    updated, error = apply_ops(document, [op], descriptions)
    assert error is None
    assert updated["edges"] == document["edges"]


def test_planning_and_dispatch_cannot_skip_review_even_after_turn_cap(tmp_path):
    document, descriptions, op = _case()
    init_constellation(tmp_path, "Evaluate model capabilities")
    token = set_current_execution_scope(ExecutionScope(
        task_id="review-integration", phase_id="execution", role_id="agent_team_main",
        metadata={"constellation_dir": str(tmp_path)},
    ))
    try:
        asyncio.run(add_task.func([{"description": text} for text in descriptions.values()]))
        (tmp_path / "constellation.json").write_text(json.dumps(document))
        before = (tmp_path / "constellation.json").read_text()
        assert "review required" in asyncio.run(finish_planning.func())
        force_finish_planning("review-integration")
        # Guard runs before AgentBus lookup or worker dispatch: no jobs can start.
        assert "review required" in asyncio.run(assign_task.func([{"agent": "researcher", "prompt": "research"}]))
        assert (tmp_path / "constellation.json").read_text() == before
        reviewed = asyncio.run(update_constellation.func([op]))
        assert not reviewed.startswith("Error")
        assert "Planning complete" in asyncio.run(finish_planning.func())
    finally:
        reset_current_execution_scope(token)
        clear_board("review-integration")
