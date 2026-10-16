from __future__ import annotations

import json
import math
import os
import sys
import threading
from pathlib import Path
from urllib.request import Request, urlopen

from constellation.home import create_session, delete_study, job_interpreter, list_history
from constellation.layout import place_constellation
from constellation.serve import MISSING_GRAPH, _study_view, agent_steps, build_view, make_home_server, make_server, read_report


def _distance(a: dict, b: dict) -> float:
    return ((a["x"] - b["x"]) ** 2 + (a["y"] - b["y"]) ** 2) ** 0.5


def _assert_goal_near_center(placed: dict) -> None:
    goal = placed["goal"]
    others = [point for node_id, point in placed.items() if node_id != "goal"]
    farthest = max(_distance(goal, point) for point in others)
    center_x = sum(point["x"] for point in placed.values()) / len(placed)
    center_y = sum(point["y"] for point in placed.values()) / len(placed)
    assert _distance(goal, {"x": center_x, "y": center_y}) < farthest * 0.45
    assert min(point["x"] for point in others) < goal["x"] < max(point["x"] for point in others) or min(point["y"] for point in others) < goal["y"] < max(point["y"] for point in others)


def _write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def test_same_task_id_keeps_a_stable_place() -> None:
    nodes = [{"id": "t10", "title": "后"}, {"id": "t2", "title": "先"}]
    edges = [
        {"from": "goal", "to": "t10", "relation": "parent"},
        {"from": "goal", "to": "t2", "relation": "parent"},
        {"from": "t10", "to": "t2", "relation": "challenge"},
    ]
    first = place_constellation(nodes, edges)
    second = place_constellation(list(reversed(nodes)), list(reversed(edges)))
    assert first == second
    goal = first["goal"]
    assert min(first["t2"]["x"], first["t10"]["x"]) < goal["x"] < max(first["t2"]["x"], first["t10"]["x"]) or min(first["t2"]["y"], first["t10"]["y"]) < goal["y"] < max(first["t2"]["y"], first["t10"]["y"])
    assert _distance(first["t2"], first["t10"]) > 400


def test_tasks_do_not_sit_on_a_clock() -> None:
    nodes = [{"id": f"t{index}", "title": f"任务{index}", "agents": []} for index in range(1, 8)]
    edges = [{"from": "goal", "to": node["id"], "relation": "parent"} for node in nodes]
    placed = place_constellation(nodes, edges)
    goal = placed["goal"]
    angles = sorted(
        math.atan2(placed[node["id"]]["y"] - goal["y"], placed[node["id"]]["x"] - goal["x"])
        for node in nodes
    )
    gaps = [angles[index + 1] - angles[index] for index in range(len(angles) - 1)]
    gaps.append(angles[0] + 2 * math.pi - angles[-1])
    assert max(gaps) - min(gaps) > 0.2
    distances = [_distance(goal, placed[node["id"]]) for node in nodes]
    # Keep an irregular constellation without stretching every deeper ring.
    assert 1.45 < max(distances) / min(distances) < 2.5


def test_unassigned_tasks_surround_the_goal() -> None:
    nodes = [{"id": f"t{index}", "title": f"任务{index}", "agents": []} for index in range(1, 7)]
    edges = [{"from": "goal", "to": node["id"], "relation": "parent"} for node in nodes]
    placed = place_constellation(nodes, edges)
    _assert_goal_near_center(placed)
    goal = placed["goal"]
    others = [placed[node["id"]] for node in nodes]
    assert min(point["x"] for point in others) < goal["x"] < max(point["x"] for point in others)
    assert min(point["y"] for point in others) < goal["y"] < max(point["y"] for point in others)
    nearest = min(_distance(others[i], others[j]) for i in range(len(others)) for j in range(i + 1, len(others)))
    assert nearest > 280


def test_nested_tasks_spread_around_the_goal() -> None:
    nodes = [
        {"id": "t1", "agents": ["scope"]},
        {"id": "t2", "agents": ["glycemic"]},
        {"id": "t3", "agents": ["cardio"]},
        {"id": "t4", "agents": ["cardio"]},
        {"id": "t5", "agents": ["safety"]},
        {"id": "t6", "agents": ["safety"]},
        {"id": "t7", "agents": []},
        {"id": "t8", "agents": []},
    ]
    edges = [{"from": "goal", "to": "t1", "relation": "parent"}]
    edges += [{"from": "t1", "to": node["id"], "relation": "parent"} for node in nodes if node["id"] != "t1"]
    placed = place_constellation(nodes, edges)
    _assert_goal_near_center(placed)
    kids = [placed[node["id"]] for node in nodes if node["id"] != "t1"]
    goal = placed["goal"]
    assert min(point["x"] for point in kids) < goal["x"] < max(point["x"] for point in kids)
    assert min(point["y"] for point in kids) < goal["y"] < max(point["y"] for point in kids)
    nearest = min(
        _distance(kids[i], kids[j])
        for i in range(len(kids))
        for j in range(i + 1, len(kids))
    )
    assert nearest > 180
    assert _distance(placed["t1"], placed["t2"]) > 300


