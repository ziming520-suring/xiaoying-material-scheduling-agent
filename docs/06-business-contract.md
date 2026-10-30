# 第二阶段：业务契约 v1

状态：本文是用户批准的第二阶段业务契约基线。第三阶段已经实现本地工具、迁移、确认、离线循环及百炼真实模型适配。下文“未来入口”“本阶段未实现”描述的是第二阶段交付时点，当前实现状态与命令见 README、phase3-plan.md 和 08-bailian.md。合成品牌规则仅用于本题，不代表法律或真实平台政策。

## 1. 共同约定与范围

- 单账号固定时段；一个时段最多一项素材。没有账号登录、跨平台发布或真实媒体识别。
- ID 均为 1–128 个 ASCII 字符，只允许 `[A-Za-z0-9._-]`。版本为正整数，布尔值不能充当整数。
- 所有基础与补充资料的 source_id 在数据集内全局唯一。material_id、rule_id、slot_id、calendar_id 分别在各自集合内唯一。
- 时间沿用基础层格式，输入必须有日期、秒和时区，输出为 UTC 秒精度。业务“同日”按固定 `+08:00` 划分，不依赖电脑时区。
- 所有对象字段均按下表列全；除明确允许 null 的字段外不允许 null。未知字段拒绝。数组中的素材、风险、来源不允许重复 ID。
- 写请求外层沿用 `{schema_version:1, request_id:string, operation:string, payload:object}`。外层版本与未来数据库版本 2 是不同概念。
- 基础写响应及错误结构不变。业务未完成不可以通过基础响应 `status=succeeded` 冒充完成；后者只表示该次写操作已成功。
- 模型只接触下述四类工具；没有任意 SQL、写文件、确认用户意见、修改日历或真实发布工具。
- 批量任务每项独立保存，已成功项不因其他项失败回滚。一次保存或确认失败，草稿、占位、确认记录和成功重放记录一起回滚。已存在的成功任务、旧草稿不因此删除。

## 2. 数据字典

### 2.1 任务与资料

| 对象 | 字段及类型 | 约束 |
|---|---|---|
| Task | `task_id:string`, `items:TaskItem[]` | items 至少 1 项，保持输入顺序；同任务 material_id 唯一 |
| TaskItem | `material_id:string`, `requested_time:string` | requested_time 为原始硬性要求，保存后不可修改 |
| Material | `material_id:string`, `version:int`, `source_id:string`, `title:string|null`, `caption:string|null`, `rights_status:enum`, `manual_review_required:bool` | rights_status 为 authorized / unknown / denied；字段允许缺内容，但导入结构必须完整 |
| Supplement | `source_id:string`, `material_id:string`, `version:int`, `fields:object` | fields 非空，只允许 title、caption、rights_status、manual_review_required，值类型同 Material |
| Rule | `rule_id:string`, `version:int`, `kind:enum`, `severity:enum`, `fields:string[]`, `terms:string[]`, `reason:string` | kind、severity 与合成规则表一致；未用 fields 或 terms 必须为 [] |
| Slot | `slot_id:string`, `starts_at:string` | starts_at 唯一；每个 Slot 代表一个发布机会，不是视频播放时长 |
| Calendar | `calendar_id:string`, `revision:int`, `existing:ExistingSchedule[]` | revision 为正整数 |
| ExistingSchedule | `schedule_id:string`, `slot_id:string` | 引用已知 Slot；同日历 slot_id 和 schedule_id 均唯一 |

新任务创建时校验格式、素材 ID 存在、原要求严格晚于逻辑时间，任一项非法则不创建任务。资料内容缺失不是创建任务的格式错误，交给审核返回资料不足。请求时间未列入可选 Slot 时允许创建任务，时段查询返回 not_offered，替代建议仍须用户确认。

`task_id` 由调用方显式提供，与操作 request_id 分开。同 task_id 同规范化 items 的重复创建返回已有任务；同 task_id 不同 items 返回 TASK_CONFLICT。规范化只统一时间、不排序 items。

### 2.2 资料合并与规则

