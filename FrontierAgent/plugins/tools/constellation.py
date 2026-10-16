"""Constellation graph for one Agent Team trial.

The main agent submits ops. This module validates them and writes
``constellation.json`` under the trial workspace. It does not invent
relations for runs that never called the tool.
"""

from __future__ import annotations

import json
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from frontier_agent.core.execution_context import get_current_execution_scope
from frontier_agent.core.tool import tool
from plugins.tools._coerce import coerce_json_list, coerce_json_object
from plugins.tools._constellation_review import parent_error, review_blocked, review_parents

RELATIONS = frozenset({"parent", "audit", "support", "challenge", "fusion", "error"})
_GRAPH_NAME = "constellation.json"
_BOARD_NAME = "task_board.json"


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _atomic_write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _load(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def constellation_dir_from_scope() -> Path | None:
    scope = get_current_execution_scope()
    if scope is None:
        return None
    raw = str(scope.metadata.get("constellation_dir") or "").strip()
    return Path(raw) if raw else None


def init_constellation(directory: Path, title: str) -> None:
    """Write the goal-only graph and an empty board if they are not there yet."""
    directory.mkdir(parents=True, exist_ok=True)
    graph = directory / _GRAPH_NAME
    if not graph.exists():
        _atomic_write(graph, {
            "version": 1,
            "updated_at": _now(),
            "goal": {"id": "goal", "title": title},
            "nodes": [],
            "edges": [],
        })
    board = directory / _BOARD_NAME
    if not board.exists():
        _atomic_write(board, {"tasks": {}})


def persist_task_board(directory: Path, tasks: dict[str, dict[str, Any]]) -> None:
    """Snapshot the in-memory board. The model does not write this file."""
    snapshot = {"tasks": {}}
    for task_id, task in tasks.items():
        snapshot["tasks"][task_id] = {
            "description": task.get("description", ""),
            "resolution": task.get("resolution", "open"),
            "owners": list(task.get("owners") or []),
            "notes": task.get("notes") or "",
        }
    _atomic_write(directory / _BOARD_NAME, snapshot)


def render_constellation(directory: Path) -> str:
    document = _load(directory / _GRAPH_NAME)
    if document is None:
        return "{}"
    return json.dumps(document, ensure_ascii=False, indent=2)


def _node_ids(nodes: dict[str, dict[str, Any]]) -> set[str]:
    return set(nodes) | {"goal"}


def _parent_count(edges: list[dict[str, str]], node_id: str) -> int:
    return sum(1 for edge in edges if edge["relation"] == "parent" and edge["to"] == node_id)


def _agents(value: Any) -> tuple[list[str] | None, str | None]:
    if value is None:
        return [], None
    if isinstance(value, str):
        value = [part.strip() for part in value.split(",") if part.strip()]
    if not isinstance(value, list) or any(not isinstance(item, str) or not item.strip() for item in value):
        return None, "agents must be a list of names"
    return [item.strip() for item in value], None


def apply_ops(
    document: dict[str, Any],
    ops: list[Any],
    descriptions: dict[str, str],
) -> tuple[dict[str, Any] | None, str | None]:
    """Apply ops to a copy. On error the caller keeps the previous file."""
    if not ops:
        return None, "update_constellation needs at least one op"
    parsed_ops = [coerce_json_object(op) for op in ops]
    if any(op and op.get("op") == "review_parents" for op in parsed_ops):
        if len(parsed_ops) != 1:
            return None, "review_parents must be the only op; review the saved graph, not a proposed batch"
        return review_parents(document, parsed_ops[0], descriptions)
    doc = deepcopy(document)
    nodes: dict[str, dict[str, Any]] = {node["id"]: dict(node) for node in doc.get("nodes") or []}
    edges: list[dict[str, str]] = [dict(edge) for edge in doc.get("edges") or []]

    def has_edge(source: str, target: str, relation: str) -> bool:
        return any(
            edge["from"] == source and edge["to"] == target and edge["relation"] == relation
            for edge in edges
        )

    for raw in ops:
        op = coerce_json_object(raw)
        if op is None:
            return None, "each op must be an object"
        kind = str(op.get("op") or "")
        if kind == "add_node":
            node_id = str(op.get("id") or "")
            if node_id == "goal" or node_id not in descriptions:
                return None, f"{node_id or '?'} is not a task on the board"
            if node_id in nodes:
                return None, f"{node_id} is already on the graph"
            agents, agent_error = _agents(op.get("agents"))
            if agent_error or agents is None:
                return None, agent_error or "bad agents"
            title = str(op.get("title") or "").strip() or descriptions[node_id]
            nodes[node_id] = {
                "id": node_id,
                "title": title,
                "task_id": node_id,
                "agents": agents,
            }
        elif kind == "add_edge":
            source = str(op.get("from") or "")
            target = str(op.get("to") or "")
            relation = str(op.get("relation") or "")
            if relation not in RELATIONS:
                return None, f"unknown relation {relation!r}"
            if source == target:
                return None, "edges cannot loop"
            if relation == "parent" and target == "goal":
                return None, "goal cannot be a child"
            known = _node_ids(nodes)
            if source not in known or target not in known:
                return None, f"edge {source} -> {target} has a missing endpoint"
            if has_edge(source, target, relation):
                return None, f"duplicate {relation} edge {source} -> {target}"
            edges.append({"from": source, "to": target, "relation": relation})
        elif kind == "set_agents":
            node_id = str(op.get("id") or "")
            if node_id == "goal" or node_id not in nodes:
                return None, f"{node_id or '?'} is not a task node"
            agents, agent_error = _agents(op.get("agents"))
            if agent_error or agents is None:
                return None, agent_error or "bad agents"
            nodes[node_id]["agents"] = agents
        elif kind == "remove_edge":
            source = str(op.get("from") or "")
            target = str(op.get("to") or "")
            relation = str(op.get("relation") or "")
            if not has_edge(source, target, relation):
                return None, f"no {relation} edge {source} -> {target}"
            edges = [
                edge for edge in edges
                if not (edge["from"] == source and edge["to"] == target and edge["relation"] == relation)
            ]
        elif kind == "remove_node":
            node_id = str(op.get("id") or "")
            if node_id == "goal" or node_id not in nodes:
                return None, "goal cannot be removed" if node_id == "goal" else f"{node_id or '?'} is not a task node"
            if any(edge["from"] == node_id or edge["to"] == node_id for edge in edges):
                return None, f"{node_id} still has edges"
            del nodes[node_id]
        else:
            return None, f"unknown op {kind!r}"

    doc["nodes"] = list(nodes.values())
    doc["edges"] = edges
    error = parent_error(doc)
    if error:
        return None, error
    old_parents = {edge["to"]: edge["from"] for edge in document.get("edges") or []
                   if edge.get("relation") == "parent"}
    new_parents = {edge["to"]: edge["from"] for edge in edges if edge.get("relation") == "parent"}
    for child, parent in old_parents.items():
        if parent != "goal" and child in new_parents and new_parents[child] != parent:
            return None, (
                f"Existing nested parent for {child} may change only through review_parents; "
                "provide evidence before detaching a potentially valid subtask"
            )
    doc["version"] = int(doc.get("version") or 1) + 1
    doc["updated_at"] = _now()
    return doc, None


def finish_blocked(directory: Path, task_ids: list[str]) -> str | None:
    document = _load(directory / _GRAPH_NAME)
    if document is None:
        return "Error: constellation.json is missing"
    nodes = {node["id"] for node in document.get("nodes") or []}
    edges = document.get("edges") or []
    missing = []
    for task_id in task_ids:
        parents = _parent_count(edges, task_id)
        if task_id not in nodes or parents != 1:
            missing.append(task_id)
    if not missing:
        error = parent_error(document)
        return f"Error: {error}" if error else None
    return (
        "Error: constellation is missing a node or exactly one parent edge for "
        + ", ".join(missing)
    )


def hierarchy_review_blocked(directory: Path, tasks: dict[str, dict[str, Any]]) -> str | None:
    """Require current containment evidence at planning and dispatch boundaries."""
    blocked = finish_blocked(directory, list(tasks))
    if blocked:
        return blocked
    document = _load(directory / _GRAPH_NAME)
    if document is None:
        return "Error: constellation.json is missing"
    return review_blocked(document, {key: task.get("description", "") for key, task in tasks.items()})


def repair_missing_parents(directory: Path, tasks: dict[str, dict[str, Any]]) -> bool:
    """Attach tasks that the planner never linked. Used only when planning hits its turn cap."""
    path = directory / _GRAPH_NAME
    document = _load(path)
    if document is None:
        return False
    nodes = {node["id"]: node for node in document.get("nodes") or []}
    edges = list(document.get("edges") or [])
    changed = False
    for task_id, task in tasks.items():
        if task_id not in nodes:
            nodes[task_id] = {
                "id": task_id,
                "title": task.get("description") or task_id,
                "task_id": task_id,
                "agents": [],
            }
            changed = True
        if _parent_count(edges, task_id) == 0:
            edges.append({"from": "goal", "to": task_id, "relation": "parent"})
            changed = True
    if not changed:
        return False
    document["nodes"] = list(nodes.values())
    document["edges"] = edges
    document["version"] = int(document.get("version") or 1) + 1
    document["updated_at"] = _now()
    _atomic_write(path, document)
    return True


@tool
async def update_constellation(ops: list[Any]) -> str:
    """Update the research graph for this trial.

    Do not rewrite ``/workspace/constellation.json`` yourself. Pass a list of
    ops, applied in order. The whole call is rejected if any op is illegal.
    Existing nested parents cannot be replaced by raw remove/add ops: use
    review_parents so valid subtrees cannot be silently flattened or regrouped.

    Args:
        ops: each item is one of
            ``{"op": "add_node", "id", "title"?, "agents"?}``
            ``{"op": "add_edge", "from", "to", "relation"}``
            where relation is parent, audit, support, challenge, fusion, or error
            ``{"op": "set_agents", "id", "agents"}``
            ``{"op": "remove_edge", "from", "to", "relation"}``
            ``{"op": "remove_node", "id"}``
            ``{"op": "review_parents", "expected_version": int, "reviews": [...]}``
            Review must be the ONLY op. Include EVERY non-goal parent edge as
            {child, parent, verdict, confidence, reason, parent_quote, child_quote}.
            verdict: decomposition|prerequisite|parallel|uncertain.
            confidence: high|medium|low. Quotes are exact task-board excerpts.
            Preserve valid decomposition and uncertain relations. Only high
            confidence prerequisite/parallel findings move children to goal.
            All decisions and original parents are recorded in the graph.

    Returns:
        The updated graph, or an error naming the rejected op.
    """
    directory = constellation_dir_from_scope()
    if directory is None:
        return "Error: constellation directory is not configured for this run."
    scope = get_current_execution_scope()
    if scope is None:
        return "Error: update_constellation can only be called inside an active run."
    from plugins.tools.task_board import board_tasks

    tasks = board_tasks(scope.task_id)
    descriptions = {task_id: task.get("description", "") for task_id, task in tasks.items()}
    document = _load(directory / _GRAPH_NAME)
    if document is None:
        return "Error: constellation.json is missing"
    parsed = coerce_json_list(ops) or []
    updated, error = apply_ops(document, parsed, descriptions)
    if error or updated is None:
        return f"Error: {error}"
    _atomic_write(directory / _GRAPH_NAME, updated)
    return json.dumps(updated, ensure_ascii=False, indent=2)
