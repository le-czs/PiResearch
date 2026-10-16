"""Place a constellation from parent edges.

The goal sits near the middle. Tasks that share an agent form a cluster on
the side facing away from the goal. Seats are computed from the circles'
real radii, then overlaps are separated locally to keep CLEARANCE between edges.
"""

from __future__ import annotations

import math
from typing import Any

RADIUS = 62
HUB_RADIUS = 88
GOAL_RADIUS = 104
# Gap between circle edges. Placement must keep at least this much.
CLEARANCE = 20.0

_CENTER = (1100.0, 760.0)
_MIN_RING = 430.0
_LEVEL_GAP = 280.0
_RADIAL_VARIATION = 240.0
_MIN_REACH = 220.0
_ROUNDING_SLACK = 0.5


def _order(node_id: str) -> tuple[int, int, str]:
    if node_id == "goal":
        return (0, 0, node_id)
    digits = "".join(ch for ch in node_id if ch.isdigit())
    return (1, int(digits) if digits else 10**9, node_id)


def _cluster_key(node_id: str, agents: dict[str, tuple[str, ...]]) -> str:
    names = agents.get(node_id) or ()
    # An empty agent list is not a shared team. Each such task keeps its own slot.
    return names[0] if names else node_id


def _unit(key: str, salt: int) -> float:
    """Spread short ids like t1 and t2 across the whole range."""
    number = (0x811C9DC5 ^ (salt * 0x01000193)) & 0xFFFFFFFF
    for char in str(key):
        number ^= ord(char)
        number = (number * 0x01000193) & 0xFFFFFFFF
    number ^= number >> 16
    number = (number * 0x85EBCA6B) & 0xFFFFFFFF
    number ^= number >> 13
    return number / 0x100000000


def _distance(a: dict[str, float], b: dict[str, float]) -> float:
    return math.hypot(a["x"] - b["x"], a["y"] - b["y"])


def _radii_of(nodes: list[dict[str, Any]]) -> dict[str, float]:
    radii = {"goal": GOAL_RADIUS}
    for node in nodes:
        node_id = str(node.get("id") or "")
        if not node_id:
            continue
        raw = node.get("radius")
        if raw is None:
            radii.setdefault(node_id, GOAL_RADIUS if node_id == "goal" else RADIUS)
            continue
        radii[node_id] = max(1.0, float(raw))
    return radii


def _reach(members: list[str], radii: dict[str, float]) -> float:
    """How far a satellite center sits from its hub."""
    rest = members[1:]
    if not rest:
        return 0.0
    hub = radii[members[0]]
    reach = max(_MIN_REACH, max(hub + radii[node_id] + CLEARANCE for node_id in rest))
    count = len(rest)
    if count == 1:
        return reach
    deltas = _deltas(count, members[0])
    for index, (left, right) in enumerate(zip(rest, rest[1:])):
        sine = math.sin((deltas[index + 1] - deltas[index]) / 2)
        need = radii[left] + radii[right] + CLEARANCE
        reach = max(reach, need / (2 * sine))
    return reach


def _deltas(count: int, key: str = "") -> list[float]:
    if count <= 1:
        return [(_unit(key or "solo", 9) - 0.5) * 0.9]
    span = min(math.pi, math.pi * (0.45 + 0.18 * (count - 1)))
    shift = (_unit(key or "arc", 8) - 0.5) * 0.35
    weights = [0.7 + 0.6 * _unit(f"{key}:{index}", 10) for index in range(count - 1)]
    total = sum(weights)
    angles = [-span / 2 + shift]
    for weight in weights:
        angles.append(angles[-1] + span * weight / total)
    return angles


def _half_width(members: list[str], radii: dict[str, float]) -> float:
    """Lateral room a cluster needs so a neighbor's circle cannot cross it."""
    hub = radii[members[0]]
    rest = members[1:]
    if not rest:
        return hub
    reach = _reach(members, radii)
    half = hub
    for node_id, delta in zip(rest, _deltas(len(rest), members[0])):
        orbit = reach + 64 * _unit(node_id, 11)
        half = max(half, abs(orbit * math.sin(delta)) + radii[node_id])
    return half


def _place_cluster(
    members: list[str],
    cx: float,
    cy: float,
    outward: float,
    positions: dict[str, dict[str, float]],
    radii: dict[str, float],
    seen: set[str],
) -> None:
    hub = members[0]
    positions[hub] = {"x": cx, "y": cy}
    seen.add(hub)
    rest = [node_id for node_id in members[1:] if node_id not in seen]
    if not rest:
        return
    reach = _reach(members, radii)
    for node_id, delta in zip(rest, _deltas(len(rest), hub)):
        angle = outward + delta
        orbit = reach + 64 * _unit(node_id, 11)
        positions[node_id] = {
            "x": cx + orbit * math.cos(angle),
            "y": cy + orbit * math.sin(angle),
        }
        seen.add(node_id)