资料读取有 `primary` 和 `complete` 两种视图：primary 不填充补充资料，complete 用全部已登记补充资料填补缺失值。只有 null、空白 title/caption、unknown 授权视为缺失；false 不视为缺失。

不覆盖原始非缺失值。基础与补充、或两份补充对同一字段提供不同非缺失值时，输出 SOURCE_CONFLICT，不挑一个覆盖。所有视图都报告已登记来源中的冲突；primary 不会因不补全而掩盖冲突。冲突字段不采用任意一份补充。比较字符串使用原文精确比较，空白只用于判定缺失，不静默改写文案。

| 规则 ID | kind / severity | 确定性检查 |
|---|---|---|
| R001 | required_fields / missing | title、caption、rights_status 缺内容，或授权为 unknown；逐字段给出缺失项 |
| R002 | denied_rights / block | rights_status 为 denied |
| R003 | banned_terms / block | caption 原文包含 terms 中任一非空字符串；按字面子串匹配，记录命中文本 |
| R004 | manual_review / confirm | manual_review_required 为 true |
| R005 | source_conflict / block | 任一字段存在来源冲突 |

保留全部 findings。审核状态优先级 `blocked > needs_information > needs_confirmation > passed`。人工确认只对 severity=confirm 的风险有效，不将 blocked 或 needs_information 转成 passed。

Finding 结构固定为 `{rule_id:string, severity:"missing"|"confirm"|"block", field:string, reason:string, source_ids:string[], evidence:string[]}`。同一规则命中多个字段可生成多条 Finding；确认风险 ID 列表按 rule_id 去重排序。

工具不得接收模型声称的“已获授权”或“审核通过”作为事实。有效引用必须能从可信资料重建。

### 2.3 草稿与确认

Draft 字段：

| 字段 | 类型 | 含义 |
|---|---|---|
| draft_id | string | 服务端首次保存分配 `draft.` + UUID hex；后续更新不变 |
| task_id / material_id | string | 组合唯一，独立于技术重放键 |
| version | int | 初次为 1；有效内容或确认状态改变时递增 |
| requested_time | string | 从任务读取，原硬性要求始终保留 |
| suggested_slot_id / suggested_time | string | 从可信 Slot 读取，不接受模型直接改时间 |
| material_ref / check_ref | string | 本次保存所核对的资料与规则结果引用 |
| review_basis | ReviewBasis | 工具在保存事务内生成的不可变审核依据快照，定义见下文；不能由模型提交 |
| review_status | enum | passed 或 needs_confirmation；缺资料或阻断时禁止保存 |
| findings | Finding[] | 保存时由工具重新生成，不能由模型提交覆盖 |
| confirmation_required | bool | 改时段或含 confirm 风险时为 true，即便后续已确认也保留 |
| required_risk_ids | string[] | 所有需确认风险的 rule_id，去重排序 |
| status | enum | pending_confirmation / confirmed |
| authorization_basis | enum|null | pending 时为 null；正常原要求为 original_request；明确人工确认为 user_confirmation |
| occupies_slot | bool | 仅 confirmed 为 true |

Confirmation 记录：`confirmation_id:string`（服务端 `confirmation.` + UUID hex）、`draft_id:string`、`accepted_version:int`、`accepted_slot_id:string`、`accepted_risk_ids:string[]`、`request_id:string`、`confirmed_at:string`（逻辑时间）。

ReviewBasis 的全部字段：`primary:Material`、`supplements:Supplement[]`（全部已登记、按 source_id 排序）、`rules:Rule[]`（按 rule_id 排序）、`read_result:ReadMaterialData`（第 3 节 read_material 的完整成功 data）、`check_result:RuleCheckData`（check_rules 的完整成功 data）、`checked_at:string`（保存时的 UTC 逻辑时间）。read_result 保留 view、有效文案/授权、来源版本与 provenance；原始资料和规则全文也在快照内，因此 material_ref/check_ref 可以从快照重新计算，不只是不可逆的摘要。

