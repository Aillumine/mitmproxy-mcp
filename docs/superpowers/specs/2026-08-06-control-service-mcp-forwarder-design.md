# Plan 2 设计：控制服务与 MCP 转发层

**日期：** 2026-08-06  
**状态：** 已实现（文档与安装脚本已更新；端到端验收见 Task 10）  
**前置：** Android P0 已完成（33 个 MCP 工具）  
**后续：** Plan 3 SwiftUI 客户端依赖本契约  

## 1. 目标与非目标

### 目标

- 新增本地 **控制服务**（`mitmproxy-control`），作为 GUI / MCP 共用的状态真相源
- MCP（`mitmproxy-mcp`）探测到控制服务后 **纯 HTTP 转发**；探测/拉起失败则 **fallback** 直调现有 `tools/*`（兼容旧行为）
- 修齐流量/Mock DB 路径：控制服务模式下 tools 与 mitmproxy addon 都使用 `~/.mitmscope/`
- 为 Plan 3 提供稳定 HTTP API；预留 SSE 事件骨架（首版用轮询）
- **真机抓包时强制禁止设置 Mac 系统代理**，避免污染本机网络

### 非目标（Plan 2 不做）

- SwiftUI / 安装 `.app` / `.dmg`（Plan 3）
- SSE 真实推送实现（只留端点骨架与事件类型约定）
- 把 `tools/*` 抽成独立 domain service 层
- 改动 MCP 工具对外语义（除可选的 `traffic_list.after_id`）
- 自动迁移 `/tmp` 旧流量/Mock 库到 `~/.mitmscope/`

---

## 2. 已锁定决策

| 项 | 选择 |
|---|---|
| 控制服务实现 | Python（FastAPI/Starlette + uvicorn），未来 SwiftUI 接同一套 API |
| MCP 连接策略 | 探测 → 尝试拉起 → 失败才 fallback 直连 |
| HTTP API 覆盖 | **全量镜像**现有 MCP 工具（当前 33 个） |
| 实时性 | Plan 2：轮询 + SSE **骨架**；Plan 3 再实装推送 |
| 架构风格 | **薄 HTTP 包装**：`POST /v1/tools/{name}` → 调用现有 `tools/*` |
| Mac 系统代理 | 默认永不自动开启；真机强制禁止；仅模拟器/纯 Mac 且用户显式要求才允许 |

---

## 3. 进程与数据流

```
┌─────────────┐  stdio   ┌──────────────────┐
│ Cursor/IDE  │ ◄──────► │ mitmproxy-mcp    │
└─────────────┘          │ (探测/转发/fallback)│
                         └────────┬─────────┘
                                  │ HTTP Bearer
                                  ▼
                         ┌──────────────────┐     spawn
                         │ mitmproxy-control│ ──────────► mitmdump
                         │ ~/.mitmscope/*   │
                         └──────────────────┘
                                  ▲
                                  │ 同一 HTTP API（Plan 3）
                         ┌──────────────────┐
                         │ SwiftUI App      │
                         └──────────────────┘
```

**Fallback：** 无健康控制服务时，MCP 行为与今天一致（直调 `tools/*`，默认 `/tmp/*.db`）。

---

## 4. `runtime.json` 与鉴权

**路径：** `~/.mitmscope/runtime.json`  
控制服务启动成功后写入；**正常退出时删除**；读侧用 pid 存活校验作双保险。

```json
{
  "version": 1,
  "pid": 12345,
  "base_url": "http://127.0.0.1:18765",
  "token": "<随机高熵字符串>",
  "traffic_db": "/Users/<user>/.mitmscope/traffic.db",
  "mock_db": "/Users/<user>/.mitmscope/mock.db",
  "proxy_port": 8888,
  "capture_target": "mac",
  "started_at": "2026-08-06T06:00:00Z"
}
```

| 字段 | 说明 |
|---|---|
| `token` | 每次冷启动重新生成；敏感，仅本地文件 |
| `capture_target` | `"device"` \| `"simulator"` \| `"mac"`，影响是否允许 Mac 系统代理 |
| `base_url` | 仅 loopback |

**鉴权**

- 控制服务只绑定 `127.0.0.1`
- 请求头：`Authorization: Bearer <token>`
- 无/错 token → `401`
- MCP / 未来 SwiftUI 都以读最新 `runtime.json` 为准

**MCP 探测顺序**

1. 读 `runtime.json` → `GET /v1/health`（带 token）
2. 健康 → 转发模式
3. 不健康/缺失 → 子进程拉起 `uv run mitmproxy-control`，日志 `~/.mitmscope/control.log`，短轮询至 health 或超时
4. 仍失败 → fallback 直连 `tools/*`

---

## 5. HTTP API

**原则：** 返回体与 MCP 工具返回的 `dict` 一致（含 `success`），便于原样转发。

### 通用

- Base：`http://127.0.0.1:<port>/v1`
- `Content-Type: application/json`
- 业务失败（工具返回 `success: false`）→ HTTP **200**
- 鉴权失败 → **401**；未知工具 → **404**；未捕获异常 → **500** `{ "success": false, "message": "..." }`

### 工具入口（全量）

```
POST /v1/tools/{tool_name}
Body: { ...与 MCP arguments 相同的 JSON... }
```

