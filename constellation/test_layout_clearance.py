"""Circles stay apart after placement, including once radii have grown."""

from __future__ import annotations

import json
import random
from pathlib import Path

from constellation.layout import CLEARANCE, _enforce_clearance, place_constellation
from constellation.node_size import SIZE_STEP, radius_for_calls
from constellation.serve import build_view


def _distance(a: dict, b: dict) -> float:
    return ((a["x"] - b["x"]) ** 2 + (a["y"] - b["y"]) ** 2) ** 0.5


def _assert_clear(placed: dict, radii: dict[str, float]) -> None:
    ids = list(placed)
    for index, left in enumerate(ids):
        for right in ids[index + 1 :]:
            gap = _distance(placed[left], placed[right]) - radii[left] - radii[right]
            assert gap >= CLEARANCE - 0.05, f"{left} overlaps {right} by {-gap:.1f}"


def _parents(ids: list[str]) -> list[dict]:
    return [{"from": "goal", "to": node_id, "relation": "parent"} for node_id in ids]


def test_question_seeds_a_repeatable_but_distinct_composition() -> None:
    tasks = [{"id": f"t{index}", "radius": 60} for index in range(1, 9)]
    edges = _parents([node["id"] for node in tasks])
    nodes = [{"id": "goal", "title": "调研开源项目", "radius": 100}] + tasks
    first = place_constellation(nodes, edges)
    assert first == place_constellation(list(reversed(nodes)), list(reversed(edges)))
    other = [dict(nodes[0], title="调研全国气温")] + tasks
    assert first != place_constellation(other, edges)
    _assert_clear(first, {node["id"]: node["radius"] for node in nodes})


def test_local_collision_does_not_stretch_unrelated_branches() -> None:
    placed = {"goal": {"x": 0.0, "y": 0.0}, "t1": {"x": 300.0, "y": 0.0},
              "t2": {"x": 300.1, "y": 0.0}, "t3": {"x": -500.0, "y": 300.0}}
    radii = {"goal": 100, "t1": 80, "t2": 80, "t3": 60}
    _enforce_clearance(placed, radii)
    _assert_clear(placed, radii)
    assert _distance(placed["t3"], {"x": -500.0, "y": 300.0}) < 1
    assert placed["goal"] == {"x": 0.0, "y": 0.0}


def test_varied_graphs_keep_clearance_after_nodes_grow() -> None:
    rng = random.Random(91)
    for case in range(40):
        owners = [f"worker{rng.randrange(6)}" for _ in range(14)]
        edges = [
            {"from": "goal" if index < 8 else "t1", "to": f"t{index}", "relation": "parent"}
            for index in range(1, 15)
        ]
        for grown in (False, True):
            nodes = [{"id": "goal", "title": f"研究问题{case}", "radius": 152 if grown else 100}]
            nodes += [
                {"id": f"t{index}", "agents": [owner], "radius": rng.uniform(100, 124) if grown else 44}
                for index, owner in enumerate(owners, start=1)
            ]
            placed = place_constellation(nodes, edges)
            radii = {node["id"]: node["radius"] for node in nodes}
            _assert_clear(placed, radii)
            for axis in ("x", "y"):
                assert max(point[axis] for point in placed.values()) - min(point[axis] for point in placed.values()) < 4500


def test_three_level_research_graph_does_not_expand_across_the_canvas() -> None:
    """A discovery task delegates research, then verification delegates writing."""
    owners = ["discovery", "projects", "frameworks", "platforms", "platforms", "browser", "reviews", "", ""]
    sizes = [68.6, 55.4, 52.0, 55.4, 52.0, 52.0, 58.5, 44.0, 44.0]
    nodes = [{"id": "goal", "radius": 96.6}]
    nodes += [
        {"id": f"t{index}", "agents": [owner] if owner else [], "radius": radius}
        for index, (owner, radius) in enumerate(zip(owners, sizes), start=1)
    ]
    edges = [{"from": "goal", "to": "t1", "relation": "parent"}]
    edges += [{"from": "t1", "to": f"t{index}", "relation": "parent"} for index in range(2, 9)]
    edges.append({"from": "t8", "to": "t9", "relation": "parent"})
    placed = place_constellation(nodes, edges)
    radii = {node["id"]: node["radius"] for node in nodes}
    _assert_clear(placed, radii)
    for axis in ("x", "y"):
        span = max(point[axis] + radii[node_id] for node_id, point in placed.items()) - min(
            point[axis] - radii[node_id] for node_id, point in placed.items()
        )
        assert span < 2500, f"Nested research spreads across {span:.0f}px on {axis}"


def test_deeper_task_chain_grows_with_depth_without_multiplying_the_radius() -> None:
    def reach(depth: int) -> float:
        nodes = [{"id": "goal", "radius": 100}]
        nodes += [{"id": f"t{index}", "radius": 60} for index in range(1, depth + 1)]
        edges = [
            {"from": "goal" if index == 1 else f"t{index - 1}", "to": f"t{index}", "relation": "parent"}
            for index in range(1, depth + 1)
        ]
        placed = place_constellation(nodes, edges)
        _assert_clear(placed, {node["id"]: node["radius"] for node in nodes})
        return max(_distance(point, placed["goal"]) for point in placed.values())

    assert reach(6) < reach(3) * 2.5