def _seat_ring(
    groups: list[tuple[str, list[str]]],
    min_radius: float,
    positions: dict[str, dict[str, float]],
    radii: dict[str, float],
    seen: set[str],
    seed: str,
) -> None:
    count = len(groups)
    if count == 0:
        return
    goal_radius = radii.get("goal", GOAL_RADIUS)
    distance = min_radius
    for _, members in groups:
        distance = max(distance, goal_radius + max(radii[node_id] for node_id in members) + CLEARANCE)
    if count >= 2:
        halves = [_half_width(members, radii) for _, members in groups]
        worst = max(halves[index] + halves[(index + 1) % count] for index in range(count))
        distance = max(distance, (worst + CLEARANCE) / (2 * math.sin(math.pi / count)))
    # The research question gives equally sized studies different compositions.
    # Status, tool counts and timestamps must not change the random seed.
    keys = [f"{seed}|{key}" if seed else key for key, _ in groups]
    ranks = {key: index for index, key in enumerate(sorted(keys, key=lambda key: (_unit(key, 12), key)))}
    angle = _unit("|".join(keys), 7) * 2 * math.pi
    for (_, members), key, gap in zip(groups, keys, _ring_gaps(keys)):
        # Each deeper ring starts beyond the previous one. A proportional
        # offset compounds at every level and makes even small trees enormous.
        # Stratify the seeded offsets so one batch cannot accidentally put
        # every hub at nearly the same distance.
        offset = (ranks[key] + 0.2 + 0.6 * _unit(key, 8)) / count if count > 1 else _unit(key, 8)
        radius = max(
            goal_radius + max(radii[node_id] for node_id in members) + CLEARANCE,
            distance + _RADIAL_VARIATION * (offset - 0.35),
        )
        _place_cluster(
            members,
            _CENTER[0] + radius * math.cos(angle),
            _CENTER[1] + radius * math.sin(angle),
            angle,
            positions,
            radii,
            seen,
        )
        angle += gap


def _ring_gaps(keys: list[str]) -> list[float]:
    """Uneven steps around the circle. The short steps still leave a seat."""
    count = len(keys)
    if count <= 1:
        return [0.0]
    equal = 2 * math.pi / count
    minimum = equal * 0.38
    leftover = 2 * math.pi - minimum * count
    weights = [0.12 + 2 * _unit(key, 5) ** 2 for key in keys]
    total = sum(weights) or 1.0
    return [minimum + leftover * (weight / total) for weight in weights]


def _flatten_vertical(positions: dict[str, dict[str, float]]) -> None:
    goal = positions["goal"]
    for node_id, point in positions.items():
        if node_id == "goal":
            continue
        point["y"] = goal["y"] + (point["y"] - goal["y"]) * 0.93


def _required_scale(positions: dict[str, dict[str, float]], radii: dict[str, float], slack: float) -> float:
    goal = positions["goal"]
    ids = [node_id for node_id in positions if node_id != "goal"]
    scale = 1.0
    for node_id in ids:
        apart = _distance(positions[node_id], goal)
        need = radii.get("goal", GOAL_RADIUS) + radii.get(node_id, RADIUS) + CLEARANCE + slack
        scale = max(scale, need / max(apart, 1e-3))
    for index, left in enumerate(ids):
        for right in ids[index + 1 :]:
            apart = _distance(positions[left], positions[right])
            need = radii.get(left, RADIUS) + radii.get(right, RADIUS) + CLEARANCE + slack
            scale = max(scale, need / max(apart, 1e-3))
    return scale


def _scale_from_goal(positions: dict[str, dict[str, float]], scale: float) -> None:
    goal = positions["goal"]
    for node_id, point in positions.items():
        if node_id == "goal":
            continue
        point["x"] = goal["x"] + (point["x"] - goal["x"]) * scale
        point["y"] = goal["y"] + (point["y"] - goal["y"]) * scale


