# 百炼接入执行记录

计划依据：phase3-plan.md Task 3；用户已确认北京 qwen-plus、工作空间接口，以及整个项目累计付费调用最多 10 元。

- 使用 executing-plans 和 test-driven-development；沿用现有非 Git 项目目录和第三阶段快照，不创建无意义 worktree。
- Pre-flight：Model.next_action(context) 接入已有动作校验、事务工具与 Trace；模型异常需要保留明确错误码。
- Ruling：使用标准库 HTTPS 调用 OpenAI 兼容协议，避免新增 SDK 依赖；关闭思考、限制单次输出 2048 tokens，保留模型选择工具的自由。
- Ruling：持久化预算账本每次联网前保守预留 0.10 元，最多 100 次，总预留不超过 10 元；失败和超时也不退还预留，禁止自动网络重试。单次请求 JSON 不超过 60000 UTF-8 字节；按北京非思考输入 0.8 元/百万 tokens、输出 2 元/百万 tokens 估算，预留高于该限制下的估算。估算并非供应商账单，控制台预算仍是第二道限制。
- 来源（2026-10-29）：https://help.aliyun.com/zh/model-studio/qwen-plus ，https://help.aliyun.com/zh/model-studio/qwen-function-calling 。模型价格变化后需重新审查预留，不能任意改模型。
- 完成：适配器/预算/CLI 的 RED→GREEN；真实连接与场景；独立审查；最终全套回归 97 tests，0 failures/0 errors，19.550 秒。
- 实测问题一：初版 observations 放入用户 JSON 导致模型在缺资料时重复操作。改用原生工具消息；通过回归检查真实工具结果的角色与关联 ID。
- 实测问题二：合法 read→finish 请求补充路径没有列出缺口。回归先失败后修复，从可信读取结果生成 pending_items，未执行规则检查时不编造 findings。
- 实测问题三：S02 连续出现 MODEL_PROTOCOL_ERROR。结构诊断定位 missing_reason；保留原模型 reason 到历史上下文，回归先失败后通过，真实 S02 随后补查、重检并保存成功。
- 失败证据保留在 examples/bailian-live、bailian-v2、bailian-v3、bailian-supplement-recheck、bailian-protocol-diagnostic，不覆盖、不删除。
- Ruling：真实读取工具已明确报告缺口时，允许模型直接请求补充；代价是该路径没有完整规则检查，结果 findings 保持空列表，不能声称规则已通过。
- Ruling：结构错误只记录固定诊断类别，不记录供应商原文；代价是早期只保留通用 MODEL_PROTOCOL_ERROR 的记录不能倒推出具体原因，不追溯伪造日志。
- 独立审查无 Critical/Important/Minor；保留事项与处理见 bailian-review.md。审查后修复通过失败回归和最终全套测试验证。
- 其他决策及其限制：标准库适配避免依赖但需维护协议；保守预留可提前停止但不放大预算；北京与 qwen-plus 白名单不支持任意切换供应商。最终费用估算和累计次数见 examples/bailian-report/summary.json，尚未核对供应商账单。
- 最终 S10 的恢复断言错误假定 confirmed；模型最后的 S19 建议正确恢复为待确认。保留 failed 原判及源文件哈希，增加只读核验和两项反例回归，不覆盖实际输出，不把恢复后的待确认当排期完成。
- 本轮付费调用已停止：96 次，Token 估算 0.1628924 元，保守预留 9.60 元；不得重置账本，剩余 4 次预留。第三阶段核心实现和实测完成；完整稳定性评估与 GitHub 交卷仍待后续阶段。
