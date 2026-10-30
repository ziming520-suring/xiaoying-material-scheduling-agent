# 第三阶段 Implementation Plan

> 使用 executing-plans 在当前会话逐项实现，test-driven-development 验证功能，末尾一次独立审查。

Goal：实现四类工具、独立确认入口、受限 Agent 循环和可复制运行记录。
Architecture：继续复用统一写事务和成功重放；业务校验生成写入计划，同事务提交。只读工具使用一致性快照；模型调用在事务外，编排器从实际工具结果生成状态。
Tech Stack：Python 3.12、SQLite、标准库；已确认阿里云百炼北京 qwen-plus，本项目累计调用预算最多 10 元。
Spec：[已批准业务契约](06-business-contract.md)。当前非 Git 仓库，保留 phase2 快照，不初始化远程仓库。

## 约束与审查重点

- 保留全部 42 项基础测试；旧数据库显式升级，旧时间和重放不丢失。
- 待确认不占位；确认精确绑定且重新检查；模型不能调用确认、改日历或发布。
- 同任务/素材唯一；部分失败保留成功项；unknown 原键恢复。
- 资料更新后保存依据可追溯；拒绝伪造审核引用、来源冲突、非法跨日建议。
- 模型不可信输出、并发占位、陈旧版本、响应丢失、数据库损坏均不可虚报成功。
- Mock 只验证编排；真实模型结果必须单独标注，未经模型配置和预算确认不调用付费接口。

## Task 1：可加载资料并读取、检查、查询

Files：修改 contracts/storage/runtime；新增 business_validation.py、business_schema.py、material_tools.py、business.py；新增 test_business.py。
Interfaces：handle_request 仍统一处理写入；read_tool(store,name,args) 提供三类只读工具；fixture.load 从可信仓库导入。

- [x] 先测迁移保留/回滚、请求校验不改状态、合成导入、资料补全/冲突、规则优先级、时段查询。
- [x] 运行并保存缺功能失败结果；实现操作型 payload 校验、v2 和只读工具。
- [x] 运行新测试与基础回归：Task 1 共 48 项通过。

## Task 2：草稿与确认完整链路

Files：business.py、business_validation.py、CLI；tests/test_business.py、test_business_cli.py。
Interfaces：draft.save、独立 confirm、drafts 只读、业务唯一性、ReviewBasis。

- [x] 先测正常、待确认、确认竞争、版本/风险绑定、重复、失败原键重试、unknown、历史快照和并发。
- [x] 同事务准备/提交、可信入口身份、CLI 数据准备及确认。
- [x] 业务测试 17 项通过；确认 CLI 已验证。

## Task 3：模型循环与证据

Files：agent.py、model.py（接入待用户配置）、CLI；tests/test_agent.py；examples/phase3。
Interfaces：Model.next_action(messages)；四工具白名单；逐项迭代、硬上限、JSONL Trace、原键恢复记录。

- [x] 先测不同工具观察后动作变化、工具失败/未知动作/上限、批量部分完成和结果不确定。
- [x] 实现显式离线 Mock 与 Model Protocol，业务循环不绑定供应商。
- [x] 确认供应商预算后实现真实模型适配和实测；实际失败与修复证据见 03-evaluation.md、bailian-progress.md。
- [x] 生成实际业务/离线记录；没有生成虚构的真实模型记录。
- [x] 独立审查、必要修复、全套回归、README/AI使用/评估更新、本地实现存档。

## 执行记录

- 2026-10-28：开始；业务设计已由用户批准，本阶段不重复设计审批。
- Provider：已异步询问供应商、模型、接口地址和单次预算；不读取或输出已有密钥。
- 2026-10-29：业务工具、确认入口、受限循环、显式离线 Mock 已实现；真实模型适配与实测仍等待配置，不将 Mock 记作真实模型效果。
- TDD：业务初测 6 项因 UNKNOWN_OPERATION 失败；保存初测 10 项失败；Agent 缺模块初测失败；新增原键重试测试发现 UUID 更换后已修正。原始输出保存在 phase3-*-red.txt。
- Ruling：默认每任务最多 24 次模型动作（包含 finish），CLI 可显式指定 1..1000；每动作至多一个工具。真实模型预算尚未确认，不发起网络调用。
- Ruling：只读工具在数据库读快照中执行；写前置条件生成 SQL 计划，再由原统一事务执行，不开放模型 SQL。confirm 仅可信 user 通道，calendar.replace 仅测试通道。
- Final review：独立审查 3 项 Important 已复现并修复；附加修复深层 JSON 和批量 Trace 故障。见 [审查记录](phase3-review.md)。无待处理 Minor。
- Final verification：83 tests / 0 failures / 0 errors，14.202 秒；18 个本地场景核查通过，6 条 CLI 命令通过。实际记录见 examples/phase3，模型全部 offline_mock。
- Ruling：最终汇总用同一次当前草稿查询刷新全任务；查询失败明确未完成，日志失败保留可核实成功项。代价是结束时多一次本地只读查询，不替代历史原键重放。
- 上述为本地实现完成时点的记录；随后用户配置百炼并授权预算，第三阶段接入与核心验证已完成。当前不等于完成完整考核：第四阶段更全面评估和第五阶段 GitHub 交付仍需继续。