覆盖当前全部工具名（与 `list_tools` 对齐），包括：

`proxy_*`、`traffic_*`、`mock_*`、`get_cert_info`、`android_*`、`ios_*`。

Plan 2 **不做** REST 别名（如 `/v1/proxy/start`）；需要时留给 Plan 3。

### 管理端点

| 方法 | 路径 | 作用 |
|---|---|---|
| GET | `/v1/health` | `{ok, version, proxy_running, pid}` |
| GET | `/v1/runtime` | 非敏感运行时信息（**不含 token**） |
| GET | `/v1/tools` | 工具名列表 |
| GET | `/v1/events` | SSE 骨架：返回 **501** 或可文档化的空流；约定事件类型 `traffic.created` 等，Plan 3 实装 |

### 轮询增量

- `traffic_list` 现有参数保留
- Plan 2 可为 `traffic_list` **增加可选** `after_id`（或 `since_ts`），便于 GUI 轮询；不改表结构

---

## 6. MCP 改造

| 模式 | `list_tools` | `call_tool` |
|---|---|---|
| 转发 | 本地声明 schema（不依赖控制服务列工具） | 全部 `POST /v1/tools/{name}` |
| fallback | 同左 | 直调现有 `tools/*` |

- 控制服务模式下，**不在 MCP 进程内再起 mitmdump**；由控制服务侧 `proxy_start` 负责
- 拉起失败必须可观测（日志 + fallback），旧 Cursor 配置继续可用

---

## 7. 控制服务实现边界

**入口：** `mitmproxy-control`（`pyproject.toml` scripts）

**启动时：**

1. 创建 `~/.mitmscope/`
2. 生成 token，选定 loopback 端口，写 `runtime.json`
3. 将 store / addon DB 路径指向该目录（环境变量或显式配置；**tools 与 addon 同路径**）
4. 启动 HTTP 服务

**运行时：** `POST /v1/tools/{name}` → 分发到现有 tool 函数（同步工具在线程池执行，避免阻塞事件循环）

**退出钩子：** 停止代理（若在跑）、删除 `runtime.json`

**依赖：** 新增 FastAPI（或 Starlette）+ uvicorn（仅控制服务需要；可放 main deps 或 optional extra，实现计划中选定）

---

## 8. 真机抓包：禁止代理 Mac 本机网络

### 规则

| 抓包目标 `capture_target` | Mac 系统代理（`setup_proxy`） |
|---|---|
| `device`（Android / iOS 真机） | **强制禁止**；即使传入 `true` 也否决，返回中说明原因 |
| `simulator`（模拟器 / 仿真器） | 默认 `false`；仅用户显式要求才允许 |
| `mac`（仅本机） | 默认 `false`；仅用户显式要求才允许 |

### 落地

- 控制服务维护 `capture_target`（写入 `runtime.json`）
- 可由 `proxy_start` / `android_setup_proxy` / `android_reverse_proxy` / iOS 等价路径更新；真机相关工具调用后应置为 `device`
- `proxy_start` 在执行前根据 `capture_target` 校验；`device` 时强制 `setup_proxy=false`
- **转发模式与 fallback 模式都遵守同一规则**（逻辑放在 `proxy_start` 或共享守卫函数，避免只修一边）
- 真机推荐路径：mitm 只监听 + `android_reverse_proxy` / 设备 Wi‑Fi 指向 Mac IP，**不**改 Mac 系统代理

与既有 Agent 规则一致：对话「开始抓包」默认不传 `setup_proxy=true`。

---

## 9. 测试

| 层级 | 内容 |
|---|---|
| 控制服务单测 | health、401、任意工具往返（可 mock `tools.*`） |
| MCP 单测 | 转发模式 vs fallback 分支 |
| 集成 | 拉起控制服务 → 经 MCP/HTTP 调 `proxy_status` / `mock_list` |
| 守卫 | `capture_target=device` 时 `setup_proxy=true` 被否决 |
| 回归 | fallback 下现有 `test_android_p0` / addon 等仍可用 |

---

## 10. 迁移与文档

- 未装/拉起失败控制服务：旧行为不变
- `install.sh` / `install-skill.sh` / README：补充控制服务说明；MCP 配置仍可为 `mitmproxy-mcp`（可自动拉起控制服务）
- 控制服务模式数据在 `~/.mitmscope/`；不自动迁移 `/tmp` 旧库（文档说明）
- 全局 `proxy` 别名保持**不带** `--setup-proxy`（已与「默认不代理电脑」对齐）

---

## 11. 成功标准

- [x] `uv run mitmproxy-control` 可启动，写出合法 `runtime.json`，`GET /v1/health` 返回 ok
- [x] 带正确 token 可 `POST /v1/tools/proxy_status` 等；错 token → 401
- [x] MCP 在控制服务健康时走转发；停止控制服务后可 fallback
- [x] `capture_target=device` 时无法开启 Mac 系统代理
- [x] Plan 3 可用同一 Base URL + Bearer token 对接（无需再改契约核心）

---

## 12. 开放实现细节（写入计划时选定，不阻塞契约）

- FastAPI vs 纯 Starlette
- 控制服务默认端口（固定 18765 vs 随机空闲端口写入 `runtime.json`）
- 拉起超时秒数与轮询间隔
- `after_id` vs `since_ts` 的最终参数名
