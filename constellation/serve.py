"""Serve one trial's constellation. The page cannot change the graph."""

from __future__ import annotations

import argparse
import json
import re
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from constellation.home import create_session, delete_study, list_history, resolve_trial
from constellation.layout import GOAL_RADIUS, HUB_RADIUS, RADIUS, place_constellation
from constellation.node_size import apply_call_radii

_TONES = ("green", "cyan", "violet", "amber", "blue", "teal")

MISSING_GRAPH = "这份运行没有星图，页面不会从轨迹反推关系。"
_TASK_HEAD = re.compile(r"^#\s*Task\s*", re.I)
_CONTRACT = re.compile(r"\n*##\s*Submission contract\b.*", re.I | re.S)
_HERE = Path(__file__).resolve().parent
_INDEX = _HERE / "index.html"
_HOME = _HERE / "home.html"
_STEP_LIMIT = 120
_LIVE_STALE_SECONDS = 30 * 60


def _user_facing_question(text: str) -> str:
    cleaned = _TASK_HEAD.sub("", str(text or "").strip())
    return _CONTRACT.sub("", cleaned).strip()


def _remember_question(payload: dict[str, Any], question: str) -> None:
    question = question.strip()
    if not question or not isinstance(payload.get("goal"), dict):
        return
    payload["goal"]["title"] = question
    for node in payload.get("nodes") or []:
        if node.get("goal"):
            node["title"] = question


