# 分阶段验证记录

以下基础层部分保留第一阶段时点的结果；第二、三阶段验证依次追加在文末。真实模型动态路径仍未执行，不能由本地工具或 Mock 测试替代。

## 可复制命令

```text
python -m unittest discover -s tests -v
python tests/record_evidence.py
```

实际运行结果以 [test-summary.json](../examples/foundation/test-summary.json) 和 [test-results.txt](../examples/foundation/test-results.txt) 为准。生成脚本先运行整套测试，失败则停止生成场景记录。

## 验收映射

| 规则 | 测试与实际证据 |
|---|---|
| B01 | test_validation 全部边界用例、test_runtime 两项 B01、CLI 非法输入与 UTF-8；[输入错误记录](../examples/foundation/02-invalid-uninitialized.json) |
| B02–B04 | 初始化重放、同键异参、等价时区用例；[正常与重放](../examples/foundation/01-normal-replay.json) |
| B05–B08 | 未初始化只读、相同时间、旧成功重放、倒退拒绝的对应测试 |
| B09 | SQLite trigger 中止 replay INSERT，核对整个数据库快照；[失败重试记录](../examples/foundation/03-storage-failure-retry.json) |
| B10 | 两个真实独立进程同键同参／异参，另有持锁超时测试；见测试报告 |
| B11 | 新进程读取旧成功记录，不重复变更时钟；见测试报告 |
| B12–B13 | 测试对象临界点与部分执行回滚；[测试到期记录](../examples/foundation/05-test-only-due-processing.json) |
| B14–B15 | 提交后的返回异常和恢复读失败，在真实 SQLite commit 周围注入故障；[不确定结果记录](../examples/foundation/04-unknown-recovery.json) |
| B16 | 生产配置推进时没有到期对象、settled_count 为 0；真实 CLI 记录和对应测试 |

额外验证损坏／陌生／不兼容数据库不覆盖、中文与特殊字符路径、缺参数、缺文件、stdout JSON 和退出码。并发测试使用实际进程与共享数据库，不以 mock 调用次数证明防重复。

独立审查后新增五项回归测试：历史响应空字段／NaN／重复键／错误类型、初始化结果版本不符、重放表列重排和缺主键。五项均先复现失败，再修复；全套共 42 项通过。参见 [审查与修复记录](review.md)。

## 发现与限制

- 开发中发现 SQLite 的连接上下文只管理事务，不自动关闭连接；Windows 删除临时数据库时暴露泄漏。已对测试辅助连接与生产连接初始化异常路径显式关闭，再执行整套验证。
- 提交确认丢失、读取恢复失败与到期处理器失败采用明确的测试故障注入；没有声称实际模拟了断电或硬盘故障。
- B12–B13 的到期对象仅是测试夹具，不是新增业务过期规则。
- 当前只实测 Windows、Python 3.12.14、SQLite 3.53.1。无真实模型决策、无业务排期、无跨机器并发验证。
- 三项基础能力的测试通过不等于整道考核达标；完整题目尚需模型动作与工具观察的实际 Trace。

## 第二阶段实际核查（业务尚未运行）

2026-10-28 在相同本机 Python 3.12.14 环境实际执行：

| 核查 | 实际结果 | 证据 |
|---|---|---|
| `python tests/validate_stage2.py --output examples/phase2/data-validation.json` | 通过；8 素材、2 补充、5 规则、4 时段、5 日历、18 场景；A01–A06 全覆盖 | [静态核查报告](../examples/phase2/data-validation.json)（含文件 SHA-256） |
| 临时副本内修改未知素材、重复占位、待确认冒充完成、预期冒充已运行 | 4 项均被核查脚本拒绝；原资料不变 | [反例探针](../examples/phase2/checker-negative-probes.json) |
| `python -X utf8 -m unittest discover -s tests -v` | 42 tests，0 failures / 0 errors，8.956 秒 | [本阶段基础回归](../examples/phase2/foundation-regression.txt) |
| 与第一阶段快照逐字节比较源码和原 test_*.py | 5 个源码文件与 3 个原测试文件均未改变 | [保留核查](../examples/phase2/foundation-preservation.json) |

静态脚本不执行业务规则、事务或模型，也不验证自然语言 assertions 中每条语义；语义由设计审查核对，后续必须转为可执行业务测试。反例探针只是核查脚本的测试，不能当素材审核 Agent 的工具失败证据。独立审查及修正记录见 [第二阶段记录](phase2-progress.md)。

独立审查发现的两个设计问题已修正：场景使用实际版本增量，不限制合法模型调整次数；草稿保存不可变审核依据快照，避免资料更新后只剩不可逆摘要。修正后重新运行 1,880 项静态检查通过；[审查报告](phase2-review.md) 明确区分反例推演与尚未执行的业务测试。