def test_agents_land_in_separate_clusters() -> None:
    nodes = [
        {"id": "t1", "agents": ["policy"]},
        {"id": "t2", "agents": ["policy"]},
        {"id": "t3", "agents": ["data"]},
        {"id": "t4", "agents": ["data"]},
    ]
    edges = [
        {"from": "goal", "to": node["id"], "relation": "parent"}
        for node in nodes
    ]
    placed = place_constellation(nodes, edges)
    near = _distance(placed["t1"], placed["t2"])
    far = _distance(placed["t1"], placed["t3"])
    assert near < far * 0.65
    assert _distance(placed["t3"], placed["t4"]) < far * 0.65
    _assert_goal_near_center(placed)


def test_agent_steps_summarize_calls_and_compaction(tmp_path: Path) -> None:
    folder = tmp_path / "agent" / "trajectories" / "subagents"
    folder.mkdir(parents=True)
    lines = [
        {"t": "llm", "tool_calls": [{"name": "web_search", "args": {"q": ["土地出让收入 87051"]}}]},
        {"t": "result", "name": "web_search", "result": "找到了口径"},
        {"t": "compaction", "tokens_before": 1000, "tokens_after": 200, "summary": ""},
    ]
    (folder / "land.t01.jsonl").write_text(
        "\n".join(json.dumps(item, ensure_ascii=False) for item in lines) + "\n",
        encoding="utf-8",
    )
    steps = agent_steps(tmp_path, "land")
    assert steps[0]["tool"] == "web_search"
    assert steps[0]["label"].startswith("【查询】")
    assert "87051" in steps[0]["label"]
    assert "/Users" not in steps[0]["label"]
    assert steps[1]["tool"] == "压缩"
    assert "1000" in steps[1]["label"]

    folder.joinpath("land.t02.jsonl").write_text(
        "\n".join(json.dumps(item, ensure_ascii=False) for item in [
            {"t": "llm", "tool_calls": [
                {"name": "bash", "args": {"command": "cd /Users/xxx/github/multica-research/deepresearch-drb2-v2/sandbox/worktree"}},
                {"name": "bash", "args": {"command": "cd /Users/xxx/github/multica-research/deepresearch-drb2-v2/sandbox/worktree"}},
            ]},
            {"t": "result", "name": "bash", "result": "ok"},
            {"t": "result", "name": "bash", "result": "ok"},
        ]) + "\n",
        encoding="utf-8",
    )
    folded = agent_steps(tmp_path, "land")
    bash = [step for step in folded if step["tool"] == "bash"]
    assert len(bash) == 1
    assert bash[0]["count"] == 2
    assert "进入" in bash[0]["label"]
    assert "/Users" not in bash[0]["label"]


def test_live_call_follows_the_tool_in_flight(tmp_path: Path) -> None:
    work = tmp_path / "sandbox" / "worktree"
    _write(work / "constellation.json", {
        "version": 1,
        "goal": {"id": "goal", "title": "题目"},
        "nodes": [
            {"id": "t1", "title": "先查", "task_id": "t1", "agents": ["land"]},
            {"id": "t2", "title": "再核", "task_id": "t2", "agents": ["land"]},
        ],
        "edges": [
            {"from": "goal", "to": "t1", "relation": "parent"},
            {"from": "goal", "to": "t2", "relation": "parent"},
        ],
    })
    _write(work / "task_board.json", {"tasks": {
        "t1": {"description": "先查", "resolution": "resolved", "owners": ["land"], "notes": ""},
        "t2": {"description": "再核", "resolution": "in_progress", "owners": ["land"], "notes": ""},
    }})
    folder = tmp_path / "agent" / "trajectories" / "subagents"
    folder.mkdir(parents=True)
    path = folder / "land.t01.jsonl"

    def write(events: list[dict]) -> None:
        path.write_text("\n".join(json.dumps(item, ensure_ascii=False) for item in events) + "\n", encoding="utf-8")

    def live(node_id: str) -> list[dict]:
        view = build_view(tmp_path)
        node = next(item for item in view["nodes"] if item["id"] == node_id)
        return node["live"]

    write([
        {"t": "start"},
        {"t": "llm", "tool_calls": [{"name": "web_search", "args": {"q": ["出让收入 87051亿元"]}}]},
    ])
    assert live("t1") == []
    assert live("t2")[0]["tool"] == "web_search"
    assert "87051" in live("t2")[0]["label"]

    write([
        {"t": "start"},
        {"t": "llm", "tool_calls": [{"name": "web_search", "args": {"q": ["出让收入 87051亿元"]}}]},
        {"t": "result", "name": "web_search", "result": "找到了"},
    ])
    assert live("t2")[0]["label"] == "【思考】"

    write([
        {"t": "start"},
        {"t": "llm", "tool_calls": [{"name": "web_search", "args": {"q": ["出让收入 87051亿元"]}}]},
        {"t": "result", "name": "web_search", "result": "找到了"},
        {"t": "end"},
    ])
    assert live("t2") == []


