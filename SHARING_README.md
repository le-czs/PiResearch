# 本机共享：DRB2 v2 方法代码

此目录是 2026-10-08 的只读共享副本。其他 Linux 用户可复制到自己的目录：

```bash
cp -r /srv/shared/deepresearch-drb2-v2 ~/deepresearch-drb2-v2
chmod -R u+rwX ~/deepresearch-drb2-v2
cd ~/deepresearch-drb2-v2
```

包含 FrontierAgent 源码、drb2_deepseek_v2 配置、DRB2 评测器与数据、运行脚本。
原始 LICENSE 和 DATA_LICENSE 已保留。没有真实 .env、API 密钥和虚拟环境。实验记录已加入 runs/，详见 runs/EXPERIMENTS_README.md。

## 配置与运行

在自己的副本中建立配置并填写自己的模型和搜索凭证：

```bash
cp nvidia-aiq/deploy/.env.example nvidia-aiq/deploy/.env
cp FrontierAgent/.env.example FrontierAgent/.env
cp drb2-evaluator/.env.deepseek.example drb2-evaluator/.env
chmod 600 nvidia-aiq/deploy/.env FrontierAgent/.env drb2-evaluator/.env
```

生成模型使用 nvidia-aiq/deploy/.env，搜索凭证使用 FrontierAgent/.env，评审配置使用 drb2-evaluator/.env。
需要安装 uv；脚本会建立自己的 Python 环境。

```bash
./run_frontier_benchmark.sh --check
RUN_NAME=my_drb2_v2 LIMIT=10 PIPELINE=agent_team_report PROFILE=drb2_deepseek_v2 ./run_frontier_benchmark.sh
```

运行会调用你配置的模型和搜索服务。详细说明见 FRONTIER_AGENT_DRB2.md。
此共享副本不会自动同步原目录的后续修改。