业务测试输入和预期见 [验收映射](07-acceptance-map.md)。S01–S18 的 execution_status 均为 not_run，尚无业务实际输出、模型 Trace、成功率或费用结果。上述基础回归耗时不能视为项目总开发用时。

## 第三阶段本地实际结果（2026-10-29）

第二阶段场景文件继续作为预期基线；本阶段实际输出另存 examples/phase3，不覆盖预期。

| 验证 | 实际结果 | 文件 |
|---|---|---|
| 全部 unittest | 83 项通过，0 failures、0 errors；14.202 秒 | [完整输出](../examples/phase3/test-results.txt)、[摘要及源码 SHA-256](../examples/phase3/test-summary.json) |
| 18 个合成场景 | 本地工具实际执行，最终草稿数量/状态/候选/占位投影与预期一致，另有文件列明的中间断言 | [汇总](../examples/phase3/summary.json) |
| 6 条 CLI 命令 | 初始化、升级、加载、建任务、离线 Agent、查询草稿均返回预期退出码与 JSON | [CLI 记录](../examples/phase3/cli-smoke.json) |
| 独立代码审查 | 3 项 Important 已修复，新增回归通过；无待处理 Minor | [审查与修复](phase3-review.md) |

各必验实际记录：[正常](../examples/phase3/S01-actual.json)、[可补资料](../examples/phase3/S02-actual.json)、[无法补资料](../examples/phase3/S03-actual.json)、[阻断](../examples/phase3/S04-actual.json)、[提示风险及确认](../examples/phase3/S05-actual.json)、[时段冲突](../examples/phase3/S06-actual.json)、[重复请求](../examples/phase3/S09-actual.json)、[保存失败恢复](../examples/phase3/S10-actual.json)。额外记录覆盖确认竞争、无时段、改占用重规划、批量部分成功、来源冲突、多风险、时间边界、版本绑定、unknown 和读取故障。

每个 actual 文件明确列出输入、agent_runs、独立确认/测试写操作、最终草稿和故障注入；不要将操作前的 Agent 输出当成后续确认后的最终状态。实际动作日志文件名写在 trace_files 中；例如 [正常 Trace](../examples/phase3/S01-run-1.jsonl)、[补查 Trace](../examples/phase3/S02-run-1.jsonl)、[冲突 Trace](../examples/phase3/S06-run-1.jsonl)。

**模型部分全部为 OfflineMock**。这些证明真实本地工具、事务和离线编排流程，不能证明真实模型因观察结果改变决策。业务工具使用合成资料，故障由 SQLite trigger 或明确的测试适配器注入，不声称模拟真实平台故障或断电。

以上是本地阶段的历史记录。本地 18 场景通过不代表完整考核通过；随后完成的真实模型接入记录如下。总开发工时未完整计时，不用测试耗时替代。

## 百炼真实模型验证（2026-10-29）

采用用户选定的北京 `qwen-plus`，调用工作空间 OpenAI 兼容接口。模型选择动作、工具真实执行、结果回传模型，不使用 OfflineMock。业务数据仍为合成资料，不对外发布。完整 [汇总及源码哈希](../examples/bailian-report/summary.json)、[最终测试输出](../examples/bailian-report/test-results.txt) 均已落盘。

| 题面/扩展场景 | 真实结果与证据 |
|---|---|
| 正常排期 A01 | 读取→检查→查询→保存；原时段 S18 confirmed 并占位。[实际输出](../examples/bailian-final/S01-actual.json)、[Trace](../examples/bailian-final/S01-run-1.jsonl) |
| 缺资料可补查 A02 | 模型观察缺授权后改为 complete 读取，再次检查后保存。[实际输出](../examples/bailian-final/S02-actual.json)、[Trace](../examples/bailian-final/S02-run-1.jsonl) |
| 缺资料不可补查 A02 | needs_information；列出 caption、rights_status，无草稿。[实际输出](../examples/bailian-v3/S03-actual.json)、[Trace](../examples/bailian-v3/S03-run-1.jsonl) |
| 阻断风险 A03 | blocked；模型检查后结束，无草稿。[实际输出](../examples/bailian-v3/S04-actual.json) |
| 时段冲突 A04 | 原要求仍 S18；建议替代时段并保存 pending_confirmation、不占位，模型随后结束。[实际输出](../examples/bailian-final/S06-actual.json)、[Trace](../examples/bailian-final/S06-run-1.jsonl) |
| 重复提交 A05 | 模型保存后，测试程序原键原参重复返回相同响应且只有一稿。[实际输出](../examples/bailian-v3/S09-actual.json) |
| 保存失败 A06 | SQLite 触发器注入失败；模型两次保存均失败后结束；无占位，错误明确。恢复由测试程序原键执行，保留其最后替代建议为待确认，不自动确认。[完整原输出](../examples/bailian-failure-final/S10-actual.json)、[Trace](../examples/bailian-failure-final/S10-run-1.jsonl)、[后续只读核验](../examples/bailian-report/S10-follow-up-validation.json) |
| 改占用后重新规划 | 测试程序将占用从 S18 改为 S19，再次模型运行改回 S18，更新同一草稿。[实际输出](../examples/bailian-v3/S11-actual.json)、[第二次 Trace](../examples/bailian-v3/S11-run-2.jsonl) |

