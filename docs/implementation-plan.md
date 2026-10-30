# 基础请求处理 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** 交付可运行、经过 B01–B16 验证的统一校验、重放和逻辑时间推进基础层。

**Architecture:** CLI 将原始 JSON 交给统一写入口。纯校验后进入 SQLite 串行写事务，先查重放再检查当前条件，最后原子提交状态与响应。时间处理器只在这个事务中执行，生产配置没有业务到期对象。

**Tech Stack:** Python 3.12，标准库 json/datetime/sqlite3/unittest；无运行时第三方依赖。

**Spec:** `../../项目1-已确认的基础规则.md`（用户已确认）。

## Global Constraints

- 所有写操作先校验；校验失败不修改业务、重放或逻辑时间。
- 成功结果持久化；同操作同键同参返回原结果；异参冲突。
- 同一事务提交状态、到期处理和成功响应；并发及重启防重复。
- 显式初始化，UTC 秒精度，禁止倒退，同时间不重复结算。
- 锁等待 5 秒；不能证明提交结果时返回 unknown。
- 第一版不实现审核、草稿或发布业务；测试对象只存在 tests 中。
- 所有命令在 project1 下执行；测试自动定位 src，CLI 使用 PYTHONPATH=src。

## Review Focus

1. 无效编码、重复 JSON 键、过深 JSON、UTC 越界：明确错误且不写状态（任务 1、3）。
2. 已有损坏／不兼容数据库：不覆盖、不偷偷初始化（任务 2）。
3. 提交成功后响应丢失和查询失败：保留不确定状态，禁止虚报失败（任务 2）。
4. 跨进程锁竞争和不同参数同键：不能执行两次（任务 2）。
5. 测试到期处理部分失败：状态、时间、重放一起回滚；无 CLI 故障注入开关（任务 2）。

## 文件结构

- `src/schedule_agent/contracts.py`：请求解析、严格时间、规范化、稳定响应。
- `src/schedule_agent/storage.py`：连接、事务、完整性检查、重放存储。
- `src/schedule_agent/runtime.py`：统一写入口、前置条件、时间与到期处理。
- `src/schedule_agent/__main__.py`：CLI 和 JSON 输出。
- `tests/test_validation.py`、`tests/test_runtime.py`、`tests/test_cli.py`：独立验证功能和故障行为；测试辅助模块承载故障注入。
- `examples/`：实际输入输出、测试报告和基础 Trace；明确不属于模型 ReAct 证据。

## Task 1：可验证的请求解析

Produces: `parse_and_validate(raw_json: str) -> ValidatedRequest`、`canonicalize(request) -> CanonicalRequest`、`RequestError`、`success_response`、`error_response`。

- [x] 写测试：合法时区归一化、字段／版本／枚举／类型／重复键错误、时间格式与越界、同参摘要相等。
- [x] 运行 `python -m unittest discover -s tests -p test_validation.py -v`，预期因生产模块缺失而失败。
- [x] 实现 contracts.py；只定义已批准的两个操作，不新增业务字段。
- [x] 同命令重跑，预期全部通过。

## Task 2：统一写事务、重放、时钟

Consumes: Task 1 的请求与响应。Produces: `Store(path, timeout=5.0)`、`handle_request(raw_json, store)`、`execute_write(request, store)`、`read_clock(store)`。

- [x] 写 B01–B16 中涉及状态的测试；真实临时 SQLite，真实独立进程；故障只在测试中替换 commit／恢复查询或到期处理器。
- [x] 运行 `python -m unittest discover -s tests -p test_runtime.py -v`，预期缺少 runtime 模块导致失败。
- [x] 实现显式建库、BEGIN IMMEDIATE、只读条件检查、原子重放保存；保留错误与 unknown 区别。
- [x] 实现集中到期入口，生产无处理器；测试处理器使用同事务对象，不自行提交。
- [x] 重跑整个 `python -m unittest discover -s tests -v`，预期全部通过。

## Task 3：CLI 与可复制证据

Consumes: Task 2 的统一入口。Produces: `python -m schedule_agent run/clock` 与 README。

- [x] 写 subprocess 测试：合法初始化／推进／重放、非法 JSON／UTF-8、缺参数、缺文件、只读查询不建库、退出码和纯 JSON stdout。
- [x] 运行 CLI 测试，预期没有 __main__ 入口失败。
- [x] 实现 CLI；输出只包含一个 JSON，错误不暴露堆栈。
- [x] 整套测试通过；用 evidence 脚本保存实际 CLI 输出与故障 Trace。
- [x] 写 README、运行说明、限制、AI 使用及验证记录；不得把示例预期写成实测结果。

## 收尾

- [x] 独立审查基础层，修复重要发现并添加先失败的回归测试。
- [x] 全套测试及证据生成成功后核对 B01–B16 映射，更新进度及规则中的实现状态。

环境适配：当前不是 Git 仓库，不初始化／提交／创建 worktree；使用独立 project1 子目录及文件进度记录。用户已授权本阶段实现，不再额外请求执行方式确认；在当前会话实现，末尾使用一次独立审查。
