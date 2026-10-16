# FrontierAgent 调研星图 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 主代理用 `update_constellation` 维护一次 trial 的星图，本地页面只读地把它画出来。

**Architecture:** 图和任务板快照都写在 trial 的 `sandbox/worktree/`。工具校验操作后再落盘。前端服务读取这两份 JSON、子代理轨迹和 `/outputs/report.md`，不调用模型。

**Tech Stack:** Python 3、FrontierAgent `@tool`、stdlib HTTP、单页 HTML/JS。

**Spec:** `deepresearch-drb2-v2/docs/superpowers/specs/2026-10-13-frontier-agent-constellation-design.md`

## Global Constraints

- 只修改 `deepresearch-drb2-v2`。
- 不写置信度、节点等级、`link`、坐标。
- 不从旧实验反推关系。
- 主代理不自己选路径，不用 `create_file` 改星图。
- 每个任务节点恰好一条 `parent`。
- 不提交 git。

---

### Task 1: 星图工具

**Files:**
- Create: `FrontierAgent/plugins/tools/constellation.py`
- Modify: `FrontierAgent/plugins/tools/__init__.py`
- Modify: `FrontierAgent/plugins/tools/task_board.py`
- Test: `FrontierAgent/tests/test_constellation.py`
- Test: `FrontierAgent/tests/test_tool_registry.py`

**Interfaces:**
- Produces: `init_constellation(directory, title)`, `update_constellation(ops)`, `repair_missing_parents(directory, tasks)`, `render_constellation(directory)`, `persist_task_board(directory, tasks)`, `finish_blocked(directory, task_ids) -> str | None`

- [ ] 写失败测试：合法操作增加 version；非法操作不改文件；同一次调用可替换父边；`goal` 不能删；缺父边时 `finish_blocked` 返回错误；`repair_missing_parents` 把缺的任务挂到 `goal`。
- [ ] 实现 `constellation.py` 并注册工具。
- [ ] `add_task` / `update_task` 保存 `notes`，并在 scope 有 `constellation_dir` 时写 `task_board.json`。
- [ ] `finish_planning` 在目录已设置且父边不全时拒绝。
- [ ] `pytest tests/test_constellation.py tests/test_tool_registry.py -q`

### Task 2: 提示与主循环

**Files:**
- Modify: `FrontierAgent/workflows/agent_team/prompts.py`
- Modify: `FrontierAgent/workflows/agent_team/nodes/main_agent.py`
- Modify: `FrontierAgent/workflows/agent_team/profiles/*.yaml` 的 `main_agent_tools`
- Test: `FrontierAgent/tests/test_constellation_prompt.py`

- [ ] 规划段和执行段包含何时调用、工具名、`/workspace/constellation.json`。
- [ ] 运行开始时初始化图；`constellation_dir` 放进 scope；规划强制结束后补父边；执行用户消息附上当前图。
- [ ] 五个 agent team profile 的工具列表加入 `update_constellation`。
- [ ] `update_constellation` 加入 `_PLANNING_ALLOWED`。

### Task 3: 只读页面

**Files:**
- Create: `constellation/layout.py`
- Create: `constellation/serve.py`
- Create: `constellation/index.html`
- Test: `constellation/test_view.py`

- [ ] `place_constellation` 按父边把同一编号放在稳定位置。
- [ ] `build_view` 合并任务板备注；没有星图文件时返回说明，不读轨迹关系。
- [ ] 页面画出目标、父边、挑战边；点开节点显示备注；打开报告读取 `sandbox/outputs/report.md`。
- [ ] 从 `deepresearch-drb2-v2` 运行 `python -m pytest constellation/test_view.py FrontierAgent/tests/test_constellation.py FrontierAgent/tests/test_constellation_prompt.py -q`。
