"""Run one homepage question in its own process."""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock


def _load_env(path: Path) -> None:
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("'").strip('"')
        if key and key not in os.environ:
            os.environ[key] = value


def _prepare_env(project: Path, trial: Path) -> None:
    _load_env(project / ".env")
    _load_env(project.parent / ".env")
    if not os.environ.get("OPENAI_API_KEY") and os.environ.get("ZHIZENGZENG_OPENAI_API_KEY"):
        os.environ["OPENAI_API_KEY"] = os.environ["ZHIZENGZENG_OPENAI_API_KEY"]
    os.environ["SANDBOX_BACKEND"] = "native"
    os.environ["FRONTIER_AGENT_WORKSPACE_DIR"] = str(trial / "sandbox" / "worktree")
    os.environ["FRONTIER_AGENT_OUTPUTS_DIR"] = str(trial / "sandbox" / "outputs")
    os.environ["FRONTIER_AGENT_INPUTS_DIR"] = str(trial / "sandbox" / "inputs")


def _write_session(path: Path, **changes: object) -> None:
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload.update(changes)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


async def _run(session_dir: Path) -> None:
    project = Path(__file__).resolve().parents[1]
    frontier = project / "FrontierAgent"
    sys.path[:0] = [str(project), str(frontier)]
    os.chdir(frontier)

    session_path = session_dir / "session.json"
    session = json.loads(session_path.read_text(encoding="utf-8"))
    trial = session_dir / "generation" / "trials" / "1"
    task = session_dir / "generation" / "tasks" / "1"
    _prepare_env(project, trial)
    if not os.environ.get("OPENAI_API_KEY"):
        raise RuntimeError("没有找到模型密钥，研究没有启动。")

    from benchmarks.public.harbor_agent.agent import FrontierAgentAgent
    from harbor.models.agent.context import AgentContext

    agent = FrontierAgentAgent(logs_dir=trial, model_name=None)
    await agent.setup(MagicMock(spec=[]), db_path=str(session_dir / ".kernel.db"))
    started = time.monotonic()
    await agent.run(task.joinpath("instruction.md").read_text(encoding="utf-8"), MagicMock(), AgentContext())
    answer_path = trial / "agent" / "final_answer.txt"
    predicted = answer_path.read_text(encoding="utf-8").strip() if answer_path.is_file() else ""
    result = {
        "question_id": "1",
        "question": str(session.get("question") or "")[:200],
        "answer_type": "report",
        "predicted_answer": predicted,
        "duration_seconds": round(time.monotonic() - started, 2),
        "error": None,
        "judge_method": "external_pending",
    }
    (trial / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    _write_session(session_path, status="finished", error="")


def main() -> None:
    session_dir = Path(sys.argv[1]).resolve()
    session_path = session_dir / "session.json"
    try:
        asyncio.run(_run(session_dir))
    except Exception as exc:
        if session_path.is_file():
            _write_session(session_path, status="failed", error=str(exc))
        raise


if __name__ == "__main__":
    main()