def _enforce_clearance(positions: dict[str, dict[str, float]], radii: dict[str, float]) -> None:
    """Separate colliding circles locally, holding the goal in place.

    Only circles whose padded boundaries touch move. This preserves the
    composition instead of stretching all branches for one crowded pair.
    A final scale catches numerical residuals and rounding.
    """
    if len(positions) < 2:
        return
    ids = sorted(positions, key=_order)
    for _ in range(128):
        largest = 0.0
        for index, left in enumerate(ids):
            for right in ids[index + 1:]:
                a, b = positions[left], positions[right]
                dx, dy = b["x"] - a["x"], b["y"] - a["y"]
                apart = math.hypot(dx, dy)
                need = radii.get(left, RADIUS) + radii.get(right, RADIUS) + CLEARANCE + _ROUNDING_SLACK
                overlap = need - apart
                if overlap <= 0:
                    continue
                largest = max(largest, overlap)
                if apart < 1e-6:
                    angle = _unit(f"{left}|{right}", 13) * math.tau
                    ux, uy = math.cos(angle), math.sin(angle)
                else:
                    ux, uy = dx / apart, dy / apart
                shift = overlap + 1e-6
                share = 0.0 if left == "goal" else 0.5
                a["x"] -= ux * shift * share
                a["y"] -= uy * shift * share
                b["x"] += ux * shift * (1 - share)
                b["y"] += uy * shift * (1 - share)
        if largest < 0.01:
            break
    for slack in (_ROUNDING_SLACK, 0.0):
        for _ in range(4):
            scale = _required_scale(positions, radii, slack)
            if scale <= 1.0:
                break
            _scale_from_goal(positions, scale)


def _normalize(positions: dict[str, dict[str, float]]) -> None:
    min_x = min(point["x"] for point in positions.values())
    min_y = min(point["y"] for point in positions.values())
    for point in positions.values():
        point["x"] = round(point["x"] - min_x + 140, 1)
        point["y"] = round(point["y"] - min_y + 140, 1)


def _groups_for(node_ids: list[str], agents: dict[str, tuple[str, ...]]) -> list[tuple[str, list[str]]]:
    grouped: dict[str, list[str]] = {}
    for node_id in node_ids:
        grouped.setdefault(_cluster_key(node_id, agents), []).append(node_id)
    groups = []
    for key in sorted(grouped, key=lambda item: _order(grouped[item][0])):
        groups.append((key, sorted(grouped[key], key=_order)))
    return groups


def _reachable(children: dict[str, list[str]]) -> set[str]:
    seen = set()
    stack = list(children.get("goal", []))
    while stack:
        node_id = stack.pop()
        if node_id in seen:
            continue
        seen.add(node_id)
        stack.extend(children.get(node_id, []))
    return seen


def place_constellation(
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
) -> dict[str, dict[str, float]]:
    """Return ``{id: {x, y}}`` for the goal and every task node.

    A node may carry ``radius``. Missing radii use the historical defaults.
    The returned centers satisfy ``distance >= radius_a + radius_b + CLEARANCE``.
    """
    radii = _radii_of(nodes)
    seed = next((str(node.get("title") or "") for node in nodes if node.get("id") == "goal"), "")
    agents: dict[str, tuple[str, ...]] = {}
    ids = ["goal"]
    for node in nodes:
        node_id = str(node.get("id") or "")
        if not node_id or node_id == "goal":
            continue
        if node_id not in ids:
            ids.append(node_id)
        names = tuple(str(name).strip() for name in (node.get("agents") or []) if str(name).strip())
        agents[node_id] = names
        radii.setdefault(node_id, RADIUS)

    children: dict[str, list[str]] = {node_id: [] for node_id in ids}
    for edge in edges:
        source = str(edge.get("from") or "")
        target = str(edge.get("to") or "")
        if not source or not target or source == target:
            continue
        if edge.get("relation") != "parent":
            continue
        children.setdefault(source, [])
        children.setdefault(target, [])
        if target not in children[source]:
            children[source].append(target)
    for kids in children.values():
        kids.sort(key=_order)

    positions: dict[str, dict[str, float]] = {"goal": {"x": _CENTER[0], "y": _CENTER[1]}}
    seen: set[str] = {"goal"}
    reachable = _reachable(children)
    level_parents = ["goal"]
    min_radius = _MIN_RING
    first_level = True
    while True:
        kids: list[str] = []
        for parent in level_parents:
            for kid in children.get(parent, []):
                if kid not in seen and kid not in kids:
                    kids.append(kid)
        if first_level:
            for node_id in ids:
                if node_id != "goal" and node_id not in reachable and node_id not in kids:
                    kids.append(node_id)
            first_level = False
        if not kids:
            break
        _seat_ring(_groups_for(kids, agents), min_radius, positions, radii, seen, seed)
        farthest = 0.0
        goal = positions["goal"]
        for node_id in kids:
            if node_id not in positions:
                continue
            farthest = max(farthest, _distance(goal, positions[node_id]) + radii.get(node_id, RADIUS))
        min_radius = farthest + _LEVEL_GAP
        level_parents = [node_id for node_id in kids if node_id in seen]
        if not level_parents:
            break

    _flatten_vertical(positions)
    _enforce_clearance(positions, radii)
    _normalize(positions)
    _enforce_clearance(positions, radii)
    for point in positions.values():
        point["x"] = round(point["x"], 1)
        point["y"] = round(point["y"], 1)
    return positions