def test_empty_graph_agents_use_board_owners(tmp_path: Path) -> None:
    work = tmp_path / "sandbox" / "worktree"
    _write(work / "constellation.json", {
        "version": 2,
        "goal": {"id": "goal", "title": "调研 verl"},
        "nodes": [
            {"id": "t1", "title": "版本", "task_id": "t1", "agents": []},
            {"id": "t2", "title": "算法", "task_id": "t2", "agents": []},
        ],
        "edges": [
            {"from": "goal", "to": "t1", "relation": "parent"},
            {"from": "goal", "to": "t2", "relation": "parent"},
        ],
    })
    _write(work / "task_board.json", {"tasks": {
        "t1": {"description": "版本", "resolution": "resolved", "owners": ["release_research"], "notes": ""},
        "t2": {"description": "算法", "resolution": "resolved", "owners": ["algo_research"], "notes": ""},
    }})
    folder = tmp_path / "agent" / "trajectories" / "subagents"
    folder.mkdir(parents=True)
    (folder / "release_research.t01.jsonl").write_text(
        json.dumps({"t": "result", "name": "web_search", "result": "v0.9.1"}) + "\n",
        encoding="utf-8",
    )
    view = build_view(tmp_path)
    t1 = next(node for node in view["nodes"] if node["id"] == "t1")
    t2 = next(node for node in view["nodes"] if node["id"] == "t2")
    assert t1["agents"] == ["release_research"]
    assert t2["agents"] == ["algo_research"]
    assert t1["steps"][0]["tool"] == "web_search"
    goal = next(node for node in view["nodes"] if node["id"] == "goal")
    assert min(t1["x"], t2["x"]) < goal["x"] < max(t1["x"], t2["x"]) or min(t1["y"], t2["y"]) < goal["y"] < max(t1["y"], t2["y"])
    stored = json.loads((work / "constellation.json").read_text(encoding="utf-8"))
    assert stored["nodes"][0]["agents"] == []


def test_view_joins_notes_and_refuses_to_invent_a_missing_graph(tmp_path: Path) -> None:
    work = tmp_path / "sandbox" / "worktree"
    _write(work / "constellation.json", {
        "version": 3,
        "goal": {"id": "goal", "title": "土地财政怎么转型"},
        "nodes": [
            {"id": "t1", "title": "查土地出让金", "task_id": "t1", "agents": ["land"]},
            {"id": "t2", "title": "核对口径", "task_id": "t2", "agents": []},
        ],
        "edges": [
            {"from": "goal", "to": "t1", "relation": "parent"},
            {"from": "t1", "to": "t2", "relation": "parent"},
            {"from": "t2", "to": "t1", "relation": "challenge"},
        ],
    })
    _write(work / "task_board.json", {
        "tasks": {"t1": {"description": "查土地出让金", "resolution": "resolved", "owners": ["land"], "notes": "87051 亿元"}},
    })
    folder = tmp_path / "agent" / "trajectories" / "subagents"
    folder.mkdir(parents=True)
    (folder / "land.t01.jsonl").write_text(
        json.dumps({"t": "result", "name": "web_search", "result": "找到出让金口径"}) + "\n",
        encoding="utf-8",
    )
    view = build_view(tmp_path)
    assert view["ok"] is True
    assert view["goal"]["title"] == "土地财政怎么转型"
    t1 = next(node for node in view["nodes"] if node["id"] == "t1")
    assert t1["notes"] == "87051 亿元"
    assert t1["steps"][0]["tool"] == "web_search"
    assert {"from": "goal", "to": "t1", "relation": "parent"} in view["edges"]
    assert {"from": "t2", "to": "t1", "relation": "challenge"} in view["edges"]

    bare = tmp_path / "bare"
    bare.mkdir()
    (bare / "agent" / "trajectories" / "subagents").mkdir(parents=True)
    (bare / "agent" / "trajectories" / "subagents" / "land.t01.jsonl").write_text(
        json.dumps({"t": "result", "name": "web_search", "result": "challenge parent fusion"}) + "\n",
        encoding="utf-8",
    )
    missing = build_view(bare)
    assert missing["ok"] is False
    assert missing["message"] == MISSING_GRAPH
    assert missing["edges"] == []
    assert missing["nodes"] == []


