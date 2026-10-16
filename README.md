# PiResearch

带研究过程可视化的深度调研系统。提交问题后，主 Agent 拆解任务、分派研究、核验资料并生成报告。星图展示任务归属、执行状态和工具调用，报告支持 Markdown 阅读与自适应表格。

## 运行

需要 Python 3.12 和 uv。先安装运行时：

```bash
cd FrontierAgent
uv sync --extra eval
cd ..
```

按 [FrontierAgent 配置说明](FrontierAgent/README.md) 和环境变量示例配置本地模型、搜索服务凭证。主页研究进程读取项目根目录的 `.env`，需配置 `OPENAI_API_KEY`、`OPENAI_MODEL`、`OPENAI_BASE_URL` 和搜索凭证；代理与服务端配置见 `FrontierAgent/.env.example` 及 `FrontierAgent/workflows/agent_team/profiles/drb2_deepseek_v2.yaml`。

启动主页：

```bash
PYTHONPATH=".:FrontierAgent" FrontierAgent/.venv/bin/python -m constellation.serve --home runs --host 127.0.0.1 --port 8765
```

访问 <http://127.0.0.1:8765/>。Enter 提交问题，Shift+Enter 换行。研究会调用已配置的模型和搜索服务。

## 目录

| 路径 | 内容 |
|---|---|
| `constellation/` | 主页、星图、报告阅读器、本地服务与布局 |
| `FrontierAgent/` | Agent 运行时、研究工作流、工具和测试 |
| `drb2-evaluator/` | DeepResearch Bench II 评测工具与数据 |
| `docs/` | 星图设计与实现说明 |
| `run_frontier_benchmark.sh` | 批量研究与评测入口 |

研究过程写入本地 `runs/`，包含任务板、星图、轨迹和报告。仓库不包含运行历史、真实密钥、虚拟环境或缓存。

父子关系表示任务包含关系。主 Agent 在规划结束前复核非主节点下的归属；明确误挂的并列任务或前置依赖可移回主问题，不确定关系保留。程序校验依据、版本和结构，记录修改历史。语义判断仍依赖 Agent。

## 测试

```bash
cd FrontierAgent
uv sync --extra dev
cd ..
PYTHONPATH=".:FrontierAgent" FrontierAgent/.venv/bin/python -m pytest constellation FrontierAgent/tests/test_constellation.py FrontierAgent/tests/test_constellation_prompt.py FrontierAgent/tests/test_constellation_review.py -q
node --test constellation/test_report_tables.mjs
```

基准运行方式见 [FRONTIER_AGENT_DRB2.md](FRONTIER_AGENT_DRB2.md)。所含组件与数据的授权分别见各目录内的 LICENSE、DATA_LICENSE 文件。