def test_grown_circles_on_the_week_graph_stay_apart() -> None:
    """The study where a satellite sat inside the goal and a solo sat on a cluster."""
    radii = {
        "goal": 139.5,
        "t1": 95.8,
        "t2": 91.3,
        "t3": 105.1,
        "t4": 92.7,
        "t5": 105.1,
        "t6": 111.5,
        "t7": 96.8,
        "t8": 96.8,
        "t9": 98.7,
        "t10": 44.0,
        "t11": 44.0,
    }
    agents = {
        "t1": ["cn_calendar_research"],
        "t2": ["week_origin_research"],
        "t3": ["buddhist_text_research"],
        "t4": ["week_origin_research"],
        "t5": ["manichaeism_research"],
        "t6": ["buddhist_text_research", "skeptic_research"],
        "t7": ["terminology_research"],
        "t8": ["terminology_research"],
        "t9": ["skeptic_research"],
        "t10": [],
        "t11": [],
    }
    nodes = [{"id": "goal", "radius": radii["goal"]}]
    nodes += [{"id": node_id, "agents": names, "radius": radii[node_id]} for node_id, names in agents.items()]
    edges = _parents(list(agents))
    edges += [
        {"from": "t2", "to": "t4", "relation": "support"},
        {"from": "t3", "to": "t4", "relation": "support"},
        {"from": "t3", "to": "t6", "relation": "support"},
        {"from": "t9", "to": "t6", "relation": "support"},
        {"from": "t5", "to": "t6", "relation": "support"},
        {"from": "t1", "to": "t6", "relation": "support"},
    ]
    placed = place_constellation(nodes, edges)
    again = place_constellation(list(reversed(nodes)), list(reversed(edges)))
    assert placed == again
    _assert_clear(placed, radii)


def test_max_radii_stay_apart_for_teams_solos_and_nesting() -> None:
    task_radius = radius_for_calls(10_000)
    goal_radius = round(task_radius + SIZE_STEP, 1)
    graphs = []

    solos = [{"id": "goal", "radius": goal_radius}]
    solos += [{"id": f"t{index}", "agents": [], "radius": task_radius} for index in range(1, 13)]
    graphs.append((solos, _parents([node["id"] for node in solos if node["id"] != "goal"])))

    teams = [{"id": "goal", "radius": goal_radius}]
    team_edges = []
    for index in range(1, 9):
        agent = f"agent{index // 2}"
        teams.append({"id": f"t{index}", "agents": [agent], "radius": task_radius})
        team_edges.append({"from": "goal", "to": f"t{index}", "relation": "parent"})
    graphs.append((teams, team_edges))

    nested = [
        {"id": "goal", "radius": goal_radius},
        {"id": "t1", "agents": ["scope"], "radius": task_radius},
    ]
    nested_edges = [{"from": "goal", "to": "t1", "relation": "parent"}]
    for index in range(2, 12):
        nested.append({"id": f"t{index}", "agents": [f"worker{(index // 3)}"], "radius": task_radius})
        nested_edges.append({"from": "t1", "to": f"t{index}", "relation": "parent"})
    graphs.append((nested, nested_edges))

    for nodes, edges in graphs:
        placed = place_constellation(nodes, edges)
        radii = {node["id"]: node["radius"] for node in nodes}
        _assert_clear(placed, radii)
        others = [point for node_id, point in placed.items() if node_id != "goal"]
        goal = placed["goal"]
        assert min(point["x"] for point in others) < goal["x"] < max(point["x"] for point in others)
        assert min(point["y"] for point in others) < goal["y"] < max(point["y"] for point in others)


def test_view_seats_circles_after_they_grow(tmp_path: Path) -> None:
    """Radii come from tool calls, so seating has to happen after that count."""
    nodes = []
    edges = []
    folder = tmp_path / "agent" / "trajectories" / "subagents"
    folder.mkdir(parents=True)
    for index in range(1, 13):
        agent = f"a{index}"
        nodes.append({"id": f"t{index}", "title": f"任务{index}", "agents": [agent]})
        edges.append({"from": "goal", "to": f"t{index}", "relation": "parent"})
        lines = []
        for _ in range(80):
            lines.append(json.dumps({"t": "llm", "tool_calls": [{"name": "web_search", "args": {"q": "证据"}}]}))
            lines.append(json.dumps({"t": "result", "name": "web_search", "result": "ok"}))
        (folder / f"{agent}.t01.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (tmp_path / "sandbox" / "worktree").mkdir(parents=True)
    (tmp_path / "sandbox" / "worktree" / "constellation.json").write_text(
        json.dumps({"version": 1, "goal": {"id": "goal", "title": "题目"}, "nodes": nodes, "edges": edges}, ensure_ascii=False),
        encoding="utf-8",
    )
    view = build_view(tmp_path)
    placed = {node["id"]: {"x": node["x"], "y": node["y"]} for node in view["nodes"]}
    radii = {node["id"]: node["radius"] for node in view["nodes"]}
    assert radii["t1"] > 100
    _assert_clear(placed, radii)