快照与草稿在同一事务中保存，读取 Draft 时必须完整返回。待确认稿更新时生成新版本快照；同内容去重不替换既有快照。已确认稿及其快照不可修改，后续可信资料更新只影响当前资料表，不覆盖已有草稿的快照。成功确认重新核验当前资料且引用不变时，保留原快照，确认时间由 Confirmation 记录。这样即使当前素材/规则升级，已确认稿仍能展示保存时的文案、授权来源和审核规则；它不意味着旧依据自动授权未来新写操作。

- 无风险且建议时间等于 requested_time：直接保存 confirmed，占位，依据为 original_request。
- 改期或提示风险：保存 pending_confirmation，不占位，原要求不变。
- blocked / needs_information / 无候选时段：不创建草稿；若已有旧待确认稿，保留原稿但当前结果标记未完成，不虚称已更新。
- 同内容用新 request_id 保存，返回同一草稿，版本不变；待确认稿的建议或资料依据改变，原稿版本 +1，旧确认失效。
- 已确认稿不可改期、不可替换、不可撤销。同内容重复保存返回已确认稿，任何不同内容返回 DRAFT_FINALIZED；本版不做自动释放。
- 用户未确认，草稿无限期保持待确认但不占位；逻辑时间前进不改其状态。新的确认若候选时间已不在未来则拒绝，旧稿保持原状。

## 3. 四类模型工具的精确契约

前三类是只读工具，不写重放或业务表。返回 `{ok:true, data:object, error:null}` 或 `{ok:false, data:null, error:{code,message,field,retryable}}`。下表列出全部参数与成功 data 字段。

| 工具 | 参数 | 成功 data |
|---|---|---|
| `read_material` | `material_id:string`, `view:"primary"|"complete"` | `material_id`, `view`, `material_ref`, `effective:{title,caption,rights_status,manual_review_required}`, `sources:SourceVersion[]`, `provenance:object`, `missing_fields:string[]`, `conflicts:SourceConflict[]`, `supplement_available:bool` |
| `check_rules` | `material_id:string`, `material_ref:string` | `material_id`, `material_ref`, `check_ref`, `rule_versions:RuleVersion[]`, `review_status`, `findings:Finding[]`, `missing_fields:string[]`, `required_risk_ids:string[]` |
| `query_slots` | `task_id:string`, `material_id:string` | `requested_time`, `requested_slot_id:string|null`, `requested_status:"available"|"occupied"|"not_offered"|"past"`, `calendar_revision:int`, `occupancy_version:int`, `requested_occupant:Occupant|null`, `alternatives:Slot[]` |
| `save_draft` | 模型参数：`task_id:string`, `material_id:string`, `suggested_slot_id:string`, `material_ref:string`, `check_ref:string`；调度器另注入 `expected_draft_version:int|null` | `draft:Draft`, `save_status:"saved"`，使用基础写响应外层，见下文 |

SourceVersion = `{source_id:string, version:int}`。sources 列出基础及全部已登记补充来源，按 source_id 排序；是否用于有效字段由 provenance 表达。RuleVersion = `{rule_id:string, version:int}`，按 rule_id 排序。provenance 的固定四个字段 title/caption/rights_status/manual_review_required 对应排序后的 source_id 数组；缺失字段为空数组。SourceConflict = `{field:string, values:[{value:string|bool, source_ids:string[]}]}`，至少两组不同的非缺失值；按 field 排序，values 按值的规范化 JSON 字符串排序。

Occupant = `{kind:"existing_schedule"|"confirmed_draft", id:string}`。alternatives 只列同业务日期、严格晚于逻辑时间、未占用且不是原 requested_time 的时段，按时间、slot_id 升序。候选顺序是检索结果排序，不是预设模型决策。待确认稿不参与占用查询。

`material_ref = "mat." + SHA256(规范化 {material_id, primary完整记录, 按source_id排序的全部supplements, view})`；即使 primary 视图也纳入全部补充版本，以发现来源变化。
`check_ref = "check." + SHA256(规范化 {material_ref, 按rule_id排序的完整rules, review_status, findings, missing_fields, required_risk_ids})`。
规范化沿用基础紧凑 JSON、对象键排序、UTF-8；数组按文档指定顺序。findings 按 rule_id、field、reason 排序，内部 source_ids 和 evidence 字符串去重排序；missing_fields、required_risk_ids 排序。工具根据当前可信资料重建引用，不需要在只读调用中持久化缓存。引用不匹配返回 STALE_MATERIAL 或 STALE_RULE_CHECK。