def test_goal_title_is_the_user_question(tmp_path: Path) -> None:
    question = "agent memory 的框架现在用哪个效果最好？有什么评测结果吗？"
    work = tmp_path / "sandbox" / "worktree"
    _write(work / "constellation.json", {
        "version": 1,
        "goal": {"id": "goal", "title": f"# Task\n\n{question}\n\n## Submission contract (mandatory)\n\nWork in three phases."},
        "nodes": [{"id": "t1", "title": "查框架", "task_id": "t1", "agents": []}],
        "edges": [{"from": "goal", "to": "t1", "relation": "parent"}],
    })
    view = build_view(tmp_path)
    assert view["goal"]["title"] == question
    assert view["nodes"][0]["title"] == question
    assert "Submission contract" not in view["nodes"][0]["title"]

    trial = tmp_path / "runs" / "sessions" / "s1" / "generation" / "trials" / "1"
    _write(trial / "sandbox" / "worktree" / "constellation.json", {
        "version": 1,
        "goal": {"id": "goal", "title": "主代理改写过的整段说明"},
        "nodes": [],
        "edges": [],
    })
    (trial.parent.parent.parent / "session.json").write_text(
        json.dumps({"question": question, "status": "finished"}, ensure_ascii=False),
        encoding="utf-8",
    )
    shown = _study_view(tmp_path / "runs", trial)
    assert shown["goal"]["title"] == question
    assert shown["nodes"][0]["title"] == question


