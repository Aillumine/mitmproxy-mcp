# dreaction Desktop Mitm 流量预览

**日期：** 2026-08-26  
**状态：** 已锁定，待实现  
**前置：** Plan 2 控制服务已落地（`POST /v1/tools/*`、`traffic_list.after_id`、`~/.mitmscope/runtime.json`）  
**关系：** 原 Plan 3 设想独立 SwiftUI 客户端；本 spec 把第一版 GUI 改挂在 dreaction Desktop。接入方式仍见 [proxy-access-and-dreaction-sync.md](../../proxy-access-and-dreaction-sync.md)（方案 A）。本页不是方案 B 的替代：B 是代理状态同步，本页是流量列表 + JSON 预览。

## 1. 目标与非目标

### 目标

- Cursor 经 MCP 抓到的接口，在 dreaction Desktop **Mitm** 页实时可见。
- 点开一条后，响应 / 请求 body 以 **格式化 JSON 树** 展示（复用现有 `JSONView`）。
- Desktop 与 MCP 共用控制服务，同一份 `traffic.db`，不另起存储。

### 非目标（本版不做）

- 独立 SwiftUI / mitmweb 客户端
- 从 Desktop 启动代理、adb reverse、Wi‑Fi 代理、Mock 编辑
- 实现 `/v1/events` SSE
- 提高 MCP `traffic_list` 的 `limit` 上限（仍为 10）
- 把 SDK Network 页与 Mitm 页合并
- token 进入 webview，或 Desktop 直读 `traffic.db`
- 扩展 `runtime.json`（不写 `device_proxy` 等方案 B 字段）

## 2. 已锁定决策

| 项 | 选择 |
|---|---|
| GUI 落点 | dreaction Desktop 新侧栏页 `mitm`，与 SDK `network` 分开 |
| 数据入口 | Bun 读 `~/.mitmscope/runtime.json`，Bearer 调 `POST /v1/tools/{name}` |
| 实时性 | 页面可见时 1s 轮询。增量用固定 `after_id` 锚点 + `offset` 连翻（因列表倒序，不能每页推进锚点） |
| 详情 | 点击后再拉 `traffic_get_detail` + 循环 `traffic_read_body`；默认打开 Response |
| JSON | `JSON.parse` 成功用 `JSONView`；失败用 `<pre>` 原文；body 上限 1MB |
| 筛选 | 第一版仅客户端 URL 包含过滤 |
| 状态条 | 只读：控制服务是否可用、`proxy_running`、端口、`capture_target`、条数 |

## 3. 架构

```
App 流量 ──► mitmdump ──► ~/.mitmscope/traffic.db
                              ▲
Cursor MCP ──HTTP──► mitmproxy-control ◄── dreaction Desktop
                         │                    │
                         └── runtime.json     ├── Bun：读 token、转发工具
                                              └── webview：Mitm 页（无 token）
```

控制服务是唯一真相源。MCP 工具语义不变。Desktop 是只读预览客户端。

## 4. 页面

沿用 Network 页的「顶栏 + 左列表 + 右详情」，列表用表格而不是瀑布图（mitm 入库的是完整请求）。

```
┌─────────────────────────────────────────────────────────┬──────────────────┐
│ Mitm  ● :8888  device   筛选 URL…            清空       │ GET  200  ✕     │
├─────────────────────────────────────────────────────────┤ Summary Request │
│ METHOD  STATUS  URL                    SIZE    TIME     │ Response        │
│ GET     200     /api/v1/user/info      12 KB   86ms     │ JSON 树         │
└─────────────────────────────────────────────────────────┴──────────────────┘
```

- **顶栏：** 运行状态、端口、`capture_target`、条数、URL 筛选、清空（`traffic_clear`）。
- **列表列：** Method、Status、URL（路径优先，hover 完整 URL）、Size、Time。状态色与 Network 一致（2xx 绿 / 3xx 蓝 / 4xx 黄 / 5xx 红）。新行追加到底部；用户未上翻时自动跟随。
- **详情宽约 450px，可关闭；ESC 关闭。** Tab：Summary / Request / Response，默认 Response。加载中占位；超过 1MB 提示截断。
- **侧栏 label：** `Mitm`，避免与 SDK Network 混入口。

### 空状态

| 情况 | 展示 |
|------|------|
| 无 `runtime.json` 或 health 失败 | 控制服务未启动。在 Cursor 说「开始抓包」或运行 `mitmproxy-control`。 |
| health 成功且 `proxy_running: false` | 代理未运行。 |
| 代理在跑、列表为空 | 在 App 里操作即可，无需改手机 Wi‑Fi。 |
| 连续 401 | token 失效，重启控制服务。 |
| 详情记录 404 | 记录已不存在（可能被清空）。 |

## 5. RPC 与数据流

Webview 不读家目录、不持有 token。每次 RPC 由 Bun 现读 `runtime.json`，避免控制服务重启后沿用旧 token。

| RPC | 方向 | 行为 |
|-----|------|------|
| `getMitmStatus` | 请求 | 读 runtime + `GET /v1/health`。返回 `{ available, base_url, proxy_running, proxy_port, capture_target }`，**不含 token**。 |
| `callMitmTool` | 请求 | `{ name, arguments }` → `POST /v1/tools/{name}`，Authorization: Bearer；把工具 JSON 原样返回。Bun 侧超时约 5s。 |

无 webview 推送消息。第一版由页面 `setInterval` 轮询。

### 列表