### 3.1 保存的技术请求键和事务

模型不能自行提供写请求 ID。调度器为每次新工具调用分配 `request_id = "save." + UUID hex`，operation=`draft.save`，payload 是上表五个模型参数加上注入的 expected_draft_version。首次执行前在运行记录中保留完整请求；该调用的故障重试必须复用原键原参。重新规划后的新调用分配新键，即使建议参数相同也不强制命中旧技术重放；业务唯一约束仍防止重复草稿。工具成功或失败返回基础响应结构。

- expected_draft_version=null 代表预计首次创建；已有草稿时应由调度器附上实际版本，模型不能自行伪造这个控制字段。
- 同参已成功请求先命中技术重放，返回历史结果，不再次校验时效性条件。
- 对新的写请求，已有草稿的**规范化建议内容**（task_id、material_id、suggested_slot_id、material_ref、check_ref）完全相同，返回当前同一草稿；即使 expected_draft_version 旧，也不增加版本或重复占位。若内容不同，只有当前为 pending 且 expected_draft_version 精确相等才允许更新；否则 DRAFT_VERSION_CONFLICT 或 DRAFT_FINALIZED。
- 对已确认稿，相同内容返回属于去重读取，不代表重新审核或新占位；重新评估仍须读取当前资料、检查规则并取得新的 refs。对待确认稿，即便内容相同，新的调用键也必须重新核验 refs、风险、任务归属、时段与逻辑时间后才能返回成功；发生冲突返回 SLOT_CONFLICT，旧稿不变。原键成功重放仍遵循基础契约，不重新校验。
- 正常原时段保存 confirmed 时，在同一 BEGIN IMMEDIATE 事务内检查已有排期和已确认稿，再写草稿、占位、成功响应。待确认保存同样要求建议时段当前可用，但不占位。
- 若首个保存返回 unknown，调度器保留原参数和原键重试，不能改 expected_draft_version 或自行换键规避核对。
- 风险确认、占位和草稿版本必须由工具决定；模型只能提出建议，不能在参数中携带 status、occupies_slot、confirmed 等额外字段。

## 4. 用户入口、任务结果及错误

### 4.1 未来入口（本阶段均不可执行）

| 操作／入口 | 输入 | 成功输出与职责 |
|---|---|---|
| `task.create`，普通 run 写入口 | payload 为完整 Task | `task:Task`；显式登记用户硬性要求 |
| `draft.confirm`，独立 `confirm --db --input` CLI | payload=`{draft_id, expected_draft_version:int, accepted_slot_id, accepted_risk_ids:string[]}` | `{draft:Draft, confirmation:Confirmation}`；仅本地用户入口允许 |
| `drafts --db --task-id`，只读 CLI | task_id | 当前草稿列表，用于展示真实最新状态及自动装配 expected_draft_version |
| `agent --db --task-id`，第三阶段入口 | 已登记 task_id | AgentResult；模型适配器、调用配置由第三阶段确定 |

普通 run 分发器与模型工具注册表必须拒绝 draft.confirm，不能仅靠提示词禁止。独立 confirm 接受相同基础写信封，确认身份是本地 CLI 的明确操作；不新增多用户身份系统。调度器内部可使用统一事务执行器，并携带可信 user 来源上下文；来源不由 JSON 字段指定。

用户确认条件：草稿存在且仍为 pending；expected_draft_version 等于当前版本；accepted_slot_id 等于草稿建议；accepted_risk_ids 集合与 required_risk_ids 精确相等；参数重复风险 ID 拒绝。全部资料及规则引用仍有效、无阻断或缺失，时段仍在未来且可用。成功时在同事务中新增确认记录、草稿版本 +1、状态改 confirmed、依据改 user_confirmation，并占位。