正常、补查和冲突三条路径展示了不同观察引起不同后续动作。最终状态由本地工具/数据库生成，不采用模型自行声称的成功。失败恢复、重复提交、日历替换是测试程序明确执行的操作，不冒充模型决策或真实用户确认。

### 失败和修复的完整口径

共保留 **19 组开发期间的场景尝试**，原断言结果有 **6 组未通过**；这是边开发边修复的样本，不能作为固定版本无偏成功率。挑选覆盖表不代表抹去原失败。

1. [初版 S03](../examples/bailian-live/S03-actual.json)：模型在缺资料时重复读取、尝试保存；工具拒绝，没有越权落库。改用原生 assistant/tool 历史，明确 ok 与 review_status 的区别。
2. [第二版 S03](../examples/bailian-v2/S03-actual.json)：模型合理提前请求补充，但汇总遗漏缺口；新增失败回归后修复，只用可信 read 缺失字段生成待补充项，不编造规则结果。
3. S02 的 [第一条协议失败](../examples/bailian-v3/S02-actual.json)、[复查失败](../examples/bailian-supplement-recheck/S02-actual.json)、[结构诊断](../examples/bailian-protocol-diagnostic/S02-run-1.jsonl)：诊断运行定位 missing_reason，历史回传遗漏原依据可能诱导这一输出；将真实 reason 保留到历史上下文后，最终 S02 实测成功。前两次仅有通用错误码，不能倒推出完全相同的具体原因。
4. [最终 S10 原断言](../examples/bailian-failure-final/S10-actual.json)仍为 failed：脚本假设恢复后必为 confirmed，但模型最后尝试的是替代时段 S19，正确恢复结果应为 pending_confirmation、不占位。原文件及其 SHA-256 保留，另做只读核验确认失败无写入、原键重复一致、唯一草稿、硬要求未改变、替代建议仍待确认。验证器增加该分支及越权占位/篡改重放反例，未来评估不再强制模型采用固定方案。这个恢复结果仍不算完成排期。

早期 v3 S10 虽满足保存失败和原键恢复断言，也出现了模型协议错误，汇总中的 model_errors 保留它。最终 S10 已由模型明确 finish，未出现该错误。

### 测试与费用

最终本地回归 **97 项通过**（包含原 42 项基础测试），所有普通 unittest 都不调用付费模型。已验证错误密钥配置、HTTP 错误不泄漏原文、响应校验、缺口提前返回、预算重启与并发、失败预留不退款等。独立 AI 审查与审查后修复说明见 [审查记录](bailian-review.md)。

累计 **96 次真实模型调用**：输入 183,948 tokens、输出 7,867 tokens。按记录的非思考原价估算 **0.1628924 元**，不是已核对账单；累计保守预留 **9.60 元 / 10 元**，失败调用也计入预留。账本保持原位，未重置；仅剩 4 次调用预留，本轮停止新增调用。估算低于预留是保守预算设计，不代表可以自动重置账本扩大次数。

### 当前限制与后续

这批证据覆盖核心六项及扩展路径，第三阶段核心实现和真实模型验证已交付。以上保留第三阶段时点的结论和预算口径，后续第四阶段见下文。模型在存储失败时尝试替代时段并不能修复存储故障，属于计划质量问题；工具正确保持待确认和失败边界。GitHub 仓库、最终 commit SHA 与完整交卷核查仍属于后续阶段。

## 第四阶段最终结果（2026-10-29）

当前评审入口为 [第四阶段评估](09-phase4-evaluation.md)：107 项测试通过，最终离线 18×3=54 次全部通过；真实 34 次场景执行中 33 次通过，8 个核心场景各 3 轮全通过。S08 无空位却尝试保存，被工具拒绝且没有占位，最终返回 save_failed，与预期不同；保留失败，不称全部验收通过。

真实 34 次使用相同运行时源码、提示词和参数，记录 170 次新增 API 调用。独立审查后只完善评估脚本及证据记录，最终运行时与真实摘要指纹一致；旧脚本有原样快照。文件索引、指纹、分母和全部失败见 [report.json](../examples/phase4/report.json)，测试日志按题面放在 [examples/phase4](../examples/phase4/test-results.txt)。

预算策略升级为有效 usage 保守结算，原 96 行保留；当前累计 266 次调用，按用量估算 0.4536844 元（非账单），按保守费率核算 0.577982 元，授权余额 9.422018 元。此前“仅剩 4 次”是旧永久预留算法的历史结论，不是当前限制。授权上限仍 10 元，未重置账本。