def test_page_opens_the_trial_report(tmp_path: Path) -> None:
    work = tmp_path / "sandbox" / "worktree"
    _write(work / "constellation.json", {
        "version": 1,
        "goal": {"id": "goal", "title": "题目"},
        "nodes": [{"id": "t1", "title": "查", "task_id": "t1", "agents": []}],
        "edges": [{"from": "goal", "to": "t1", "relation": "parent"}],
    })
    report = tmp_path / "sandbox" / "outputs" / "report.md"
    report.parent.mkdir(parents=True)
    report.write_text("# 结论\n\n土地出让收入下降。\n", encoding="utf-8")
    assert "土地出让收入下降" in (read_report(tmp_path) or "")

    page = Path(__file__).with_name("index.html").read_text(encoding="utf-8")
    assert 'id="open-report"' in page
    assert "renderMarkdown" in page
    assert "prepareUserReport" in page
    assert 'class="prose"' in page
    assert '<pre id="report-body">' not in page
    assert "挑战" in page
    assert "罗纳尔多" not in page
    assert 'endpoint("report")' in page
    assert "node.notes" in page
    assert "satellite" in page
    assert "clearSelection" in page
    assert "node.live" in page
    assert "function ringAmount" in page
    assert "Math.min(0.88" in page

    server = make_server(tmp_path, "127.0.0.1", 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        port = server.server_address[1]
        with urlopen(f"http://127.0.0.1:{port}/api/view") as response:
            body = json.loads(response.read().decode("utf-8"))
        assert body["goal"]["title"] == "题目"
        with urlopen(f"http://127.0.0.1:{port}/api/report") as response:
            assert "土地出让收入下降" in response.read().decode("utf-8")
        with urlopen(f"http://127.0.0.1:{port}/") as response:
            html = response.read().decode("utf-8")
        assert "PiResearch" in html
    finally:
        server.shutdown()
        server.server_close()


def test_home_lists_history_and_starts_a_question(tmp_path: Path) -> None:
    runs = tmp_path / "runs"
    trial = runs / "land" / "generation" / "trials" / "1"
    _write(trial / "sandbox" / "worktree" / "constellation.json", {
        "version": 1,
        "goal": {"id": "goal", "title": "土地财政怎么转型"},
        "nodes": [],
        "edges": [],
    })
    (trial / "result.json").write_text(
        json.dumps({"question": "土地财政怎么转型。", "predicted_answer": "报告"}, ensure_ascii=False),
        encoding="utf-8",
    )
    rows = list_history(runs)
    assert rows[0]["status"] == "finished"
    assert rows[0]["constellation"] is True
    assert "土地财政" in rows[0]["title"]

    started: list[Path] = []

    def starter(path: Path) -> int:
        started.append(path)
        return os.getpid()

    server = make_home_server(runs, "127.0.0.1", 0, starter=starter)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        port = server.server_address[1]
        with urlopen(f"http://127.0.0.1:{port}/") as response:
            page = response.read().decode("utf-8")
        assert "开始研究" in page
        assert "要研究的问题" not in page
        assert "调研中" in page
        assert "已完成" in page
        with urlopen(f"http://127.0.0.1:{port}/api/history") as response:
            history = json.loads(response.read().decode("utf-8"))
        assert history[0]["href"] == "/study/land/1"
        with urlopen(f"http://127.0.0.1:{port}/study/land/1") as response:
            assert "主页" in response.read().decode("utf-8")
        with urlopen(f"http://127.0.0.1:{port}/api/studies/land/1/view") as response:
            view = json.loads(response.read().decode("utf-8"))
        assert view["ok"] is True
        empty = Request(
            f"http://127.0.0.1:{port}/api/questions",
            data=json.dumps({"question": "短"}).encode(),
            headers={"Content-Type": "application/json"},
        )
        try:
            urlopen(empty)
            raise AssertionError("short question should fail")
        except Exception as exc:
            assert getattr(exc, "code", None) == 400
        body = json.dumps({"question": "请比较两种房产税。"}).encode()
        request = Request(
            f"http://127.0.0.1:{port}/api/questions",
            data=body,
            headers={"Content-Type": "application/json"},
        )
        with urlopen(request) as response:
            created = json.loads(response.read().decode("utf-8"))
        assert created["href"].startswith("/study/sessions/")
        assert started
        second = create_session(runs, "另一个问题也可以同时开始。", starter=starter)
        assert second["href"].startswith("/study/sessions/")
        assert second["href"] != created["href"]
        assert second["status"] == "running"
        assert len(started) == 2
    finally:
        server.shutdown()
        server.server_close()


def test_delete_study_removes_one_session(tmp_path: Path) -> None:
    runs = tmp_path / "runs"
    keep = runs / "sessions" / "keep"
    (keep / "generation" / "trials" / "1").mkdir(parents=True)
    (keep / "session.json").write_text(
        json.dumps({"question": "留下", "status": "finished", "pid": 0}, ensure_ascii=False),
        encoding="utf-8",
    )
    gone = runs / "sessions" / "gone"
    (gone / "generation" / "trials" / "1").mkdir(parents=True)
    (gone / "session.json").write_text(
        json.dumps({"question": "删掉", "status": "failed", "pid": 999999}, ensure_ascii=False),
        encoding="utf-8",
    )
    (gone / ".kernel.db").write_text("db", encoding="utf-8")
    sibling = runs / "bundle" / "generation" / "trials"
    (sibling / "1").mkdir(parents=True)
    (sibling / "2").mkdir(parents=True)
    delete_study(runs, "sessions/gone/1")
    delete_study(runs, "bundle/1")
    assert not gone.exists()
    assert (keep / "session.json").is_file()
    assert not (sibling / "1").exists()
    assert (sibling / "2").is_dir()
    try:
        delete_study(runs, "../../etc")
        raise AssertionError("path escaped")
    except FileNotFoundError:
        pass


def test_research_job_uses_the_project_venv(tmp_path: Path) -> None:
    binary = tmp_path / "FrontierAgent" / ".venv" / "bin" / "python"
    binary.parent.mkdir(parents=True)
    binary.write_text("", encoding="utf-8")
    assert job_interpreter(tmp_path) == str(binary)
    assert job_interpreter(tmp_path / "empty") == sys.executable

def test_home_delete_endpoint_removes_requested_study(tmp_path: Path) -> None:
    runs = tmp_path / "runs"
    trial = runs / "sessions" / "remove-me" / "generation" / "trials" / "1"
    trial.mkdir(parents=True)
    session = trial.parent.parent.parent
    (session / "session.json").write_text(
        json.dumps({"question": "测试", "status": "finished", "pid": 0}, ensure_ascii=False),
        encoding="utf-8",
    )
    server = make_home_server(runs, "127.0.0.1", 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        request = Request(
            f"http://127.0.0.1:{server.server_address[1]}/api/studies/sessions/remove-me/1",
            method="DELETE",
        )
        with urlopen(request) as response:
            assert response.status == 200
        assert not session.exists()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