同一个成功确认 request_id 重放原结果；不同 request_id 再确认已确认稿返回 DRAFT_FINALIZED，不产生第二条确认。确认失败不修改旧稿。占位或资料变化后重新规划更新待确认稿，再使用新版本确认，不能把旧确认自动转移到新稿。

### 4.2 结果字段

AgentResult = `{task_id:string, task_status:"completed"|"partial"|"incomplete", items:ItemResult[], stop_reason:string|null}`，不是一次数据库写请求的成功响应。items 保持任务输入顺序，必须逐项返回。

ItemResult 全部字段：`material_id:string`, `requested_time:string`, `review_status:"not_checked"|"passed"|"needs_information"|"needs_confirmation"|"blocked"`, `findings:Finding[]`, `suggested_slot:Slot|null`, `pending_items:PendingItem[]`, `draft:Draft|null`, `save_status:"not_attempted"|"saved"|"failed"|"unknown"`, `item_status:"completed"|"pending_confirmation"|"needs_information"|"blocked"|"save_failed"|"unknown"|"incomplete"`, `reason_codes:string[]`。

PendingItem = `{kind:"missing_information"|"time_change"|"risk_confirmation", field:string|null, risk_id:string|null, message:string}`。风险原因、待确认项由可信工具结果和时间差异派生，不由模型任意删除。

- 全部 item_status=completed 时 task_status=completed；至少一个 completed 且存在其他状态为 partial；其余为 incomplete。
- confirmed 且占位成功才可 completed；pending 保存成功仍是 pending_confirmation。提示风险在用户确认后仍保留审核 findings 与 review_status，不抹掉风险记录。
- 失败时 draft 字段可携带查证存在的旧稿，但 save_status 必须说明**本次**写入失败；不能把旧稿当作本次已保存。
- 未核实 unknown 时不能推断 completed；原键恢复成功或可信查询查明后才更新结论。
- 初次工具读取失败使用 not_checked/incomplete；达到后续 Agent 执行上限使用 incomplete，stop_reason=`step_limit`，保留已成功项。
- 最终状态由编排器依据工具结果组装，不接受模型无依据宣称成功。模型动作 Trace 的格式及具体上限在第三阶段确定，不在资料中伪造。
- pending_items 仅列尚未满足的条件；已确认时为空数组，已接受风险仍保留在 findings/required_risk_ids 和 Confirmation 中，不再表示需要重复确认。

### 4.3 新增错误语义

错误外层沿用基础层：`{code,message,field,retryable}`。下列拒绝错误 retryable=false；必须按所述条件处理后用新操作键／参数重新请求：

| code | 原因 |
|---|---|
| MATERIAL_NOT_FOUND / TASK_NOT_FOUND / DRAFT_NOT_FOUND / SLOT_NOT_FOUND | 指定资源不存在 |
| TASK_CONFLICT | task_id 已绑定另一组不可变要求 |
| INVALID_PUBLISH_TIME | 新时间或新确认时间不严格晚于逻辑时间 |
| STALE_MATERIAL / STALE_RULE_CHECK | 资料或规则引用已失效，须重新读取并检查 |
| REVIEW_BLOCKED / MATERIAL_INCOMPLETE | 规则阻断或资料不足，不能保存 |
| SLOT_CONFLICT | 保存或确认时发现候选已占用 |
| DRAFT_VERSION_CONFLICT / DRAFT_FINALIZED | 旧版本更新或试图更改已确认草稿 |
| CONFIRMATION_MISMATCH / CONFIRMATION_CHANNEL_REQUIRED | 接受的时段／风险不符，或不是独立用户入口；版本不符使用 DRAFT_VERSION_CONFLICT |

FORMAT 错误统一为已有 INVALID_REQUEST，不新增同义错误码。只读工具故障使用 TOOL_UNAVAILABLE，retryable=true。保存故障沿用 STORAGE_ERROR、STORAGE_BUSY、OUTCOME_UNKNOWN；不能另造“成功但没保存”的结果。