def _session_for(trial: Path) -> dict[str, Any] | None:
    session_path = trial.parent.parent.parent / "session.json"
    if not (session_path.is_file() and session_path.parent.parent.name == "sessions"):
        return None
    try:
        session = json.loads(session_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return session if isinstance(session, dict) else None


def _load_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _graph_path(trial: Path) -> Path:
    return trial / "sandbox" / "worktree" / "constellation.json"


def _board_path(trial: Path) -> Path:
    return trial / "sandbox" / "worktree" / "task_board.json"


def report_path(trial: Path) -> Path:
    return trial / "sandbox" / "outputs" / "report.md"


def read_report(trial: Path) -> str | None:
    path = report_path(trial)
    if not path.is_file():
        return None
    return path.read_text(encoding="utf-8")


def _cut(text: str, limit: int) -> str:
    compact = " ".join(str(text or "").split())
    if len(compact) <= limit:
        return compact
    window = compact[:limit]
    for separator in (" ", "、", "，", ",", "/"):
        index = window.rfind(separator)
        if index >= max(6, limit // 2):
            return _trim_cut(window[:index])
    return _trim_cut(window)


def _trim_cut(text: str) -> str:
    cleaned = text.rstrip(" ，,、/（(")
    parts = cleaned.split()
    while parts and parts[-1].lower() in {"on", "of", "the", "and", "for", "with", "to", "in", "a"}:
        parts.pop()
    return " ".join(parts) or cleaned


def _snippet(text: str, limit: int = 180) -> str:
    compact = " ".join(str(text or "").split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 1] + "…"


def _first_text(value: Any) -> str:
    if isinstance(value, list):
        return _first_text(value[0]) if value else ""
    return str(value or "").strip()


_SUBJECTS = (
    "先租后售",
    "改善型住房",
    "房价收入比",
    "保障性住房",
    "土地使用权",
    "出让收入",
    "土地财政",
    "房地产税",
    "房产税",
    "共有产权",
    "普通住宅",
    "容积率",
    "差饷",
)


def _figure(text: str) -> str:
    for match in re.finditer(r"\d+(?:\.\d+)?\s*(?:万亿元|万亿|亿元|亿|%)?", text):
        raw = re.sub(r"\s+", "", match.group(0))
        digits = re.sub(r"\D", "", raw.split(".", 1)[0])
        if raw.endswith(("亿", "%", "万")) or len(digits) >= 5:
            return raw
    return ""


def _subject(text: str) -> str:
    for name in _SUBJECTS:
        if name in text:
            return name
    head = text.split()[0] if text.split() else text
    head = re.sub(r"^\d{4}年?", "", head) or head
    return _snippet(head, 12)


def _mostly_latin(text: str) -> bool:
    letters = [ch for ch in text if ch.isalpha()]
    if not letters:
        return False
    return sum(ch.isascii() for ch in letters) / len(letters) > 0.7


def _action(verb: str, detail: str, limit: int = 26) -> str:
    """Keep the action in brackets, then as much of the subject as fits."""
    prefix = f"【{verb}】"
    body = " ".join(str(detail or "").split())
    if not body or body == verb:
        return prefix
    text = f"{prefix}{body}"
    if len(text) <= limit:
        return text
    room = max(limit - len(prefix), 6)
    return f"{prefix}{_cut(body, room)}"


def _search_label(query: str) -> str:
    text = " ".join(query.split())
    if not text:
        return "【查询】"
    if _mostly_latin(text):
        stop = {"theory", "efficiency", "neutrality", "effects", "and", "the", "of", "on", "for", "with"}
        words = [word for word in text.split() if word.lower() not in stop]
        return _action("查询", " ".join(words[:4]), 32)
    noun = _subject(text)
    figure = _figure(text)
    if figure and figure not in noun:
        return _action("查询", f"{noun} {figure}", 24)
    return _action("查询", noun, 22)


def _clause(text: str) -> str:
    piece = re.split(r"[，,：:;；。（(]", text, maxsplit=1)[0].strip()
    if _mostly_latin(piece):
        return _cut(piece, 28)
    return _cut(piece, 14)


def _path_name(value: str) -> str:
    name = Path(value).name
    if name.endswith(".md"):
        name = name[:-3]
    return name.replace("_", " ") or value


def _bash_label(command: str) -> str:
    line = " ".join(command.strip().splitlines()[:1])
    line = " ".join(line.split())
    if not line:
        return "【执行】"
    if re.match(r"python", line):
        script = ""
        for part in line.split()[1:]:
            if part.startswith("<<"):
                break
            if part.startswith("-"):
                continue
            script = _path_name(part)
            break
        return _action("执行", script or "python")
    if line.startswith("SRC="):
        return "【整理】来源"
    if line.startswith("mkdir"):
        paths = re.findall(r"/[^\s;]+", line)
        return _action("新建", _path_name(paths[-1]) if paths else "目录")
    if line.startswith("cd "):
        target = line.split()[1] if len(line.split()) > 1 else ""
        return _action("进入", _path_name(target) if target else "目录")
    if line.startswith("ls"):
        paths = re.findall(r"/[^\s;]+", line)
        return _action("查看", _path_name(paths[-1]) if paths else "目录")
    if line.startswith("find "):
        names = re.findall(r"-name\s+(\S+)", line)
        paths = re.findall(r"/[^\s;]+", line)
        target = names[0].strip("'\"") if names else (_path_name(paths[-1]) if paths else "文件")
        return _action("查找", target)
    if re.match(r"wc\b", line):
        files = [part for part in line.split()[1:] if not part.startswith("-")]
        return _action("统计", _path_name(files[-1]) if files else "字数")
    word = line.split()[0]
    named = {
        "grep": "查找",
        "rg": "查找",
        "cat": "读取",
        "head": "读取",
        "tail": "读取",
        "cp": "复制",
        "mv": "移动",
        "rm": "删除",
    }
    paths = re.findall(r"/[^\s;]+", line)
    target = _path_name(paths[-1]) if paths else ""
    return _action(named.get(word, "执行"), target or word)


def _call_label(name: str, args: Any) -> str:
    """One glanceable line: the action, then what it was about."""
    payload = args if isinstance(args, dict) else {}
    if name == "web_search":
        return _search_label(_first_text(payload.get("q")))
    if name == "web_fetch":
        extract = _first_text(payload.get("info_to_extract"))
        if extract:
            return _action("浏览", _clause(extract), 24)
        url = _first_text(payload.get("url")).split("?")[0].rstrip("/")
        return _action("浏览", url.rsplit("/", 1)[-1] or "网页", 24)
    if name == "bash":
        return _bash_label(_first_text(payload.get("command")))
    if name == "read_file":
        return _action("读取", _path_name(_first_text(payload.get("path"))), 24)
    if name == "create_file":
        return _action("写入", _path_name(_first_text(payload.get("path"))), 24)
    if name == "submit_report":
        headings = []
        for line in str(payload.get("content") or "").splitlines():
            heading = line.strip().lstrip("#").strip()
            if heading:
                headings.append(heading)
            if len(headings) >= 8:
                break
        for heading in headings:
            lowered = heading.lower().strip(":")
            if lowered.startswith(("scope", "completeness", "summary", "overview")):
                continue
            if not _mostly_latin(heading):
                return _action("提交", _clause(heading), 24)
        return "【提交】"
    if name == "压缩":
        return "【压缩】"
    return name or "工具"


def _uniquify_labels(steps: list[dict[str, Any]]) -> None:
    used: set[str] = set()
    for step in steps:
        label = str(step.get("label") or "")
        full = str(step.pop("_full", "") or "")
        tokens = []
        for token in re.split(r"[\s，,、：:；;]+", full):
            if not token or "/" in token or token in {"cd", "ls", "mkdir", "python3"}:
                continue
            if token in label or label in token:
                continue
            if any(token[i : i + 2] in label for i in range(max(len(token) - 1, 0))):
                continue
            tokens.append(token)
        candidate = label
        index = 0
        while candidate in used:
            if index < len(tokens):
                candidate = _cut(f"{label} {tokens[index]}", 18)
                index += 1
                continue
            candidate = f"{label} {sum(item.startswith(label) for item in used) + 1}"
            break
        step["label"] = candidate
        used.add(candidate)


def _collapse_navigation(steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    folded: list[dict[str, Any]] = []
    for step in steps:
        previous = folded[-1] if folded else None
        label = str(step.get("label") or "")
        navigation = label.startswith(("【进入】", "【查看】", "【新建】", "【查找】"))
        if (
            previous
            and navigation
            and previous.get("tool") == step.get("tool")
            and previous.get("label") == label
        ):
            previous["count"] = int(previous.get("count") or 1) + 1
            continue
        item = dict(step)
        item["count"] = 1
        folded.append(item)
    return folded


def agent_steps(trial: Path, agent: str) -> list[dict[str, Any]]:
    """Tool calls and context compressions from this agent's trajectory.

    Empty when there are none. Calls stay in trajectory order. A short label
    is taken from the matching model tool call when that call was recorded.
    """
    folder = trial / "agent" / "trajectories" / "subagents"
    if not folder.is_dir() or not agent:
        return []
    files = sorted(folder.glob(f"{agent}.jsonl")) + sorted(folder.glob(f"{agent}.t*.jsonl"))
    steps: list[dict[str, Any]] = []
    for path in files:
        pending: list[dict[str, Any]] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            kind = event.get("t")
            if kind == "llm":
                pending.extend(call for call in (event.get("tool_calls") or []) if isinstance(call, dict))
                continue
            if kind == "compaction":
                before = event.get("tokens_before")
                after = event.get("tokens_after")
                label = (
                    f"【压缩】{_token_mark(before)} → {_token_mark(after)}"
                    if before and after
                    else "【压缩】"
                )
                steps.append({
                    "agent": agent,
                    "tool": "压缩",
                    "label": label,
                    "_full": label,
                    "detail": _snippet(str(event.get("summary") or "")) or label,
                    "error": False,
                })
            elif kind == "result":
                name = str(event.get("name") or "").strip()
                if not name:
                    continue
                args = None
                for index, call in enumerate(pending):
                    if call.get("name") == name:
                        args = pending.pop(index).get("args")
                        break
                payload = args if isinstance(args, dict) else {}
                full = _first_text(
                    payload.get("q")
                    or payload.get("info_to_extract")
                    or payload.get("command")
                    or payload.get("path")
                    or payload.get("content")
                    or ""
                )
                steps.append({
                    "agent": agent,
                    "tool": name,
                    "label": _call_label(name, args),
                    "hint": _snippet(full, 90),
                    "_full": full,
                    "detail": _snippet(str(event.get("result") or event.get("error") or "")),
                    "error": bool(event.get("error")),
                })
            else:
                continue
            if len(steps) >= _STEP_LIMIT:
                break
        if len(steps) >= _STEP_LIMIT:
            break
    folded = _collapse_navigation(steps)
    _uniquify_labels(folded)
    return folded


def _read_events(path: Path) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict):
            events.append(event)
    return events


def _trajectory_files(trial: Path, agent: str) -> list[Path]:
    folder = trial / "agent" / "trajectories" / "subagents"
    if not folder.is_dir() or not agent:
        return []
    return sorted(folder.glob(f"{agent}.jsonl")) + sorted(folder.glob(f"{agent}.t*.jsonl"))


def live_calls(trial: Path, agent: str) -> list[dict[str, Any]]:
    """Tools this agent is using right now.

    A finished trajectory (an ``end`` event, or a file left untouched) contributes
    nothing. In-flight calls are the ones announced by the model and not yet
    followed by a result. Between calls, the agent is thinking.
    """
    running: list[tuple[Path, list[dict[str, Any]]]] = []
    for path in _trajectory_files(trial, agent):
        events = _read_events(path)
        if not events or any(event.get("t") == "end" for event in events):
            continue
        running.append((path, events))
    if not running:
        return []
    path, events = running[-1]
    if time.time() - path.stat().st_mtime > _LIVE_STALE_SECONDS:
        return []
    pending: list[dict[str, Any]] = []
    for event in events:
        kind = event.get("t")
        if kind == "llm":
            pending.extend(call for call in (event.get("tool_calls") or []) if isinstance(call, dict))
            continue
        if kind != "result":
            continue
        name = str(event.get("name") or "").strip()
        for index, call in enumerate(pending):
            if call.get("name") == name:
                pending.pop(index)
                break
    if not pending:
        return [{"tool": "思考", "label": "【思考】", "hint": "正在想下一步", "error": False, "count": 1}]
    current: list[dict[str, Any]] = []
    for call in pending[:2]:
        name = str(call.get("name") or "").strip() or "工具"
        args = call.get("args")
        payload = args if isinstance(args, dict) else {}
        full = _first_text(
            payload.get("q")
            or payload.get("info_to_extract")
            or payload.get("command")
            or payload.get("path")
            or payload.get("content")
            or ""
        )
        current.append({
            "tool": name,
            "label": _call_label(name, args),
            "hint": _snippet(full, 90),
            "error": False,
            "count": 1,
        })
    return current


def _attach_live(trial: Path, nodes: list[dict[str, Any]]) -> None:
    groups: dict[str, list[dict[str, Any]]] = {}
    for node in nodes:
        node["live"] = []
        if node.get("goal"):
            continue
        agent = node["agents"][0] if node.get("agents") else ""
        groups.setdefault(agent, []).append(node)

    def task_order(node: dict[str, Any]) -> tuple[int, str]:
        digits = "".join(ch for ch in str(node.get("id") or "") if ch.isdigit())
        return (int(digits) if digits else 10**9, str(node.get("id") or ""))

    for agent, members in groups.items():
        if not agent:
            continue
        calls = live_calls(trial, agent)
        if not calls:
            continue
        members.sort(key=task_order)
        working = [node for node in members if node.get("status") == "in_progress"]
        target = working[0] if working else members[0]
        target["live"] = calls


def _token_mark(value: Any) -> str:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return str(value or "")
    if number >= 10000:
        text = f"{number / 10000:.1f}万"
        return text.replace(".0万", "万")
    return str(number)


def _with_board_owners(node: dict[str, Any], tasks: dict[str, Any]) -> dict[str, Any]:
    """Use task-board owners when the graph never recorded an agent.

    The file on disk is left as the main agent wrote it. Placement and colors
    still need a name, and the board is the assignment the same agent already made.
    """
    copy = dict(node)
    named = [str(name).strip() for name in (copy.get("agents") or []) if str(name).strip()]
    if named:
        return copy
    task = tasks.get(str(copy.get("id") or "")) or {}
    owners = [str(name).strip() for name in (task.get("owners") or []) if str(name).strip()]
    if owners:
        copy["agents"] = owners
    return copy


def build_view(trial: Path) -> dict[str, Any]:
    """Join the graph, the board snapshot, and tool steps.

    A trial without ``constellation.json`` returns an explanation and no edges.
    Trajectories are not consulted in that case.
    """
    document = _load_json(_graph_path(trial))
    if document is None:
        return {
            "ok": False,
            "message": MISSING_GRAPH,
            "goal": None,
            "nodes": [],
            "edges": [],
            "radius": RADIUS,
            "report": False,
        }
    board = _load_json(_board_path(trial)) or {"tasks": {}}
    tasks = board.get("tasks") or {}
    raw_nodes = list(document.get("nodes") or [])
    raw_edges = list(document.get("edges") or [])
    laid_out = [_with_board_owners(node, tasks) for node in raw_nodes]
    children: dict[str, list[str]] = {}
    for edge in raw_edges:
        if edge.get("relation") == "parent":
            children.setdefault(str(edge.get("from") or ""), []).append(str(edge.get("to") or ""))
    titles = {"goal": _user_facing_question(str((document.get("goal") or {}).get("title") or ""))}
    agents_of: dict[str, list[str]] = {}
    for node in laid_out:
        node_id = str(node.get("id") or "")
        titles[node_id] = str(node.get("title") or node_id)
        agents_of[node_id] = [str(name) for name in (node.get("agents") or []) if str(name).strip()]
    seen_agents: list[str] = []
    for node_id in sorted(agents_of, key=lambda item: (int("".join(ch for ch in item if ch.isdigit()) or "0"), item)):
        agent = agents_of[node_id][0] if agents_of[node_id] else ""
        if agent and agent not in seen_agents:
            seen_agents.append(agent)
    agent_tone = {agent: _TONES[index % len(_TONES)] for index, agent in enumerate(seen_agents)}
    hubs: set[str] = set()
    grouped: dict[str, list[str]] = {}
    for node_id in sorted(agents_of, key=lambda item: (int("".join(ch for ch in item if ch.isdigit()) or "0"), item)):
        agent = agents_of[node_id][0] if agents_of[node_id] else ""
        if not agent:
            continue
        grouped.setdefault(agent, []).append(node_id)
    for members in grouped.values():
        if members:
            hubs.add(members[0])

    def pack(node_id: str, *, goal: bool) -> dict[str, Any]:
        task = tasks.get(node_id) or {}
        names = [] if goal else agents_of.get(node_id, [])
        steps: list[dict[str, Any]] = []
        for name in names:
            steps.extend(agent_steps(trial, name))
        if goal:
            radius, tone, weight = GOAL_RADIUS, "origin", "origin"
        elif not names:
            radius, tone, weight = RADIUS, "pending", "node"
        elif node_id in hubs:
            radius, tone, weight = HUB_RADIUS, agent_tone.get(names[0] if names else "", "blue"), "hub"
        else:
            radius, tone, weight = RADIUS, agent_tone.get(names[0] if names else "", "blue"), "node"
        return {
            "id": node_id,
            "title": titles.get(node_id) or node_id,
            "goal": goal,
            "x": 0.0,
            "y": 0.0,
            "radius": radius,
            "tone": tone,
            "weight": weight,
            "agents": names,
            "status": "" if goal else str(task.get("resolution") or "open"),
            "notes": "" if goal else str(task.get("notes") or ""),
            "children": [
                {"id": child, "title": titles.get(child) or child}
                for child in children.get(node_id, [])
            ],
            "steps": steps,
        }

    nodes = [pack("goal", goal=True)]
    for node in raw_nodes:
        node_id = str(node.get("id") or "")
        if node_id and node_id != "goal":
            nodes.append(pack(node_id, goal=False))
    _attach_live(trial, nodes)
    apply_call_radii(nodes)
    # Radii have to exist before seating. Circles grow with tool calls, and
    # the seats are what keep those circles from landing on each other.
    positions = place_constellation(
        [
            {"id": node["id"], "title": node.get("title") or "", "agents": node.get("agents") or [], "radius": node.get("radius")}
            for node in nodes
        ],
        raw_edges,
    )
    for node in nodes:
        point = positions.get(str(node["id"])) or {"x": 0.0, "y": 0.0}
        node["x"] = point["x"]
        node["y"] = point["y"]
    return {
        "ok": True,
        "message": "",
        "goal": {"id": "goal", "title": titles["goal"]},
        "nodes": nodes,
        "edges": [
            {
                "from": str(edge.get("from") or ""),
                "to": str(edge.get("to") or ""),
                "relation": str(edge.get("relation") or ""),
            }
            for edge in raw_edges
        ],
        "radius": RADIUS,
        "report": report_path(trial).is_file(),
        "updated_at": document.get("updated_at") or "",
        "version": document.get("version") or 1,
    }


def _study_view(runs: Path, trial: Path) -> dict[str, Any]:
    payload = build_view(trial)
    session = _session_for(trial) or {}
    if payload.get("ok"):
        question = str(session.get("question") or "").strip()
        if question:
            _remember_question(payload, question)
        payload["running"] = session.get("status") == "running"
        return payload
    if session.get("status") == "running":
        payload["message"] = "研究已经开始，星图会在主 agent 派发任务后出现。"
    elif session.get("status") == "failed":
        payload["message"] = str(session.get("error") or "这次研究没有完成。")
    return payload


def make_home_server(
    runs: Path,
    host: str = "127.0.0.1",
    port: int = 8765,
    starter: Any = None,
) -> ThreadingHTTPServer:
    root = runs.resolve()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args: Any) -> None:
            return

        def _send(self, status: int, body: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _json(self, status: int, payload: Any) -> None:
            self._send(status, json.dumps(payload, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

        def _study(self, path: str, suffix: str) -> Path | None:
            marker = "/api/studies/"
            if not path.startswith(marker) or not path.endswith(suffix):
                return None
            study = path[len(marker):-len(suffix)].strip("/")
            return resolve_trial(root, study)

        def do_GET(self) -> None:  # noqa: N802
            path = urlparse(self.path).path
            if path in ("/", "/index.html"):
                self._send(200, _HOME.read_bytes(), "text/html; charset=utf-8")
                return
            if path.startswith("/study/"):
                self._send(200, _INDEX.read_bytes(), "text/html; charset=utf-8")
                return
            if path == "/api/history":
                self._json(200, list_history(root))
                return
            trial = self._study(path, "/view")
            if trial is not None:
                if not trial.is_dir():
                    self._json(404, {"ok": False, "message": "没有这项研究。"})
                    return
                self._json(200, _study_view(root, trial))
                return
            trial = self._study(path, "/report")
            if trial is not None:
                text = read_report(trial)
                if text is None:
                    self._send(404, "这份运行还没有报告。".encode("utf-8"), "text/plain; charset=utf-8")
                    return
                self._send(200, text.encode("utf-8"), "text/plain; charset=utf-8")
                return
            self._send(404, b"not found", "text/plain; charset=utf-8")

        def do_POST(self) -> None:  # noqa: N802
            path = urlparse(self.path).path
            if path != "/api/questions":
                self._send(404, b"not found", "text/plain; charset=utf-8")
                return
            length = int(self.headers.get("Content-Length") or "0")
            if length > 100_000:
                self._json(413, {"error": "问题太长了。"})
                return
            try:
                payload = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
            except json.JSONDecodeError:
                self._json(400, {"error": "提交的内容无法读取。"})
                return
            try:
                kwargs = {"starter": starter} if starter is not None else {}
                created = create_session(root, str(payload.get("question") or ""), **kwargs)
            except ValueError as exc:
                self._json(400, {"error": str(exc)})
                return
            except RuntimeError as exc:
                self._json(409, {"error": str(exc)})
                return
            self._json(200, created)

        def do_DELETE(self) -> None:  # noqa: N802
            path = urlparse(self.path).path
            marker = "/api/studies/"
            if not path.startswith(marker) or path == marker:
                self._send(404, b"not found", "text/plain; charset=utf-8")
                return
            study = path[len(marker):].strip("/")
            try:
                delete_study(root, study)
            except FileNotFoundError as exc:
                self._json(404, {"error": str(exc)})
                return
            self._json(200, {"ok": True})

    return ThreadingHTTPServer((host, port), Handler)


def make_server(trial: Path, host: str = "127.0.0.1", port: int = 8765) -> ThreadingHTTPServer:
    root = trial.resolve()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args: Any) -> None:
            return

        def _send(self, status: int, body: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802
            path = urlparse(self.path).path
            if path in ("/", "/index.html"):
                self._send(200, _INDEX.read_bytes(), "text/html; charset=utf-8")
                return
            if path == "/api/view":
                payload = json.dumps(build_view(root), ensure_ascii=False).encode("utf-8")
                self._send(200, payload, "application/json; charset=utf-8")
                return
            if path == "/api/report":
                text = read_report(root)
                if text is None:
                    self._send(404, "这份运行还没有报告。".encode("utf-8"), "text/plain; charset=utf-8")
                    return
                self._send(200, text.encode("utf-8"), "text/plain; charset=utf-8")
                return
            self._send(404, b"not found", "text/plain; charset=utf-8")

    return ThreadingHTTPServer((host, port), Handler)


def main() -> None:
    parser = argparse.ArgumentParser(description="Open the research homepage, or one trial's constellation.")
    parser.add_argument("trial", nargs="?", type=Path, help="Trial directory, the parent of sandbox/")
    parser.add_argument("--home", type=Path, help="Runs directory. Serves the homepage and every study under it.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    if args.home:
        server = make_home_server(args.home, args.host, args.port)
        label = f"runs={args.home.resolve()}"
    elif args.trial:
        server = make_server(args.trial, args.host, args.port)
        label = f"trial={args.trial.resolve()}"
    else:
        parser.error("pass a trial directory, or --home RUNS")
        return
    host, port = server.server_address[:2]
    print(f"constellation http://{host}:{port}/  {label}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
