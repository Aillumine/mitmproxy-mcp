# mitmproxy-control 本地网页控制台

**日期：** 2026-08-31  
**状态：** 已锁定，待实现  
**前置：** Plan 2 控制服务已落地（`POST /v1/tools/*`、`~/.mitmscope/`、Bearer）  
**取代：** 不再把 GUI 挂在 DReaction；原 Plan 3 SwiftUI / dreaction Mitm 页都不做。

## 1. 目标与非目标

### 目标

- 用 **一个入口**（`mitmproxy-control`）同时提供控制 API 和本机网页。
- 网页覆盖 **当前全部 MCP 工具** 的可操作/可查看能力（见 §5 对照表）。
- 流量 Request / Response、Mock 响应体 **必须格式化**（JSON 树 + 一键美化；非 JSON 用语法高亮纯文本）。
- 界面按 Proxyman / Charles 的密度做深色控制台，方便整天盯包，而不是营销落地页。

### 成功标准

1. 浏览器打开 `http://127.0.0.1:18765/`（端口以 `runtime.json` 为准）即可用，不必再开第二个 GUI 进程。
2. 列表里能看到刚抓到的 HTTP/HTTPS；点开后 body 是折叠 JSON 树，不是一整行。
3. Mock 页能列出规则、看到格式化后的 mock JSON、增删改、开关、导入导出。
4. Proxy / 证书 / Android / iOS 工具都能从网页点到，结果可见。
5. 页面源码里 **没有** Bearer token；sqlite 仍只在 control / mitmdump 进程读。

### 非目标

- DReaction 集成、独立 `.app` / SwiftUI / Electrobun
- 官方 mitmweb（看不到 `mock.db`）
- WebSocket **帧**监察（当前 addon 不落帧；Upgrade 若作为 HTTP 记录出现则当普通流量。帧存储另开一期）
- SSE 推送（继续 1s 轮询；`GET /v1/events` 保持 501）
- 提高 `traffic_list` 的 `limit`（仍为 10，前端连翻）
- 改 MCP 工具对外 JSON 语义
- 真机默认打开 Mac 系统代理
- 把 mock 做成改业务源码

## 2. 已锁定决策

| 项 | 选择 |
|---|---|
| 形态 | control 托管的 SPA，loopback only |
| 前端 | `web/`：Vite 6 + React 19 + TypeScript。构建产物进 Python 包 `mitm_proxy_mcp/webui/` |
| 样式 | 手写 CSS 变量 + 少量工具类，**不引入** Tailwind / Mantine / shadcn。避免紫渐变、大圆角、重阴影 |
| JSON 查看 | `react18-json-view`（折叠树、暗色） |
| JSON 美化 / Mock 编辑 | CodeMirror 6（`@codemirror/lang-json`）+ `prettier/standalone`（`babel` + `estree` plugin）一键 Format |
| 非 JSON | 尝试 XML 简单缩进；否则等宽 `<pre>`；二进制只显示类型与字节数 |
| Body 上限 | 与此前 GUI spec 相同：拼接 `traffic_read_body` 至 **1MB**，超出提示截断 |
| 实时性 | Traffic 页可见时 1s 轮询；增量固定 `after_id` + `offset` 连翻（工具倒序） |
| 鉴权 | 新增 loopback `POST /v1/ui/session` 发 HttpOnly cookie；原 Bearer 给 MCP 不变 |
| 开浏览器 | MCP 自动拉起 control 时 **不** `webbrowser.open`。CLI `mitmproxy-control --open` 才打开。日志打印 UI URL |
| 启动代理 | control 起来 **不**自动 `proxy_start`。顶栏大红/teal「启动代理」一键调用 |

## 3. 架构

```
浏览器 ──cookie──► mitmproxy-control :18765
                       ├── GET /            SPA
                       ├── POST /v1/ui/session
                       ├── GET  /v1/*       原 API（Bearer 或 cookie）
                       └── POST /v1/tools/* 全量 MCP 镜像
                              │
                              ▼
                    mitmdump :8888 + ~/.mitmscope/*.db
                              ▲
Cursor MCP ──Bearer───────────┘
```

浏览器与 MCP 共用同一 control、同一份 `traffic.db` / `mock.db`。

### 3.1 会话（cookie）

