# 进度 — implementation-plan.md

- Spec: ../../项目1-已确认的基础规则.md，用户已确认全部具体设计。
- Pre-flight: Task 1 的 ValidatedRequest/CanonicalRequest 供 Task 2 消费；Task 2 的 handle_request/read_clock 供 Task 3 消费，未发现契约冲突。
- 环境适配：当前目录无 Git 仓库；在 project1 中实现，不创建工作树或提交；保留本记录和原始测试输出。
- 执行方式：当前会话直接实现，按功能块先测后写，完成后独立审查。
- Task 1: complete — 8 个校验测试先因缺少实现失败，contracts.py 完成后全部通过。
- Task 2: complete — 22 个事务测试先因缺少实现失败；实现后与 Task 1 合计 30 项通过。首轮 Windows 暴露连接未关闭：测试使用 sqlite3 上下文并不会自动 close，已修正测试辅助生命周期；生产 Store.connect 的 PRAGMA 失败路径也已关闭连接，损坏数据库测试覆盖此回归。
- Task 3: complete — 7 个 CLI 测试先因缺少 __main__ 失败，实现后合计 37 项通过；证据脚本生成五组实际输入、输出和诊断事件。
- Final review: 独立 AI 审查发现两个 Important 问题，没有 Critical 或待办 Minor：历史成功响应浅校验、重放表异常列序配合位置 INSERT 可能破坏键。详见 review.md。
- Final: fixed 历史响应损坏 — 三项回归测试覆盖空字段、错误类型、布尔版本、重复键、NaN、时间不一致；先复现失败，再严格校验存储边界，变绿。
- Final: fixed 表结构不兼容 — 两项回归测试覆盖列重排和缺主键；先复现失败，再显式列名 INSERT 和版本 1 表定义校验，变绿。
- Final suite: `python -X utf8 -m unittest discover -s tests -v` → 42 tests, 0 failures, 0 errors（修复后实测）。
- Ruling: 版本 1 只支持当前表定义（允许大小写和空白差异），不自动迁移手工改造的等价 schema — 为了拒绝未知列序和缺失约束，避免静默丢失重放能力 — 代价是手工重建的其他等价布局也可能被拒绝，需要未来显式迁移。
- Ruling: 审查中未判断的模型、草稿、排期与发布保持后续范围 — 本阶段用户只授权基础层 — 当前成果不能作为完整考核提交。
- Ruling: 内部到期处理器保持可信代码约定，不做任意代码沙箱 — 生产没有注册处理器 — 将来接入不可信插件前必须另做隔离。
- Ruling: 不把本机 SQLite 测试扩展为断电或网络文件系统可靠性承诺 — 未执行这些环境测试 — 不适合据此宣布分布式或硬件故障保证。
- 审查未覆盖文档和证据，由主实现者核对链接、实际输出及 B01–B16 映射；没有声称第二轮独立复审。
- Deferred minors: 无。
