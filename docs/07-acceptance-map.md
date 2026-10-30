# 题面 → 场景 → 验收预期

下表保留第二阶段的输入与预期基线；如今已完成本地与真实模型执行，实际结果见本文末尾和 [第四阶段评估](09-phase4-evaluation.md)。预期文件中的 not_run 是设计标记，不代表实际记录未运行，也不能把预期当作实测。题面六项必须验收原文为：“正常排期；素材信息缺失；规则风险；时段冲突；重复提交；保存失败。”

| 编号／题面要求 | 场景输入与试验安排 | 预期与需要保留的实际证据 |
|---|---|---|
| A01 正常排期 | [S01](../data/scenarios/S01.json)、[S12](../data/scenarios/S12.json) | 原时段无风险直接保存并占位；批量成功项不被其他失败回滚 |
| A02 素材信息缺失 | [S02](../data/scenarios/S02.json)、[S03](../data/scenarios/S03.json) | 可补资料须有来源并重检；无法补全则列缺口，不编造、不占位 |
| A03 规则风险 | [S04](../data/scenarios/S04.json)、[S05](../data/scenarios/S05.json)、[S13](../data/scenarios/S13.json)、[S14](../data/scenarios/S14.json) | 阻断不可豁免；提示风险确认前不占位，确认后仍保留风险；来源冲突可追溯；多项问题不被覆盖 |
| A04 时段冲突 | [S06](../data/scenarios/S06.json)、[S07](../data/scenarios/S07.json)、[S08](../data/scenarios/S08.json)、[S11](../data/scenarios/S11.json) | 替代建议保留原要求、待确认；确认前新冲突拒绝；无空位明确未完成；变更占用后动作和建议随结果改变 |
| A05 重复提交 | [S09](../data/scenarios/S09.json)、[S10](../data/scenarios/S10.json)、[S11](../data/scenarios/S11.json)、[S17](../data/scenarios/S17.json) | 同键重放、重启、新键重复建议、重新规划与故障恢复均不创建第二份同任务同素材草稿 |
| A06 保存失败 | [S10](../data/scenarios/S10.json)、[S12](../data/scenarios/S12.json)、[S17](../data/scenarios/S17.json) | 失败明确输出且原子回滚；保留其他成功项；unknown 不能冒充失败或成功，原键核对 |

## 额外边界

| 场景／后续测试 | 预期 |
|---|---|
| [S15](../data/scenarios/S15.json) 时间边界 | 时钟前进不自动确认、不过期、不发布；新确认时间 <= 当前逻辑时间则拒绝 |
| [S16](../data/scenarios/S16.json) 确认精确绑定 | 缺少风险确认拒绝；更新建议后旧版本确认拒绝；不占位 |
| [S18](../data/scenarios/S18.json) 读取工具失败 | 不把工具不可用当审核通过；有限尝试或停止，完整保留错误 |
| 后续契约单元测试 | 非法字段、未登记素材、非未来时间、未提供时段、同 task_id 异参、同键异参、确认参数重复风险 ID 均有明确结果 |
| 后续授权入口测试 | 模型工具列表不包含确认、导入、改日历和发布；普通 run 不能执行 draft.confirm |
| 后续并发保存／确认测试 | 两进程争同一时段最多一个成功；同业务不同键也只有一份草稿；跨表已有排期与 confirmed 草稿无冲突 |
| 后续迁移测试 | v1→v2 成功保留旧时钟/重放；中途故障全回滚；旧成功请求仍能重放；不支持或损坏结构不自动重建 |
| 后续资料变更测试 | 旧 material_ref/check_ref 拒绝；重新读取重检后方能保存；确认后不自动改期或释放 |
| 后续历史依据测试 | 当前素材与规则升级后，旧 confirmed 草稿仍完整返回原审核快照；可恢复原文、来源版本、规则并重建 refs；模型无法篡改快照 |
| 后续执行上限测试 | 达到明确的步数或工具次数上限，返回 incomplete/step_limit，保留已成功项 |

以上“后续”描述保留设计时态；对应可执行测试已在第三、四阶段建立，见 tests/ 中业务、Agent、迁移、并发和基础测试。