- `POST /v1/ui/session`：**无** Authorization。仅当 `request.client.host` 为 `127.0.0.1` 或 `::1` 时成功。
- 成功：`Set-Cookie: mitm_ui=<token>; HttpOnly; Path=/; SameSite=Strict; Max-Age=86400`（不加 `Secure`，因为是 http://127.0.0.1）。
- 失败：非 loopback → 403；token 未就绪 → 503。
- `require_token` 接受：`Authorization: Bearer` **或** cookie `mitm_ui` 等于启动时 token。
- SPA 启动先 `POST /v1/ui/session`（`credentials: include`），再调其它 API。
- Cookie **值**是 token，但 JS 读不到（HttpOnly）。页面状态、日志、localStorage 禁止出现 token。
- 现有 MCP / 测试继续只测 Bearer；新增 cookie 用例。

### 3.2 静态资源

- `GET /`、`GET /assets/*` 无鉴权（loopback bind 已限制暴露面）。
- SPA 深链：未命中文件的 `GET /traffic` 等返回 `index.html`。
- **禁止**把 `/v1/*` fallback 成 HTML。
- 开发：`web/` 下 `vite`，`server.proxy['/v1'] → 127.0.0.1:18765`，这样 cookie 同源。

## 4. 视觉与交互

对标 Proxyman 列表，而不是 Dribbble 仪表盘。

### 4.1 色板（CSS 变量，禁止再发明）

| Token | 值 | 用途 |
|-------|-----|------|
| `--bg` | `#0c0e12` | 窗口底 |
| `--bg-1` | `#14181f` | 侧栏、顶栏、inspector |
| `--bg-2` | `#1b212b` | 表格行 hover / 输入 |
| `--line` | `#232a36` | 分割线 |
| `--text` | `#e7ecf3` | 主文字 |
| `--muted` | `#8b95a8` | 次要 |
| `--accent` | `#3d9a8a` | 选中行左边条、主按钮、2xx |
| `--get` `#5b9fd4` / `--post` `#d4a054` / `--del` `#c45c6a` | 方法小标签 |
| `--warn` `#d4a054` / `--err` `#c45c6a` | 4xx / 5xx |

圆角一律 `6px`。无 `box-shadow`、无渐变、无 `rounded-2xl`。间距 4/8/12/16/24。正文 13px，表格 12px，JSON 12px IBM Plex Mono（系统等宽回退）。

### 4.2 布局

```
┌ 56px 侧栏 ────┬────────────── 主区 ──────────────────────────────┐
│ Mitm          │ 顶栏：● 代理 :8888  mac   12 条    [启动] [证书] │
│  Traffic      ├─────────────────────────────────────────────────┤
│  Mock         │ 页内工具条（筛选 / 搜索 / 清空 / 新建规则）         │
│  Android      ├────────────────────┬────────────────────────────┤
│  iOS          │ 主列表             │ 右侧 inspector ~420–480px  │
│  Proxy        │                    │                            │
└───────────────┴────────────────────┴────────────────────────────┘
```

- 侧栏图标 + 文字；当前项 `accent` 左边条。
- Traffic / Mock 为默认工作区：左表右详情。Devices / Proxy 可以单栏。
- 选中行 `bg-2` + 左侧 2px accent。
- 空状态用短句 + 一个主按钮（「启动代理」「新建 Mock」），不要插画。

### 4.3 格式化规则

1. 拿到完整字符串后 `JSON.parse`：成功 → JSON 树（默认展开 2 层）+ 工具条「美化」「折叠」「复制」。
2. 美化：`prettier.format(src, { parser: 'json', plugins: [estree, babel] })` 写入 CodeMirror 只读或切换 Raw。
3. `JSON.parse` 失败：若像 XML/HTML，做缩进；否则 `<pre>`。
4. Mock 编辑器默认 CodeMirror JSON 模式；保存前 Format；非法 JSON 仍允许保存（规则存的是字符串），但黄条提示。
5. Header 区：每行 `Name: Value`，值过长可折行。

## 5. MCP 工具 → 页面

