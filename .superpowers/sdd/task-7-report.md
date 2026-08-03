# Task 7 实施报告

## 状态

已按 TDD 完成 TLS 客户端握手失败入库，并使用任务简报中的
`tls_failed_client` 实现原文。

## 变更

- `traffic_addon.py` 新增 `tls_failed_client(data)` hook。
- 失败记录使用 `tls-<n>` ID、`CONNECT` method、`TLS` resource type 和
  `status=0`，保留原始握手错误。
- 无 SNI 时回退到 `unknown`，异常被隔离，避免拖垮代理。
- 新增 3 个测试，覆盖正常失败、缺失 SNI 和多条记录不覆盖。

## TDD 与验证

- RED：`TestTlsFailure` 3 项均因缺少 `tls_failed_client` 而失败。
- GREEN：`TestTlsFailure` 3 passed。
- addon 全量：11 passed。
- 项目全量：129 passed，5 failed；失败均为任务说明指定忽略的
  `tests/test_tools.py` 既有失败。
- IDE lint：改动文件无诊断。
- Ruff：全仓检查发现 99 个既有问题，未在本任务范围清理。
- E2E：任务简报的 `--cacert /dev/null` 在本机 curl 先行报 CA 文件错误，
  未发起握手；改用错误 `--pinnedpubkey` 成功模拟 pinning，数据库读到
  `('tls-1', 'example.com', 'CONNECT', 0, 'TLS', 'connection closed early')`。
- MCP 读取：`traffic_list(limit=10, filter_type="TLS")` 返回 1 条记录。

## 自审

- 仅修改任务指定的实现、测试与本报告。
- SQL 字段和值数量一致，数据库连接在成功路径提交并关闭。
- ID 与既有 HTTP 记录共享计数器，不会与 `req-<n>` 主键冲突。
- 未发现需要阻止提交的问题。