## 正常、冲突、确认、重复、失败的契约推演

1. **正常 S01：** task.create 登记硬性 S18 → 读取/规则检查通过 → 时段查询空闲 → save 事务再次核验 → confirmed、占位、保存成功。模型仍需自己选择实际动作，箭头不是运行 Trace。
2. **冲突 S06：** 查到 S18 被占 → 模型提出任一可用同日替代 → save 返回 pending_confirmation → 不占位、任务 incomplete、明确等待用户。
3. **确认 S05/S07：** 用户根据真实稿件版本、时段、风险提交独立确认 → 同事务重检 → 可用则确认占位；被抢占则 SLOT_CONFLICT，旧稿不变。
4. **重复 S09/S11：** 已成功键命中历史结果；新的调用键由业务唯一性返回同稿或更新待确认稿。新的占用条件要新查工具，不能把旧重放当最新状态。
5. **失败 S10/S17：** 已知未提交则无部分写入、原键重试；已提交但无法核对则 unknown，保持原键原参恢复；不提前宣布保存成功。

版本断言绑定真实输出，不能假定模型第一次运行只保存一次。例如第一次运行先保存 S19 再合法更新 S20，第二次运行恢复原 S18，最终 version=3 也可合格；断言要求每次有效变更加 1、去重/拒绝不增加版本，以及始终只有一份草稿。S05/S11/S16 采用这一口径。

## 后续评估证据要求

至少三组输入、实际输出和 Trace，覆盖所有六项；建议至少执行上述全部场景。至少两条真实模型路径体现观察结果导致动作变化，例如 S01 对比 S06，或 S02 补查后再审。对 S11 更换占用重跑，核对工具返回及下一步行为。不能只提交固定分支或预设日志。

每条真实 Trace 记录步骤、工具参数、返回摘要、后续动作及简短决策依据，不记录模型内部思维过程。离线 Mock 可验证流程，必须与真实模型验证分开标记。保存失败、unknown 和确认拒绝要保留中间实际输出，不能只截取最终成功。

第二阶段只完成资料静态核查及基础回归。后续实际记录另存，不改写设计预期。

## 第四阶段实际证据索引

以下链接均指实际执行结果；每项的 trace_files 对应同目录 JSONL，包含参数、结果和后续动作。重复轮次与未通过项完整计入 [真实评估汇总](../examples/phase4/live/summary.json)，没有只挑成功记录。

| 题面要求 | 实际结果与关键边界 |
|---|---|
| 正常排期 | [S01](../examples/phase4/live/round-1/S01-actual.json)：原时段保存、占位；[S12](../examples/phase4/live/round-1/S12-actual.json)：批量保留成功项 |
| 素材信息缺失 | [S02](../examples/phase4/live/round-1/S02-actual.json)：补读、重检；[S03](../examples/phase4/live/round-1/S03-actual.json)：明确缺口、不编造 |
| 规则风险 | [S04](../examples/phase4/live/round-1/S04-actual.json)：阻断；[S05](../examples/phase4/live/round-1/S05-actual.json)：提示风险需独立确认；S13/S14 检查来源冲突与多问题 |
| 时段冲突 | [S06](../examples/phase4/live/round-1/S06-actual.json)：替代待确认；[S07](../examples/phase4/live/round-1/S07-actual.json)：确认竞争拒绝；[S11](../examples/phase4/live/round-1/S11-actual.json)：占用改变后重规划；[S08](../examples/phase4/live/round-1/S08-actual.json)：未通过预期，无空位却尝试保存，工具拒绝，未越权占位 |
| 重复提交 | [S09](../examples/phase4/live/round-1/S09-actual.json)：同键重放及新键业务唯一；S10/S17 原键恢复 |
| 保存失败 | [S10](../examples/phase4/live/round-1/S10-actual.json)：失败无部分写入、原键恢复；[S17](../examples/phase4/live/round-1/S17-actual.json)：unknown 不冒充成功；S12 保留其他成功项 |

额外边界的实际文件位于同目录 S15/S16/S18，分别覆盖逻辑时间、确认版本/风险绑定、读取工具不可用。模拟用户确认、日历替换与恢复不计为模型自主行为。