| 工具 | 页面 | UI |
|------|------|-----|
| `traffic_list` | Traffic | 轮询列表；筛选 domain / type / status / url |
| `traffic_search` | Traffic 工具条 | 关键词 + search_in；结果高亮进同一张表 |
| `traffic_get_detail` | Inspector | Summary：URL、method、status、type、size、time、error |
| `traffic_read_body` | Inspector | Request / Response Tab，循环直至 EOF 或 1MB |
| `traffic_clear` | Traffic | 清空，需确认 |
| `mock_list` | Mock | 表格：enabled、name、method、pattern、status、hits、delay |
| `mock_add` / `mock_update` | Mock inspector | 表单 + JSON 编辑器 |
| `mock_toggle` | Mock | 行内开关 |
| `mock_delete` / `mock_clear` | Mock | 删除 / 清空，需确认 |
| `mock_export` / `mock_import` | Mock | 下载 JSON / 文件选择；merge 勾选 |
| `proxy_status` / `proxy_start` / `proxy_stop` | 顶栏 + Proxy | 端口输入；`setup_proxy` 仅 capture_target ≠ `device` 时可勾，真机禁用并说明 |
| `get_cert_info` | Proxy / 证书抽屉 | 渲染返回的安装说明（预格式化文本） |
| `android_list_devices` | Android | 设备表，轮询 3s |
| `android_get_device_info` | Android | 点选后详情 |
| `android_get_proxy` / `android_setup_proxy` / `android_clear_proxy` | Android | 当前代理；Wi‑Fi 代理表单（host/port） |
| `android_reverse_proxy` / `android_reverse_proxy_remove` | Android | 主按钮「Reverse 到本机代理」 |
| `android_cert_status` / `android_push_cert` / `android_inject_system_cert` | Android | 证书状态 + 用户/系统证书按钮 |
| `ios_list_devices` / `ios_list_simulators` / `ios_list_real_devices` | iOS | Tab：全部 / 模拟器 / 真机 |
| `ios_get_device_info` | iOS | 详情 |
| `ios_boot_simulator` / `ios_shutdown_simulator` | iOS | 启动 / 关机 |

Traffic 详情若 `response_headers` 含 `X-Mock-Rule`，行上打 **MOCK** 徽章，点击跳到对应 Mock 规则（按 id 匹配；没有则仍显示 id）。

## 6. Traffic 数据流（沿用已验证算法）

1. 页可见且 `proxy_running` 或 control 可用时，每 1s：`traffic_list({ after_id, limit: 10, offset })`。
2. 首屏：`after_id` 空、`offset=0`，只取最近 10 条，按时间正序插入。锚点 = 这批最新 `id`。
3. 增量：锚点固定；`returned === 10` 则 `offset += 10` 再拉，直到 `< 10`。结果正序追加。不要每页改锚点。
4. 筛选：能映射到工具参数的（domain/type/status/url）传给 `traffic_list` 并重置锚点；关键词走 `traffic_search`。
5. 切走页面停快轮询；Android/iOS 3s。
6. 点行：立刻显示列表字段；并行 `traffic_get_detail` + 两个 body 拼接；世代号丢弃过期响应。

## 7. 前端结构

```
web/
  package.json
  vite.config.ts          # proxy /v1；build.outDir → ../src/mitm_proxy_mcp/webui
  index.html
  src/
    main.tsx
    styles.css            # 仅此处定义色板
    api.ts                # session + callTool
    format.ts             # prettier / json tree helpers
    App.tsx               # 侧栏 + 顶栏
    pages/Traffic.tsx
    pages/Mock.tsx
    pages/Android.tsx
    pages/Ios.tsx
    pages/Proxy.tsx
    components/JsonPane.tsx
    components/DataTable.tsx
    poll/drainTraffic.ts  # 纯函数，可单测
```

Python：

- `control/app.py`：cookie 鉴权、session、StaticFiles、SPA fallback
- `control/static.py` 或 app 内：解析 `webui` 目录（开发未构建时 `/` 返回简短 503 说明先 `npm run build`）

## 8. 测试

- pytest：cookie 仅 loopback；非 loopback 403；cookie 可调 `/v1/health`；`/v1/tools/traffic_list` 不被 SPA 吞掉；无 webui 时 `/` 非 500。
- vitest：`drainTraffic`、`format.ts`（合法 JSON / 截断 / 非法 JSON）。
- 手测：控制服务 + 代理 + curl 两条 + Mock 一条，浏览器验收格式化与 Mock 列表。

## 9. 文档

- `README.md`：增加「本机网页」：启动 control、打开 URL、`--open`。
- `docs/proxy-access-and-dreaction-sync.md`：GUI 改为本网页，不再指向 dreaction Mitm 页。
- 不自动改 DReaction 代码。
