# 素材审核与发布排期 Agent

当前已实现统一基础层、四类业务工具、独立用户确认、受限 Agent 循环、离线 Mock，以及阿里云百炼 `qwen-plus` 真实模型适配。真实验证与费用见 [百炼接入记录](docs/08-bailian.md)。系统只保存草稿，不执行发布。GitHub 交付仓库为 [xiaoying-material-scheduling-agent](https://github.com/ziming520-suring/xiaoying-material-scheduling-agent)，版本以检出的 commit SHA 为准。

第四阶段结果：107 项自动测试全部通过；18 个离线场景各 3 轮共 54 次全部通过；真实模型 34 次场景执行中 33 次通过，8 个核心场景各 3 轮全通过。无空位场景出现一次被工具拒绝的无效保存尝试，完整保留为失败。详见 [实际评估与限制](docs/09-phase4-evaluation.md)、[测试日志](examples/phase4/test-results.txt)、[机器索引](examples/phase4/report.json)。

阅读入口：[业务字段、工具与确认契约](docs/06-business-contract.md)、[数据集与 18 个场景](data/README.md)、[题面验收映射](docs/07-acceptance-map.md)、[第四阶段实际评估](docs/09-phase4-evaluation.md)。

交卷入口：[题号、评审步骤、实际结果与未完成项](SUBMISSION.md)。完整题面和已确认规则已收录在本仓库 docs 中。

## VS Code 直接运行

用 VS Code 打开 **project1 文件夹**，安装 Microsoft 的 Python 扩展（包含 Python Debugger）。首次用 `Ctrl+Shift+P` → `Python: Select Interpreter` 选择 Python 3.12。本机已有的解释器路径是 `C:\Users\余淼\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe`；其他机器选择自己的 Python 3.12。

打开根目录 `run_demo.py`，点击右上角 **Run Python File** 三角按钮即可运行默认离线演示；也可以按 `F5`，选择 **素材排期：一键演示（离线，不收费）**。这两种方式都会自动初始化独立演示库、加载冲突场景、创建 DEMO 任务并运行 Agent，无需逐条输入命令。每次使用新的 `var/demo-.../`，其中有 `result.json`、`trace.jsonl` 和数据库；待确认结果的退出码 2 是正常业务状态，不是 Python 崩溃。

F5 还提供 **素材排期：真实百炼（消耗模型预算）**，使用相同示例和共享预算账本。该选项需要 VS Code 进程继承已配置的 `DASHSCOPE_API_KEY`（设置后可完全退出并重开 VS Code）；不会创建新预算或自动确认。第四阶段已将旧调用预留升级为保留历史的用量结算，累计授权仍为 10 元，详见 [预算说明](docs/08-bailian.md)。不要删除账本。直接运行默认仍是免费的离线演示。

## 本地业务演示

在 project1 目录，用新数据库和新 Trace 文件运行以下 PowerShell 命令（不需要网络或密钥）：

```powershell
$env:PYTHONPATH = (Resolve-Path ./src).Path
New-Item -ItemType Directory -Path ./var -Force | Out-Null
python -m schedule_agent run --db ./var/demo.sqlite3 --input ./examples/initialize.json
python -m schedule_agent run --db ./var/demo.sqlite3 --input ./examples/business/upgrade.json
python -m schedule_agent run --db ./var/demo.sqlite3 --input ./examples/business/load-conflict.json
python -m schedule_agent run --db ./var/demo.sqlite3 --input ./examples/business/task.json
python -m schedule_agent agent --db ./var/demo.sqlite3 --task-id DEMO --model mock --max-steps 24 --trace ./var/demo-trace.jsonl
python -m schedule_agent drafts --db ./var/demo.sqlite3 --task-id DEMO
```

本演示的模拟时钟是 2026-10-28 北京时间 17:00，与运行机器当前日期无关。18:00 已占用；Mock 会根据查询结果建议 19:00，保存待确认稿且不占位，任务 incomplete，Agent 退出码为 2。这是预期行为，不能显示为“已发布”。

重复前四个准备写请求会命中重放，不重置现有状态。重新运行 Agent 必须指定新的 Trace 文件名；已有 Trace 不会被覆盖。若要重做全新实验，请换新的数据库路径，不要删除旧库冒充升级。

## 明确确认草稿

先运行 `drafts` 查看当前 draft_id、version、suggested_slot_id、required_risk_ids。用户接受这份具体建议后，按真实返回填写 UTF-8 JSON 文件，例如 `var/confirm.json`：

```json
{
  "schema_version": 1,
  "request_id": "confirm-demo-1",
  "operation": "draft.confirm",
  "payload": {
    "draft_id": "替换为真实draft_id",
    "expected_draft_version": 1,
    "accepted_slot_id": "S19",
    "accepted_risk_ids": []
  }
}
```

这只是填写模板，不是可直接提交的真实确认。版本、时段和风险列表都必须以当前草稿为准。执行：

```powershell
python -m schedule_agent confirm --db ./var/demo.sqlite3 --input ./var/confirm.json
```

只有独立 confirm 命令能提交用户确认；普通 run 和模型工具通道拒绝确认。工具会重新检查资料、规则、时段和版本。确认成立也只是草稿占位，仍未发布。

## 环境与运行

Python 3.12，标准库 SQLite，无第三方依赖。离线 Mock 不需要网络或密钥；真实百炼模式需要 HTTPS 网络与 DASHSCOPE_API_KEY，运行方法及累计 10 元限制见 [接入说明](docs/08-bailian.md)。本机实测 Python 3.12.14 / SQLite 3.53.1 / Windows；其他系统尚未实测。

在本目录运行 PowerShell：

```powershell
$env:PYTHONPATH = (Resolve-Path ./src).Path
New-Item -ItemType Directory -Path ./var -Force | Out-Null
python -m schedule_agent run --db ./var/runtime.sqlite3 --input ./examples/initialize.json
python -m schedule_agent run --db ./var/runtime.sqlite3 --input ./examples/advance.json
python -m schedule_agent run --db ./var/runtime.sqlite3 --input ./examples/advance.json
python -m schedule_agent clock --db ./var/runtime.sqlite3
```

第三个写命令重放同一请求，返回与前一次完全相同的结果，不再次推进时钟。初始化到 `2026-10-28T09:00:00Z`，推进到 `2026-10-28T10:00:00Z`，时钟版本为 2。再次执行整组命令不会重置时钟。

macOS/Linux 的对应方式（尚未实测）：

```sh
export PYTHONPATH="$PWD/src"
mkdir -p var
python3 -m schedule_agent run --db ./var/runtime.sqlite3 --input ./examples/initialize.json
python3 -m schedule_agent run --db ./var/runtime.sqlite3 --input ./examples/advance.json
python3 -m schedule_agent clock --db ./var/runtime.sqlite3
```

无需执行 pip install。`requirements.txt` 说明运行时零第三方依赖；`.env.example` 列出在线模式变量，离线无需密钥，本项目不自动读取 .env。源码运行使用的 `PYTHONPATH` 不是模型密钥。

当前这台电脑的默认 `python` 命令不可用，已验证 Codex 自带解释器可用。仅在本机可将上述 `python` 换成以下调用；评审机器仍使用正常安装的 Python 3.12：

```powershell
$taskPython = Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
& $taskPython -X utf8 -m unittest discover -s tests -v
# 完成前面的 PYTHONPATH 和 var 目录设置后，也可运行 CLI：
& $taskPython -m schedule_agent run --db ./var/runtime.sqlite3 --input ./examples/initialize.json
```

CLI 的 stdout 是一份 UTF-8 JSON；stderr 为基础操作事件 JSONL。退出码：成功 0、请求或前置条件拒绝 2、存储或处理失败 3、提交结果不确定 4。`--help` 输出普通帮助文本。

## 自动验证与实际记录

```powershell
python -m unittest discover -s tests -v
python tests/record_evidence.py
python tests/validate_stage2.py
python tests/record_business_evidence.py
```

测试自己定位 src，无需依赖已安装包。证据脚本使用全新的临时数据库，不修改手工运行的 `var/runtime.sqlite3`；重写 `examples/foundation` 中本工具生成的测试报告和 Trace。

- [测试结果](examples/foundation/test-results.txt) / [机器可读摘要](examples/foundation/test-summary.json)
- [正常操作与重放](examples/foundation/01-normal-replay.json)
- [非法输入与未初始化](examples/foundation/02-invalid-uninitialized.json)
- [保存失败、回滚与重试](examples/foundation/03-storage-failure-retry.json)
- [提交不确定与恢复](examples/foundation/04-unknown-recovery.json)
- [仅用于测试的到期对象](examples/foundation/05-test-only-due-processing.json)

以上是实际基础运行证据；故障注入方式单独标明。它们不包含模型动作或模型决策依据，不能冒充题目要求的 ReAct 记录。

`validate_stage2.py` 仅静态核查设计资料；data/scenarios 中的 `execution_status=not_run` 保留为设计基线，不用实际输出覆盖预期。第三阶段实际执行记录另存 [业务记录摘要](examples/phase3/summary.json)：18 组真实本地工具与 SQLite 操作，模型部分全部是显式 OfflineMock。证据脚本重写自己生成的 examples/phase3 文件，不修改手工演示数据库。

Agent 默认全任务 24 次模型动作，每步至多调用一个工具；可用 `--max-steps` 指定 1..1000。上限时保留成功项并报告 step_limit。Trace 记录动作、参数、结果和简短依据，不记录模型内部思维。遇到 unknown 时停止该项并保留 write_request 中的原键原参；先将该完整 request 写入 UTF-8 JSON，再用 run 原键核对，不能重新生成请求键来猜测保存结果。

Agent 退出码：全部完成 0；未完成/待确认 2；确定保存失败 3；保存结果不确定 4。已确认草稿再次运行只读返回，不重复写入。

## 评审入口

- [需求与范围](docs/01-requirements.md)
- [方案与事务边界](docs/02-solution.md)
- [验证、验收映射及限制](docs/03-evaluation.md)
- [AI 使用记录](docs/04-ai-usage.md)
- [复盘与下一步](docs/05-retrospective.md)
- [实现计划](docs/implementation-plan.md) / [进度记录](docs/progress.md)

确认后的完整规则见 [项目规则](docs/project1-rules.md)，原始题面见 [考核题面](docs/exam.md)；核心契约也摘要于本项目文档。仓库检出后无需访问本机上级目录。

## 使用边界

- 所有时间必须带日期、秒和时区，内部与输出统一 UTC。未初始化不会读取系统时间作为默认值。
- 重放身份是数据库范围内的 `(operation, request_id)`；成功记录永久保留。改参数必须用新键。
- `unknown` 不是失败：请保留原键原参重试核对，不要换新键执行。
- SQLite 负责本机多进程写入串行化，默认等锁 5 秒，不承诺跨机器网络文件系统、外部副作用或断电硬件可靠性。
- 第一版没有业务到期对象；时钟到达发布时间不会发布、确认或改变草稿。
- 交卷时附完整 GitHub URL 与 commit SHA；在本仓库运行 `git rev-parse HEAD` 获取所检出版本，历史阶段记录中的“未提交”是当时状态。
