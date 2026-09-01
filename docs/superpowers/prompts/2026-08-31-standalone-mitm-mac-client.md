# 新窗口提示词：独立 Mitm Mac 客户端（不接 DReaction）

把下面「复制区」整段贴进新 Cursor Agent 窗口。工作区请打开 **`/Users/jorim/agentspace/mitmproxy-mcp`**。需要对照 UI 时只把 DReaction 当只读参考，不要改它。

---

## 复制区

```
你在仓库 /Users/jorim/agentspace/mitmproxy-mcp 里工作。全程用简体中文回复。

# 任务

做一个 **独立的 macOS 客户端**，专门给本仓库的 mitmproxy 抓包用。不要接到 DReaction 上，不要继续 dreaction 的 `feat/mitm-traffic-gui`。

用户体验对标 DReaction Desktop：原生 Mac 窗口、左侧列表 + 右侧详情、点开一条就能看格式化 JSON。产品目标是：

**用户只启动这一个客户端（一个入口），就能看到 mitmproxy 抓到的接口。**

不要用 mitmweb 当产品。不要做浏览器书签页。不要往业务 App / dreaction SDK 里塞 proxy plugin。

# 为什么拆开

DReaction 的 9600 是 SDK WebSocket 调试桥（`ws` + `api.request`/`api.response`），不是 HTTP 代理。Mitm 是 `mitmdump` 监听（默认 8888）+ `mitmproxy-control`（默认 `127.0.0.1:18765`）。两个协议不能共用 9600。用户明确说：不要再挂在 DReaction 上，新写客户端。

# 已有后端（必须复用，不要另起流量库）

控制服务已经是 GUI / MCP 的真相源（原 Plan 3 就是给独立客户端预留的）：

- 进程：`uv run mitmproxy-control` → FastAPI，`POST /v1/tools/{name}` 镜像全部 MCP 工具
- 探活：`GET /v1/health`，Bearer 来自 `~/.mitmscope/runtime.json`
- 流量：`~/.mitmscope/traffic.db`（`traffic_list` / `traffic_get_detail` / `traffic_read_body` / `traffic_clear`）
- 代理：`proxy_start` / `proxy_stop` / `proxy_status`；真机禁止 `setup_proxy=true`
- MCP（Cursor）继续走 stdio，发现 control 健康就 HTTP 转发，同一份 db

必读：

- `docs/superpowers/specs/2026-08-06-control-service-mcp-forwarder-design.md`（Plan 2，已落地；文中 Plan 3 = SwiftUI 客户端）
- `README.md` 架构与 `~/.mitmscope/`
- `docs/proxy-access-and-dreaction-sync.md`（只看接入层 A：reverse / Wi-Fi；忽略「挂到 DReaction」的产品方案）
- `src/mitm_proxy_mcp/control/` 实际 API

可参考、但 **不要当本项目 spec 继续写**：

- `docs/superpowers/specs/2026-08-26-dreaction-mitm-traffic-gui-design.md`
- `docs/superpowers/plans/2026-08-26-dreaction-mitm-traffic-gui.md`

里面可复用的产品细节（列表轮询、`after_id`+`offset` 连翻、点击后再拉 body、JSON 树 / `<pre>`、1MB 上限、token 不进 UI、不直读 `traffic.db`）可以吸收进 **新 spec**，但 GUI 落点改成独立 App。

DReaction 只读参考（禁止改代码、禁止依赖它的包）：

- `/Users/jorim/agentspace/dreaction/app/src/components/DeviceNetwork.tsx` 布局：顶栏 + 列表 + 右侧详情
- `/Users/jorim/agentspace/dreaction/app/src/components/NetworkRequestDetail.tsx` JSON 详情交互
- `/Users/jorim/agentspace/dreaction/app/src/components/DeviceMitm.tsx` 上一版挂载实现（轮询/空状态文案）——只当逻辑备忘

# 「一个服务」怎么理解（设计时要拍板）

用户要的是 **一个启动入口**，不是把 8888 改成 9600。打开 Mac 客户端后，用户不应再手动开 control / 再开一个 GUI。

请在设计里明确并让用户选：

1. App 启动时自动探测/拉起 `mitmproxy-control`，必要时再 `proxy_start`（推荐，和 MCP 的 discovery 类似）
2. App 把 control 嵌进同一进程（改动大，和现有 MCP 转发要兼容）
3. App 只读已运行的 control（又变回两个入口，用户刚否决过）

代理监听端口继续独立（默认 8888），不要占用 DReaction 的 9600，也不要和 control 的 18765 抢端口。

# 第一版范围（建议，可在 brainstorm 里改）

做：

- macOS 窗口客户端（技术栈在 brainstorm 里三选一拍板：原生 SwiftUI / Electrobun+React 对标 DReaction / Tauri）
- 启动即保证能看到流量（含自动拉起 control / 代理的策略）
- 实时列表（先轮询即可；SSE 骨架已有，本版可不做推送）
- 点开请求：Summary / Request / Response，默认 Response；JSON 可展开
- 状态条：代理是否在跑、端口、条数
- URL 筛选、清空
- UI 进程不持有 `runtime.json` 的 token、不直读 sqlite

第一版不做（除非用户当场要求）：

- Mock 编辑器
- adb reverse / 证书向导做成完整设备管理
- 和 DReaction / 业务 SDK 同步
- 提高 MCP `traffic_list` 的 limit（仍 10，客户端自己连翻）
- Windows / Linux
- 改 MCP 工具对外语义

# 约束

- mock 永远是代理层 `mock_add`，禁止改业务源码
- 真机抓包禁止 Mac 系统代理
- 先设计后代码：读并遵循 `~/.claude/skills/brainstorming/SKILL.md`（或仓库/插件里同等 brainstorming skill）
- HARD-GATE：在写出 `docs/superpowers/specs/YYYY-MM-DD-mitm-mac-client-design.md`、用户批准之前，禁止建仓库脚手架、禁止写 App 代码、禁止改 control 契约（除非 spec 明确要改）
- spec 批准后再走 writing-plans，再实现
- 不要提交 git，除非用户明确要求

# 你现在就做

从 brainstorming 开始：先读上面列出的文档和 `control/` 代码，然后 **一次只问一个问题**。先问技术栈（SwiftUI vs Electrobun vs Tauri），再问「一个入口」是自动拉起 control 还是嵌入进程。不要一上来写代码。
```
