# 第二阶段合成资料

全部是虚构的元数据、文案和测试品牌规则，不包含真实媒体、真实授权凭证或平台政策。**18 个场景均尚未执行业务验证**；expected 不是实际输出，也不是模型决策日志。

## 资料入口

- [manifest.json](manifest.json)：数据集 ID、逻辑时间、文件清单；只包含仓库相对路径。
- [materials.json](materials.json)：8 项素材，字段见 [业务契约](../docs/06-business-contract.md)。
- [supplements.json](supplements.json)：M02 可补授权，M06 的授权来源相互矛盾。
- [rules.json](rules.json)：R001–R005 的确定性检查配置。
- [slots.json](slots.json)：2026-10-28 北京时间 18、19、20、21 点；存储为 UTC 10、11、12、13 点。
- calendars/：5 份互相独立的已有排期，C_EMPTY、C_18、C_19、C_18_19、C_FULL。
- scenarios/：18 份场景输入、试验安排和预期；完整对应关系见 [验收映射](../docs/07-acceptance-map.md)。

初始逻辑时间为 `2026-10-28T09:00:00Z`。每个场景使用新的数据库，加载全部公共资料和该场景指定的一份日历。不能串行复用前一场景留下的草稿、占位或时钟。

| 素材 | 设计用途 |
|---|---|
| M01 / M08 | 两项独立的正常素材，可用于批量部分成功 |
| M02 | 基础授权 unknown；SUP.M02 补为 authorized |
| M03 | 文案缺失、授权 unknown；没有可补资料 |
| M04 | 明确 denied，且命中禁用表述 |
| M05 | 有人工复核标记，需要明确接受 R004 |
| M06 | 基础 authorized、补充 denied；必须报告来源冲突 |
| M07 | 标题缺失、授权未知、禁用文案、人工复核同时出现 |

## 场景文件结构

`schema_version=1`，`scenario_id`、`title` 为字符串；`execution_status` 固定为 `not_run`；`requirement_ids` 为 A01–A06 的非空去重列表；`calendar_id` 指定独立日历；`task` 是未来 task.create 的 payload。

`steps` 是测试安排清单，每项 `{action:string, details:object}`。action 只允许 agent_run、user_confirm、calendar_replace、repeat_saved_request、repeat_save_new_key、inject_fault、clear_fault、read_state、clock_advance、revise_pending。这是第三阶段测试编写的说明，**不是现有可执行 DSL，也不是预定模型 Trace**。`current_pending` 等符号表示后续测试从真实工具输出取值，不能当字面参数提交。

`expected` 包含 `task_status`、`items`、`assertions:string[]`。其中 items 是验收投影，不冒充完整 AgentResult，每项包含：

- material_id、review_status、item_status、save_status：与业务契约对应。
- draft_status：pending_confirmation / confirmed / null；draft_count：该任务该素材预期 0 或 1 份；occupies_slot：布尔。
- allowed_suggested_slot_ids：允许的建议集合，空数组表示没有建议；并非强制模型选列表第一项。失败保存仍可能保留建议而没有草稿。
- reason_codes：至少应报告的未完成原因。工具错误使用契约码；NO_AVAILABLE_SLOT 是编排结果原因，非工具异常。

expected 描述全部步骤后的最终观察。中间状态由 assertions 约束，尤其 S05 确认前、S10 失败时、S17 不确定时，必须另存实际记录。确认失败的 save_status=failed 指本次草稿写入/确认失败，旧稿仍存在；并不否认此前保存成功。S07、S15、S16 的最终状态来自确认后只读观察，不通过重新运行 Agent 悄悄换掉被验证的旧稿。

S06、S07、S15、S16 文本中的 S19/S20 只是说明路径；实际断言用 allowed_suggested_slot_ids 和真实返回绑定，S16 必须选择两个不同候选。模型可能产生不合格结果，这应记录为评估失败，不能修改预期掩盖。

## 静态验证

在 project1 目录运行：

```text
python tests/validate_stage2.py
python tests/validate_stage2.py --output examples/phase2/data-validation.json
```

该脚本只核查 JSON 结构、类型、引用、时区、场景覆盖和预期状态一致性，**不执行审核、排期、故障注入或模型**，不生成业务实际输出。第一阶段 42 项测试另行执行。

后续 fixture.load 需要 manifest_sha256：定义为 manifest.json 原始 UTF-8 文件字节的 SHA-256。它用于标识加载清单，不是全部文件的完整性签名；加载器仍须严格验证所有资料。现阶段没有导入命令，不能把这些文件直接交给尚未实现的 CLI。
