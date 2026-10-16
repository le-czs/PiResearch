"""Conservative, versioned review of task containment, separate from execution order."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from datetime import UTC, datetime
from typing import Any


def parent_error(document: dict[str, Any]) -> str | None:
    ids = {node["id"] for node in document.get("nodes") or []}
    parents: dict[str, list[str]] = {node_id: [] for node_id in ids}
    for edge in document.get("edges") or []:
        if edge.get("relation") != "parent":
            continue
        source, target = edge.get("from"), edge.get("to")
        if target not in ids or source not in ids | {"goal"}:
            return "parent edges must point from an existing parent to an existing task"
        parents[target].append(source)
    for node_id, incoming in parents.items():
        if len(incoming) != 1:
            return f"{node_id} must have exactly one parent edge"
    for node_id in ids:
        seen: set[str] = set()
        cursor = node_id
        while cursor != "goal":
            if cursor in seen:
                return f"parent cycle involving {cursor}; every task must reach goal"
            seen.add(cursor)
            cursor = parents[cursor][0]
    return None


def _nested(document: dict[str, Any]) -> dict[str, str]:
    return {
        edge["to"]: edge["from"] for edge in document.get("edges") or []
        if edge.get("relation") == "parent" and edge.get("from") != "goal"
    }


def _fingerprint(document: dict[str, Any], descriptions: dict[str, str]) -> str:
    data = {
        "goal": document.get("goal"),
        "tasks": sorted((node["id"], descriptions.get(node["id"], ""))
                        for node in document.get("nodes") or []),
        "parents": sorted((edge["from"], edge["to"]) for edge in document.get("edges") or []
                          if edge.get("relation") == "parent"),
    }
    return hashlib.sha256(json.dumps(data, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def review_blocked(document: dict[str, Any], descriptions: dict[str, str]) -> str | None:
    error = parent_error(document)
    if error:
        return f"Error: {error}"
    if not _nested(document):
        return None
    record = document.get("hierarchy_review") or {}
    if record.get("fingerprint") == _fingerprint(document, descriptions):
        return None
    return (
        "Error: parent containment review required before finishing planning or dispatch. "
        "Call update_constellation with ONLY a review_parents op, expected_version="
        f"{document.get('version')}, and reviews for EVERY non-goal parent edge: "
        "{child, parent, verdict: decomposition|prerequisite|parallel|uncertain, "
        "confidence: high|medium|low, reason, parent_quote, child_quote}. "
        "Quotes must come verbatim from task-board descriptions. Preserve genuine "
        "subtasks and uncertain cases. Only high-confidence prerequisite/parallel "
        "findings move a child to goal; task content and execution stay unchanged. "
        "Review these child/parent pairs: "
        + json.dumps(_nested(document), ensure_ascii=False, sort_keys=True)
    )


def review_parents(
    document: dict[str, Any],
    op: dict[str, Any],
    descriptions: dict[str, str],
) -> tuple[dict[str, Any] | None, str | None]:
    """Validate the whole review, then apply only supported moves to goal atomically.

    Semantic verdicts belong to the coordinator; code checks scope, evidence,
    coverage and freshness. No topology heuristic is evidence of a wrong parent.
    """
    error = parent_error(document)
    if error:
        return None, error
    if type(op.get("expected_version")) is not int or op["expected_version"] != document.get("version"):
        return None, "stale review: read the current graph and review its exact version"
    reviews = op.get("reviews")
    if not isinstance(reviews, list):
        return None, "review_parents needs a reviews list"
    nested = _nested(document)
    findings: list[dict[str, Any]] = []
    reviewed: set[str] = set()
    changes: list[dict[str, str]] = []
    for item in reviews:
        if not isinstance(item, dict):
            return None, "each parent review must be an object"
        child, parent = item.get("child"), item.get("parent")
        if not isinstance(child, str) or child not in nested or nested[child] != parent:
            return None, "review child/parent must match a current non-goal parent edge"
        if child in reviewed:
            return None, f"duplicate parent review for {child}"
        verdict, confidence = item.get("verdict"), item.get("confidence")
        if verdict not in ("decomposition", "prerequisite", "parallel", "uncertain"):
            return None, f"invalid parent-review verdict for {child}"
        if confidence not in ("high", "medium", "low"):
            return None, f"invalid parent-review confidence for {child}"
        reason = str(item.get("reason") or "").strip()
        if not reason:
            return None, f"parent review for {child} needs a specific reason"
        evidence: dict[str, str] = {}
        for field, node_id in (("parent_quote", parent), ("child_quote", child)):
            quote = item.get(field)
            if not isinstance(quote, str) or not quote.strip() or quote not in descriptions.get(node_id, ""):
                return None, f"{field} for {child} must quote the task-board description verbatim"
            evidence[field] = quote
        finding = {"child": child, "parent": parent, "verdict": verdict,
                   "confidence": confidence, "reason": reason, **evidence}
        findings.append(finding)
        reviewed.add(child)
        if verdict in ("prerequisite", "parallel") and confidence == "high":
            changes.append({"child": child, "from": parent, "to": "goal", "reason": reason})
    if reviewed != set(nested):
        return None, "review must cover every non-goal parent edge; missing: " + ", ".join(sorted(set(nested) - reviewed))
    updated = deepcopy(document)
    moves = {change["child"]: change for change in changes}
    for edge in updated.get("edges") or []:
        if edge.get("relation") == "parent" and edge["to"] in moves:
            edge["from"] = "goal"
    error = parent_error(updated)
    if error:
        return None, error
    record = {
        "reviewed_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "version_before": document.get("version"),
        "fingerprint": _fingerprint(updated, descriptions),
        "findings": findings,
        "changes": changes,
    }
    updated["hierarchy_review"] = record
    updated.setdefault("hierarchy_review_history", []).append(deepcopy(record))
    updated["version"] = int(document.get("version") or 1) + 1
    updated["updated_at"] = record["reviewed_at"]
    return updated, None
