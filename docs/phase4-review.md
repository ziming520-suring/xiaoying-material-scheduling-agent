# 第四阶段独立 AI 审查与修复

2026-10-29，一名独立 AI 代理只读检查预算策略、评估证据与版本冻结。没有读取密钥或调用模型，不等同于人工审查。审查未发现 Critical，提出 Important 1、Minor 2；没有独立重跑完整付费评估，也未核实账户账单。

| 发现 | 复现与修复 | 验证 |
|---|---|---|
| Important：最后一例期间源码改变仍可能 exit 0 | 内存 mock 让最后一例修改指纹，旧脚本写 source_unchanged=false 但 stop_reason=completed；改为每例结束后及最终返回前检查，变化返回 2/source_changed | test_final_case_source_change_cannot_report_success |
| Minor：冻结指纹未包括资料和辅助代码 | 原范围仅 src 与两份脚本；增加 data 下全部 JSON 和 runtime_helpers.py | test_fingerprints_include_fixture_content_and_runtime_helper |
| Minor：S17 提前失败时丢失故障说明 | 故障说明原来在断言后才记录；将 S17/S18 的说明提前写 checkpoint | test_fault_description_survives_early_model_failure |

新增回归先运行，得到预期 3 项失败，见 [RED 日志](phase4-review-red.txt)。一次修复后完整运行 107 项测试通过；最终脚本的 18 个离线场景重复 3 轮，54/54 通过。没有增加付费模型调用，未修改业务源码、提示词、模型参数或真实输出。

真实 34 场景结束于修复之前，原 summary 明确 source_unchanged=true。因此该报告不能声称应用了修复后的更宽冻结范围，但当前全部运行时源码仍与真实评估指纹一致。两份旧评估脚本原样保存在 [evaluated-harness](../examples/phase4/evaluated-harness/)，逐字节匹配原摘要指纹；它们仅用于审计，不是推荐入口。最终离线记录采用修复后的脚本。新 [报告生成器](../tests/build_phase4_report.py) 核验这些对应关系，不改原始结果。

预算审查未在已检查路径中发现越额问题：先事务预留、整数微元、有效 usage 结算、未知保留、并发串行化、冲突结算拒绝、未来策略拒绝、历史 calls 保留。该结论以代码固定费率和输入输出限制为前提，不是平台账单或未来价格保证。