1. 页面可见时每 1s：先 `getMitmStatus`。不可用则停止 1s 轮询，改为约 3s 再探，并显示对应空状态。
2. `callMitmTool("traffic_list", { after_id, limit: 10, offset })`。工具按时间 **倒序** 返回；`after_id` 是 `timestamp > 锚点`，再 `LIMIT 10`，因此一轮 10 条是「比锚点新的记录里最新的 10 条」，不是「锚点之后的下 10 条」。
3. **首屏：** `after_id` 为空、`offset=0`，只拿最近 10 条，按时间正序插入（旧上新下）。本版不回放全量历史。锚点设为这 10 条里 **最新** 的 `id`。
4. **增量：** 锚点固定为当前已展示最新 `id`。`offset` 从 0 起连翻：`returned === 10` 则 `offset += 10` 再拉，直到少于 10 条。把本轮全部结果按时间正序追加到底部，再把锚点更新为新的最新 `id`。不要每页都把锚点改成该页最新，否则中间记录会被跳过。
5. 页面 hidden 或切离 Mitm 则停轮询；回来从当前锚点续，不整表重拉。
6. URL 筛选只过滤本地已展示行，不传给工具。

### 详情

1. 点击后立即显示列表已有的 method / status / url。
2. 并行：`traffic_get_detail` 与 body 拼接。
3. `traffic_read_body`：`offset=0, length=4000`，`has_more` 则继续，直到拼完或达到 1MB。对 `request_body` 与 `response_body` 各拼一次。
4. 能 `JSON.parse` → `JSONView`；否则 `<pre>`。
5. 快速切换行：递增世代号，丢弃过期响应。

### 映射到现有详情组件

将控制服务返回值适配为 `NetworkRequest` / `NetworkResponse`，复用 `NetworkRequestDetail` + `JSONView`，不新造 JSON 树。

| dreaction 字段 | 来源 |
|----------------|------|
| `request.url` / `request.method` | 列表或 `traffic_get_detail` |
| `request.headers` | `request.request_headers` |
| `request.data` | 拼好的 `request_body` |
| `response.status` | `request.status` |
| `response.headers` | `request.response_headers` |
| `response.body` | 拼好的 `response_body` |
| `duration` | `request.time_ms` |

`method` 若不在 `GET\|POST\|PUT\|DELETE\|PATCH\|OPTIONS` 内，详情按字符串展示，不抛错。

## 6. 失败处理

| 情况 | 处理 |
|------|------|
| 无 runtime / health 失败 | 空状态「控制服务未启动」；轮询降到约 3s |
| `proxy_running: false` | 「代理未运行」；仍 1s 探一次，代理起来后自动出包 |
| `callMitmTool` 401 | 下一轮重读 runtime；连续失败显示 token 失效 |
| 工具 `success: false` | 顶栏轻提示，不清已有列表 |
| 单次 HTTP 超时 / 网络错 | 本次跳过，下次再试；不弹窗 |
| 详情 404 | 侧栏「记录已不存在」 |
| `traffic_clear` | 清空本地列表与选中项，重置 `after_id` |
| 控制服务中途重启 | 下一轮 `getMitmStatus` 拿到新 token；列表继续增量，不整表闪烁 |

## 7. 改动范围

### dreaction（主改）

| 路径 | 改动 |
|------|------|
| `app/src/shared/rpc-types.ts` | 增加 `getMitmStatus`、`callMitmTool` |
| `app/src/bun/mitm.ts` | 新建：读 runtime、health、转发工具 |
| `app/src/bun/index.ts` | 注册上述 RPC |
| `app/src/utils/menu.tsx` | 侧栏增加 Mitm |
| `app/src/components/DeviceMitm.tsx` | 新建：顶栏、列表、轮询、空状态 |
| 详情适配（可放在 `DeviceMitm.tsx` 或小模块） | 映射到 `NetworkRequestDetail` |

不修改 SDK Network 页的数据源与行为。

### mitmproxy-mcp

| 路径 | 改动 |
|------|------|
| `docs/proxy-access-and-dreaction-sync.md` | 交叉引用本 spec；澄清 B 仍是状态同步 |
| 生产代码 | **不改**（含 `traffic_list` limit、`/v1/events`、`runtime.json` 字段） |

## 8. 验收

1. 未起控制服务 → Mitm 页为「控制服务未启动」。
2. Cursor 按方案 A 开始抓包后打开 Desktop → 顶栏显示端口且 `proxy_running`。
3. App 打一个 JSON 接口 → 约 1s 内列表出现；点开默认 Response，JSON 树可展开，内容与 MCP `traffic_read_body` 一致。
4. 非 JSON（如 HTML）→ `<pre>` 原文，页面不崩。
5. 切走 Mitm 再回来 → 只补增量，不整表重绘闪烁。
6. 清空 → 列表与侧栏皆空，之后新请求仍能进来。
7. 关掉控制服务 → 空状态切换；再拉起后自动恢复（新 token）。
8. 现有 Network（SDK XHR）页行为不变。
9. mitmproxy-mcp：`uv run pytest -q` 仍绿。

## 9. 后续（本 spec 不包含）

- 方案 B 完整面板：`device_proxy` 写入、Desktop 一键 reverse
- SSE `traffic.created`，替换轮询
- GUI 专用更高 `limit` 或 REST `/v1/traffic`
- Mock 规则的 Desktop 编辑
- 原 Plan 3 独立 SwiftUI 安装包
