# 百炼接入与复现

供应商：阿里云百炼；地域：华北2（北京）；模型：`qwen-plus`；用户授权整个项目累计付费调用最多 10 元。业务资料都是合成数据。

## 本机配置和运行

Python 3.12 标准库通过 HTTPS 调用 OpenAI 兼容协议，不安装 SDK 或 Agent 框架。环境变量须在启动 Python 的进程内有效；`.env.example` 只是说明，本项目不自动读取 `.env`。

先在 Windows 用户环境变量中配置 `DASHSCOPE_API_KEY`，再在 PowerShell 中运行（不打印密钥）：

```powershell
$env:DASHSCOPE_API_KEY = [Environment]::GetEnvironmentVariable('DASHSCOPE_API_KEY', 'User')
$env:PYTHONPATH = (Resolve-Path ./src).Path
```

跟随 README 的四条 `run` 命令准备新数据库后，将离线运行命令替换为：

```powershell
python -m schedule_agent agent --db ./var/demo.sqlite3 --task-id DEMO --model bailian --max-steps 10 --trace ./var/bailian-demo.jsonl
```

必须用尚不存在的 Trace 路径。冲突待确认时退出码 2 属于正确结果。默认地址为用户提供的 `https://ws-vc9m33bi2n59pg1q.cn-beijing.maas.aliyuncs.com/compatible-mode/v1`；其他评审账号可设置 `BAILIAN_BASE_URL` 为自己北京业务空间的同类 HTTPS 地址，或官方北京传统地址。地址中不含密钥。

密钥只放 HTTP Authorization，不进入请求上下文、Trace 或文档；拒绝 HTTP、非百炼北京域名和跨地址重定向。HTTP 错误不输出供应商原始正文。

## 模型职责

每次请求同时提供四类工具和 `finish` 的函数 schema、当前素材/任务、真实历史工具观察。`tool_choice=required` 只要求返回一次工具选择，不指定下一步函数；模型决定具体动作及参数。`enable_thinking=false`、`stream=false`、`max_tokens=2048`；不采集内部思维链。reason 只要求简短行动依据。

模型响应必须恰好一个完整工具调用。未知动作、跨任务参数、伪造引用、超长或被截断的响应会失败，不能转成成功。工具执行和用户确认入口保持原边界。正常已确认稿保存后，编排器可终止；其他情况模型可以选择结束。模型并不拥有确认或发布工具。

## 成本控制

固定账本 `var/bailian-budget.sqlite3` 在每次发送前通过 SQLite 写事务预留 0.10 元。第四阶段升级为策略 v2：完整、有效 usage 按输入 1 元/百万 tokens、输出 3 元/百万 tokens 保守结算；未得到有效 usage 的调用继续占用全部预留，不自动重试。按整数微元保证“已结算保守费用 + 未结算预留 + 本次预留 ≤ 10 元”。因此不再将成功的低成本调用永久按 0.10 元计费，也没有固定 100 次成功调用上限；100 次未结算调用仍会耗尽额度。

升级保留全部旧调用行和实际用量，并原子记录策略版本；不清空账本、不扩大用户授权金额。旧文档里的“剩 4 次”是升级前历史状态。[迁移证据](../examples/phase4/budget-migration.json) 记录旧 96 行逐行一致。**不要删除或替换此账本来重跑项目**。更换输出目录只隔离业务库，不重置累计预算。预算迁移与业务数据库 v1→v2 升级是两项不同机制。

请求 JSON 不超过 60000 UTF-8 字节，输出最多 2048 tokens。2026-10-29 查阅的 [qwen-plus 北京官方价格](https://help.aliyun.com/zh/model-studio/qwen-plus) 在输入不超过 128k 时，非思考输入 0.8 元/百万 tokens、输出 2 元/百万 tokens；以请求字节数加协议余量作为保守输入估计，每次 0.10 元预留高于本版预计调用成本。模型或价格变化后需要重新审查此政策。

摘要中的 estimated_yuan 按上述官方原价计算估算，不折算免费额度或缓存优惠，**不是实付账单**；settled_conservative_yuan 则使用更高的 1/3 费率进行预算核算。没有 usage 的调用仍保留全部预留。控制台费用是最终依据。该限制仅覆盖本项目适配器，无法限制用户在其他程序中消耗同一个账户，也不能在价格改变后继续保证同样的金额上界；变更模型或费率前需重新核查。

## 可复现付费评估

以下命令会实际消耗模型额度，请确认授权和预算后运行；每次指定新的目录：

```powershell
python tests/record_live_evidence.py --scenarios S01 S06 S03 S10 --output examples/my-live-run
```

脚本初始化独立业务库，模型动态决定动作；保存失败使用明确标注的 SQLite 触发器注入，原键恢复和日历替换属于测试脚本的直接操作，不冒称为模型决策。预期不满足时停止后续场景，保留失败输出与 Trace。该脚本不会被 unittest 自动发现，不会在普通测试中付费。

实际证据索引与测试结果见 [评估文档](03-evaluation.md)。目前检验的是合成业务任务上的有限样本，不能外推为生产成功率。

第四阶段采用会保留失败并继续统计的批量入口（每次用新的 output 名称；对应的 var/phase4-名称 也须不存在）：

```powershell
# 免费：18 场景，各 3 轮
python tests/phase4_evaluate.py --model mock --output examples/my-offline-evaluation --rounds 3
# 付费：第一轮 18 场景，后两轮各 8 个核心场景，共 34 次场景执行
python tests/phase4_evaluate.py --model bailian --output examples/my-live-evaluation --rounds 3
```

普通断言失败保留在分母；配置、认证、预算或冻结文件变更会停止评估。退出码 0 表示全部通过，1 表示存在场景失败，2 表示提前停止。确认、故障恢复及日历变更由测试程序明确模拟，不由模型自行授权。最终统计和限制见 [第四阶段评估](09-phase4-evaluation.md)。
