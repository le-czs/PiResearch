# FrontierAgent 调研星图

日期：2026-10-13

给 `deepresearch-drb2-v2` 的 Agent Team 加一张由主代理维护的图，再用一个本地页面把它画出来。画面参照桌面上的 `multica-research-constellation-v2.html`：深色画布、圆、关系线、点开后的抽屉。不做法置信度、节点等级、罗纳尔多对话，也不改已完成的旧实验。

权威分工：

- 图文件只回答「有哪些任务节点、它们如何相连」。
- 任务板回答状态和完成备注。
- 子代理轨迹回答工具步骤。
- `/outputs/report.md` 是唯一的报告入口。

## 文件

一次 trial 的工作区里有两份运行时写入的 JSON：

- `/workspace/constellation.json`：图。主机路径是 `<trial>/sandbox/worktree/constellation.json`。
- `/workspace/task_board.json`：任务板快照。同一目录。

运行开始时写入只有目标节点的图，以及空任务板。`version` 从 1 起，每次成功的 `update_constellation` 加 1。`updated_at` 由运行时填写。主代理不填这两个字段，也不自己选择路径，不用 `create_file` 改这两份文件。

任务板目前只在进程内存里，运行结束会被清掉。`add_task` 和 `update_task` 成功后，运行时把当前板写成 `task_board.json`，前端才能在进程结束后读到状态和备注。快照形状与内存板一致：`{"tasks": {"t1": {"description": "…", "resolution": "open", "owners": [], "notes": ""}}}`。`resolution` 只使用任务板已有的 `open`、`in_progress`、`resolved`、`cancelled`。

坐标不写入文件。前端用节点编号做稳定布局。

```json
{
  "version": 4,
  "updated_at": "2026-10-13T00:00:00Z",
  "goal": { "id": "goal", "title": "题目原文" },
  "nodes": [
    { "id": "t1", "title": "任务描述", "task_id": "t1", "agents": ["land_finance_research"] }
  ],
  "edges": [
    { "from": "goal", "to": "t1", "relation": "parent" },
    { "from": "t13", "to": "t2", "relation": "audit" },
    { "from": "t6", "to": "t5", "relation": "support" },
    { "from": "t7", "to": "t5", "relation": "challenge" },
    { "from": "t1", "to": "t15", "relation": "fusion" }
  ]
}
```

`goal.title` 来自用户题目。主代理不能删除或修改 `goal`。

### 节点

一个节点是一条任务，不是任务备注里的某一句话。`id` 必须等于已登记的任务编号，例如 `t1`。`title` 省略时，运行时抄任务板的 `description`。`agents` 是这一轮派到该任务上的子代理名字，还没派人就是 `[]`。

圆一样大。不写 XXL、XL、L、M、S，也不写来源类型。

### 边

| relation | 方向 | 含义 |
|---|---|---|
| `parent` | 父 → 子 | 谁分解出谁。每个任务节点恰好一条。布局靠它长成树 |
| `audit` | 审核者 → 被审核的任务 | 这个节点负责审核另一个节点的结果 |
| `support` | 支持者 → 被支持的任务 | 这个节点支持另一个节点的结论 |
| `challenge` | 挑战者 → 被挑战的任务 | 这个节点在挑战另一个节点 |
| `fusion` | 旧节点 → 新节点 | 旧节点被融进新节点。旧节点留在图里 |

没有 `link`。支持、挑战、审核、融合都指向整个任务节点。自环、悬空端点、未知关系、重复边都拒绝。

## 工具

主代理只通过 `update_constellation` 改图。一次调用带一组按顺序执行的操作。任一条非法，整次调用不生效，文件保持原样。

操作：

- `add_node`：`{id, title?, agents?}`。`id` 必须是任务板上已有的任务，不能是 `goal`。
- `add_edge`：`{from, to, relation}`。两端当时都要已经在图里。
- `set_agents`：`{id, agents}`。替换该任务节点的代理名单。不能用于 `goal`。
- `remove_edge`：`{from, to, relation}`。
- `remove_node`：`{id}`。不能是 `goal`。该节点上不能还留着边；换父节点时先在同一次调用里删掉旧边。

一次调用结束时检查：每个任务节点恰好有一条 `parent` 边。因此同一次调用里可以先删旧父边再加新父边。

`update_constellation` 在规划阶段就允许，和 `add_task`、`update_task`、`finish_planning` 放在同一允许列表里。

显式 `finish_planning` 时，任务板上每个任务都要有节点和恰好一条父边，否则拒绝结束规划。规划回合用尽后的强制结束仍然放行，运行时把还没有节点的任务补成 `goal` 的子节点，避免图缺父边。提示里写明不要依赖这条补救。

## 主代理提示

规则写进 `FrontierAgent/workflows/agent_team/prompts.py` 的规划段和执行段。子代理的提示不包含这段。执行循环会丢掉规划上下文，所以每一轮用户消息都附上当前的 `constellation.json`。

提示要求：

- 拆任务的同一轮：`add_task` 之后立刻 `add_node`，并加上恰好一条 `parent`。父节点是 `goal`，或是它真正从中拆出来的已有任务。
- 派人的同一轮：`assign_task` 或更换负责人时 `set_agents`。
- 收回报告并作出判断的同一轮：和 `update_task` 一起补 `audit`、`support`、`challenge` 或 `fusion`。融合要先登记新任务和新节点，再从每个旧节点连一条 `fusion`。
- 没有这些变化的回合不要为了刷新文件再调工具。
- 不写坐标，不写置信度，不整文件重写，不改 `goal`。

## 前端

本地页面放在 `deepresearch-drb2-v2` 内，由一个只读的本地服务打开一个 trial 目录。浏览器不能直接读磁盘，所以页面通过这个服务取 JSON、轨迹和报告。它不启动模型，也不修改图。

- 圆和线来自 `constellation.json`。
- 圆的状态和抽屉里的结论来自 `task_board.json` 的 `resolution` 和 `notes`。
- 点开节点后的工具步骤来自该节点 `agents` 里每个子代理的轨迹文件。一个节点有多名代理时，步骤按代理分开列出。
- 「打开报告」打开该 trial 的 `/outputs/report.md`。工作区笔记不挂到节点上。

页面包含：顶栏里的题目、画布、五种关系的图例、缩放、点选节点后的抽屉。抽屉显示标题、代理、状态、备注，以及由 `parent` 边得到的子任务。没有对话栏，没有置信度，没有等级筛选。

旧实验目录没有这两份 JSON 时，页面说明这份运行没有星图，不从轨迹反推关系。

## 测试

- 工具：合法操作写入文件并增加 `version`；非法操作不改文件；父边可以在同一次调用里替换；`goal` 不能删；规划阶段可以调用。
- `finish_planning`：缺父边时拒绝；强制结束时把缺的节点补到 `goal` 下。
- 任务板：`add_task` 和 `update_task` 之后 `task_board.json` 与内存板一致。
- 提示：规划段和执行段都包含何时更新、用哪个工具、文件在 `/workspace/constellation.json`。
- 前端：用一份固定的图和任务板渲染出目标节点、一条父边和一条挑战边，点开节点能看到备注。
