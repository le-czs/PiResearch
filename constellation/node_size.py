"""Node radius follows tool-call count. The goal stays one size larger."""

from __future__ import annotations

from typing import Any

MIN_RADIUS = 44.0
GROWTH = 80.0
SIZE_STEP = 28.0


def radius_for_calls(calls: int) -> float:
    count = max(0, int(calls))
    return round(MIN_RADIUS + GROWTH * (1 - 1 / (1 + count / 18)), 1)


def _tool_calls(node: dict[str, Any]) -> int:
    done = len(node.get("steps") or [])
    live = [
        step
        for step in (node.get("live") or [])
        if step.get("tool") and step.get("tool") != "思考"
    ]
    return done + len(live)


def apply_call_radii(nodes: list[dict[str, Any]]) -> None:
    peak = radius_for_calls(0)
    for node in nodes:
        if node.get("goal"):
            continue
        node["radius"] = radius_for_calls(_tool_calls(node))
        peak = max(peak, node["radius"])
    for node in nodes:
        if node.get("goal"):
            node["radius"] = round(peak + SIZE_STEP, 1)