## 5. 数据加载、日历变化与版本升级设计

本阶段只交付 JSON，不执行导入或升级。第三阶段新增 `schema.upgrade`（payload=`{target_storage_version:2}`）并走统一写入口。先严格验证 v1，再原子增加业务表与版本；保留原逻辑时间、replay_results 和旧成功结果；失败整个回滚。同键升级重放可用，新键 target=2 对已是 v2 的完整库返回无变化成功。不修改 v1 三张表的列布局。

业务持久化逻辑表：tasks/task_items（任务素材唯一）、materials、supplements、rules、slots、calendar_state（外部 revision 与 occupancy_version）、existing_schedules（slot 唯一）、drafts（task_id+material_id 唯一；confirmed 的 suggested_slot_id 部分唯一索引）、confirmations。查询和写入时合并检查 existing_schedules 与 confirmed drafts，跨表排他由串行写事务保证。

`fixture.load` 为本地测试／准备入口，不提供给模型。payload=`{dataset_id, manifest_sha256, calendar_id}`，只从仓库可信 manifest 引用的 JSON 读取，不接受模型给文件路径。验证所有引用后在新 v2 业务库中整体加载。逻辑时间必须等于 manifest 初始时间；不得重置时钟。业务表已有数据时同参原键重放可用，其他加载请求拒绝 FIXTURE_ALREADY_LOADED，不覆盖用户草稿。

改变占用场景使用测试入口 `calendar.replace`，payload=`{expected_revision:int, existing:ExistingSchedule[]}`，新 request_id，先校验再原子替换外部已有排期。revision 和 occupancy_version 各加 1；不得与已确认草稿冲突。pending 草稿允许被外部占用抢先，之后确认必须失败。它不清空草稿、不改素材、不修改初始时间，不向模型开放。

资料未来更新采用可信 fixture 的版本更新测试夹具，不开放给模型。phase2 场景中的故障与资料突变均只声明测试意图，第三阶段实现测试辅助时必须显式隔离，不能增加隐藏生产开关。

fixture.load 以文件原始字节 SHA-256 核对 manifest_sha256，加载完成后 occupancy_version 初始为 1。新 confirmed 草稿或 pending→confirmed 占位时 occupancy_version +1；pending 的保存/更新和任何重放不增加它。calendar.replace 每次成功的新请求使 revision 与 occupancy_version +1，即使日历内容未改变。请求的 expected_revision 不符返回 CALENDAR_VERSION_CONFLICT，已有确认占位冲突返回 SLOT_CONFLICT；FIXTURE_ALREADY_LOADED、CALENDAR_VERSION_CONFLICT 均为拒绝错误、retryable=false。未通过资料格式/引用核查返回 INVALID_REQUEST；初始时钟与 manifest 不一致也返回 INVALID_REQUEST。

迁移成功 data 为 `{previous_storage_version:int, storage_schema_version:2}`；导入成功 data 为 `{dataset_id:string, calendar_id:string, calendar_revision:1, occupancy_version:1}`；日历替换成功 data 为 `{calendar_revision:int, occupancy_version:int}`。模型不接触这些准备操作。

只读 drafts 成功使用基础响应外层，operation=`drafts.read`、request_id=null、data=`{task_id:string,drafts:Draft[]}`，草稿按任务 items 顺序排列。没有草稿时返回空数组。它读当前状态而非历史重放：最终任务汇总须查询当前草稿，不能把旧成功响应中的 pending 状态当当前确认状态。save_status 指本次保存或确认写请求的结果；没有新写调用时为 not_attempted，任务仍可因已存在的有效 confirmed 稿为 completed。

## 6. 完成边界

本阶段的 read/check/query/save/confirm 都是契约，不是已实现工具。`data/scenarios/*.json` 的 expected 为验收预期，execution_status 固定 not_run。实际输出与真实模型 Trace 必须在后续运行生成到 examples，不能复制 expected 当作 actual。

实现时必须保留原 42 项基础测试，新增业务用例和 v1→v2 迁移／确认入口权限测试。模型供应商和调用预算留待第三阶段接入前确定。
