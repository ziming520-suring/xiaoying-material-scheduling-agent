# 项目 1：素材审核与发布排期 Agent

题号：1。题目：素材审核与发布排期 Agent。

GitHub 仓库：[ziming520-suring/xiaoying-material-scheduling-agent](https://github.com/ziming520-suring/xiaoying-material-scheduling-agent)。在检出的项目根目录运行 `git rev-parse HEAD` 可获取所检出版本的完整 SHA，交卷时与链接一起提交。本文不预填自身提交编号，最终编号在上传后核验。

## 评审步骤

1. 阅读 [README](README.md) 的运行入口、确认边界和退出码。
2. 使用 Python 3.12，在仓库根目录执行 `python run_demo.py`。无需安装依赖、配置密钥或联网。它创建独立 var/demo-... 目录，演示原时段冲突、替代建议待确认；退出码 2 是正常业务状态。result.json、trace.jsonl 和数据库路径会输出到终端。
3. 执行 `python -X utf8 -m unittest discover -s tests -v`。测试不调用付费模型。
4. 阅读 [第四阶段报告](docs/09-phase4-evaluation.md) 和 [题面验收映射](docs/07-acceptance-map.md)。真实模型记录在 examples/phase4/live，离线记录明确标记，不互相冒充。
5. 如需本人复跑真实模型，先按 [百炼配置](docs/08-bailian.md) 设置自己的密钥和北京地址，并了解付费预算。默认入口始终离线；本项目不自动执行真实发布。

第五阶段已用仅含待提交文件的全新目录复跑上述测试和演示；[实际复现摘要](examples/phase5/clean-reproduction.json)、[完整测试日志](examples/phase5/clean-test-results.txt)、[演示输出](examples/phase5/demo-result.json)、[演示 Trace](examples/phase5/demo-trace.jsonl)。没有拷贝原本机预算库或调用付费模型。

## 已实现与验证

Python 标准库、CLI、本地 SQLite，未使用 Agent 框架。四类工具和受限 ReAct 循环由代码实现；百炼 qwen-plus 根据实际工具结果选择动作。用户确认入口独立，不注册为模型工具。

107 项自动测试通过；最终离线 54 次场景通过；固定运行时版本真实 34 次场景中 33 次通过。正常、补资料、风险、冲突、重复、保存失败都有实际输入、输出及 Trace。8 个核心场景各重复 3 轮全部通过。真实评估的唯一失败、版本范围及故障注入说明均见报告，不能声称 34/34。

## 已知限制和未完成项

- S08 无空位时模型尝试保存被占时段，工具拒绝且无草稿或占位；最终保存失败，与预期直接结束不同。保留为模型计划质量问题。
- 只处理合成元数据、单账号固定时段；不识别真实媒体，不接发布平台，不支持已确认草稿自动改期或撤销。
- 真实模型别名和价格可能变化，小样本不是生产成功率承诺。费用是用量估算，非已核对账单。
- AI 生成代码和材料范围、用户确认与贡献记录见 [AI 使用](docs/04-ai-usage.md)。不将 AI 工作冒称个人手写；候选人独立修改和复跑需要本人据实补录。
- 实际总开发用时未连续记录。三天是用户可用窗口，题面三小时是建议，测试耗时不能代替开发用时。已知的评估起止时间在报告中。

## 题面与规则

[原始考核题面](docs/exam.md)、[用户确认的项目规则](docs/project1-rules.md)、[业务契约](docs/06-business-contract.md)。设计规则与题面原文有明确区分。五份必需文档、src、tests、examples、data、requirements.txt 和 .env.example 均在同一项目。
