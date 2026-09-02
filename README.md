# MITM Proxy MCP

基于 MCP (Model Context Protocol) 的代理抓包服务，带本机网页控制台。支持 Android / iOS 真机与模拟器，以及 Mac 本机流量；Cursor 和浏览器都能查看、搜索、Mock HTTP/HTTPS 与 WebSocket。

## 功能特点

- **本机网页**：Traffic / Mock / Android / iOS / Proxy，与 MCP 共用 `~/.mitmscope/` 数据库
- **抓包**：HTTP/HTTPS，按域名、状态码、资源类型筛选；CONNECT 隧道不进列表
- **WebSocket**：握手记成 `wss://`，后续帧单独入库；Socket.IO 可看 `0{sid}` / 事件；去掉 `permessage-deflate` 以免 App 连不上
- **分类与预览**：顶栏 HTTP / Socket / HTML / JS / CSS / Image / Media；HTML body 可在原文和渲染之间切换
- **智能搜索**：搜索请求/响应内容，大响应分片读取
- **AI 驱动**：用自然语言让助手分析接口、加 Mock
- **默认不改 Mac 系统代理**：`proxy` / `--proxy` 只听 `8888`，真机请走 adb reverse 或设备 Wi‑Fi

## 架构

```
┌─────────────┐  stdio   ┌──────────────────┐
│ Cursor/IDE  │ ◄──────► │ mitmproxy-mcp    │
└─────────────┘          │ (探测/转发/fallback)│
                         └────────┬─────────┘
                                  │ HTTP Bearer
                                  ▼
┌─────────────┐          ┌──────────────────┐     spawn
│ 本机浏览器   │ ◄──────► │ mitmproxy-control│ ──────────► mitmdump
└─────────────┘  cookie  │ ~/.mitmscope/*   │
                         └──────────────────┘
                                  ▲
                                  │ HTTP/HTTPS/WSS
                         ┌────────┴─────────┐
                         │ 模拟器 / 真机 / Mac │
                         └──────────────────┘
```

- **MCP 服务**（`mitmproxy-mcp`）：Cursor 通过 stdio 调用；启动时自动探测控制服务，健康则 HTTP 转发，否则 fallback 直调 `tools/*`
- **控制服务**（`mitmproxy-control`）：本地 FastAPI 服务，托管本机网页、管理代理进程与 `~/.mitmscope/` 下的流量/Mock 数据
- **代理进程**（`mitmproxy-start` / mitmdump）：实际抓包；控制服务模式下由 `proxy_start` 或 `--proxy` 拉起

**Fallback：** 无健康控制服务时，MCP 行为与旧版一致（直调 `tools/*`，默认 `/tmp/*.db`）。

### 数据目录 `~/.mitmscope/`

控制服务模式下，流量与 Mock 数据统一存放在用户目录：

| 文件 | 说明 |
|------|------|
| `traffic.db` | 流量 SQLite 库 |
| `mock.db` | Mock 规则库 |
| `throttle.json` | 弱网档位（off / 4g / 3g / 2g） |
| `runtime.json` | 控制服务运行时信息（含 Bearer token、端口、抓包目标） |
| `control.log` | 控制服务日志 |

> 不自动迁移 `/tmp` 旧库；需要历史数据请手动复制或重新抓包。

### 本机网页

控制服务托管一个本机网页控制台（流量 / Mock / 代理 / 设备），与 MCP **共用同一套** `~/.mitmscope/` 数据库（`traffic.db`、`mock.db`）。

```bash
# 构建前端（产物写入 Python 包内的 webui/）
cd web && npm run build

# 启动控制服务
uv run mitmproxy-control
```

启动日志会打印 `UI: http://127.0.0.1:{port}/`，用浏览器打开该地址即可。需要自动打开浏览器时加 `--open`：

```bash
# 日常一条命令：网页控制台 + 抓包代理 + 打开浏览器
uv run mitmproxy-control --proxy
# 或全局别名 / 入口：
proxy
uv run proxy
```

`--proxy` 会启动控制服务、打开本机网页，并在后台拉起 `mitmproxy-start`（不另开终端、默认不改 Mac 系统代理）。只要网页、不要代理时用 `uv run mitmproxy-control --open`。

MCP 自动拉起控制服务时**不会**带 `--open` / `--proxy`，也不会弹出浏览器。在本机网页点「启动」也可以再拉起代理。

**Traffic 页：**

| 能力 | 说明 |
|------|------|
| 分类 | 全部 / HTTP / Socket / HTML / JS / CSS / Image / Media |
| 分组 | 按 `https://host/` 与 `wss://host/` 折叠 |
| WebSocket | 握手 `GET 101` + 后续 `WS` 帧；Socket.IO 前缀（如 `42[...]`）会格式化里面的 JSON |
| HTML | Response 可切 **原文** / **HTML**（沙箱渲染，不跑脚本） |
| 复制 | cURL、接口 path、请求/响应头与参数 |
| 清空 | 用页面 Clear；**重启代理不会清库** |

图片 / 视频 / 音频走流式转发，列表里仍有 URL，body 可能为空。真机抓包时**不要**勾顶栏 `setup_proxy`。

### 真机抓包：禁止 Mac 系统代理

真机（Android / iOS）抓包时，**不会也不应**设置 Mac Wi-Fi 系统代理，避免污染本机网络。推荐路径：mitm 只监听 + `android_reverse_proxy` / 设备 Wi-Fi 指向 Mac IP。

| 抓包目标 | Mac 系统代理（`setup_proxy`） |
|----------|-------------------------------|
| `device`（真机） | **强制禁止** |
| `simulator`（模拟器） | 默认 `false`；仅用户显式要求才允许 |
| `mac`（仅本机） | 默认 `false`；仅用户显式要求才允许 |

## 快速开始

### 1. 环境要求

- Python 3.11+
- [uv](https://github.com/astral-sh/uv) (Python 包管理器)
- 改本机网页时需要 Node.js 18+（`cd web && npm run build`）

**安装 uv：**

```bash
# macOS
curl -LsSf https://astral.sh/uv/install.sh | sh

# 或通过 pip
pip install uv
```

### 2. 安装

```bash
# 下载项目
cd mitmproxy-mcp

# 安装依赖
uv sync
```

### 3. 一键安装（推荐）

**远程安装（无需手动 clone，直接运行）：**

```bash
curl -fsSL https://raw.githubusercontent.com/Aillumine/mitmproxy-mcp/main/install.sh | bash
```

自动完成：clone 到 `~/.mitmproxy-mcp`、安装依赖、配置 MCP、安装用户级 Skill，支持 Cursor、Claude Code、Antigravity。

**本地安装（已 clone 仓库时）：**

```bash
bash install-skill.sh
```

### 4. 手动配置 MCP

将以下内容写入对应 IDE 的配置文件，并将 `/path/mitmproxy-mcp` 替换为实际项目路径。

**Cursor** — `~/.cursor/mcp.json`

```json
{
  "mcpServers": {
    "user-mitmproxy": {
      "command": "uv",
      "args": ["--directory", "/path/mitmproxy-mcp", "run", "mitmproxy-mcp"]
    }
  }
}
```

**Claude Code** — `~/.claude.json`

```json
{
  "mcpServers": {
    "user-mitmproxy": {
      "command": "uv",
      "args": ["--directory", "/path/mitmproxy-mcp", "run", "mitmproxy-mcp"]
    }
  }
}
```

**Antigravity** — `~/.gemini/antigravity/mcp_config.json`

```json
{
  "mcpServers": {
    "user-mitmproxy": {
      "command": "uv",
      "args": ["--directory", "/path/mitmproxy-mcp", "run", "mitmproxy-mcp"]
    }
  }
}
```

### 5. 重启 IDE

配置完成后，重启对应 IDE 使配置生效。

---

## 使用方法

### 第一步：启动代理

有三种启动方式：

**方式一：在 Cursor 中启动（推荐）**

> "启动代理"

Cursor 会调用 `proxy_start`，MCP 会自动探测或拉起 `mitmproxy-control`。默认**不**设置 Mac 系统代理。

**方式二：全局别名（安装后自动配置）**

```bash
proxy
```

安装脚本会在 `~/.zshrc` 中添加 `proxy` 别名（`mitmproxy-control --proxy`：网页 + 后台抓包，**不改** Mac 系统代理）。新终端窗口直接可用。

**方式三：手动启动**

```bash
# 控制服务（MCP 也会自动拉起，通常无需手动运行）
uv run mitmproxy-control
# 同时打开本机网页：uv run mitmproxy-control --open

# 仅启动代理（不设置 Mac 系统代理）
uv run mitmproxy-start
```

<details>
<summary><strong>可选：显式开启 Mac 系统代理</strong>（仅模拟器/本机抓包且用户明确要求时）</summary>

```bash
uv run mitmproxy-start --setup-proxy
# 或在 Cursor 中说「启动代理并设置 Mac 系统代理」
```

关闭时（Ctrl+C 或 `proxy_stop`）会自动恢复系统代理。真机抓包时此选项会被强制否决。

</details>

> 保持代理进程运行，不要关闭。控制服务退出时会清理 `runtime.json` 并尽力停止代理。

### 第二步：配置设备代理

完整触发条件、方案 A/B/C（与 dreaction 同步）见 **[docs/proxy-access-and-dreaction-sync.md](docs/proxy-access-and-dreaction-sync.md)**。

**Android 默认（推荐，免手填 Wi‑Fi）：**

1. 启动代理（`proxy_start`，不要开 Mac 系统代理）
2. `android_list_devices` → `android_reverse_proxy(serial, 8888)`
3. 设备全局代理变为 `127.0.0.1:8888`，经 USB adb reverse 到本机 mitm

**仅在无 adb / 必须无线同网时**：`android_setup_proxy(serial, MacIP, 8888)`，或手动将手机 Wi‑Fi 代理指向 `192.168.x.x:8888`。

**iOS 模拟器**：可跟随 Mac 系统代理（需用户明确要求 `setup_proxy=true`）；否则在模拟器 Wi‑Fi 填 `127.0.0.1:8888`。

**真机抓包不要开启 Mac 系统代理。**

<details>
<summary><strong>手动配置 Mac 系统代理</strong>（仅本机/模拟器且已显式开启 setup_proxy 时）</summary>

1. Mac 系统设置 → 网络 → Wi-Fi → 高级 → 代理
2. 勾选 **网页代理(HTTP)**：`127.0.0.1:8888`
3. 勾选 **安全网页代理(HTTPS)**：`127.0.0.1:8888`
4. 保存

</details>

### 第三步：安装 CA 证书（抓 HTTPS 必须）

**iOS 模拟器证书安装：**
1. 在 iOS 模拟器的 Safari 中访问 `http://mitm.it`
2. 点击 **Apple** 图标下载证书
3. 设置 → 通用 → VPN与设备管理 → 安装描述文件
4. 设置 → 通用 → 关于本机 → 证书信任设置 → 启用 mitmproxy

**Mac 浏览器证书安装（重要！）：**
如果 Mac 系统代理已开启，Mac 浏览器访问 HTTPS 网站也需要安装证书，否则会显示证书错误无法访问。

1. 在 Mac 浏览器（Safari/Chrome）中访问 `http://mitm.it`
2. 点击 **Apple** 图标下载证书（`mitmproxy-ca-cert.pem`）
3. 双击下载的证书文件，打开"钥匙串访问"
4. 找到 `mitmproxy` 证书，双击打开
5. 展开"信任"选项，将"使用此证书时"设置为"始终信任"
6. 关闭窗口，输入密码确认

> **提示**：安装证书后，Mac 浏览器才能正常访问 HTTPS 网站。如果浏览器仍无法打开网页，请检查代理是否正常启动。

---

### 第四步：在 Cursor 中查询流量

在 **Cursor** 中，用自然语言查询流量：

**基础查询：**
> "显示最近的网络请求"
> "显示 /apim/v3/earn/ 的请求"
> "显示所有失败的请求（状态码 4xx 或 5xx）"

**搜索内容：**
> "搜索响应中包含 '收益先锋-BTC' 的请求"
> "搜索 URL 中包含 search 的请求"
> "搜索请求头中包含 X-Token 的请求"

**查看大响应：**
> "读取 req-5 的响应体"
> "继续读取 req-5 响应体，从 4000 开始"

**智能分析：**
> "帮我找持仓列表接口"
> "分析这个 API 的请求参数"

### 第五步：停止抓包

在 Cursor 中说「停止代理」，或运行 `proxy_stop`。若使用了 `--setup-proxy`，Mac 系统代理会自动关闭。

---

## MCP 工具列表

| 工具 | 说明 |
|-----|------|
| `proxy_start` | **启动代理服务**（后台运行，默认不改 Mac 系统代理）|
| `proxy_stop` | **停止代理服务** |
| `proxy_status` | 获取代理状态 |
| `throttle_get` | 获取弱网档位（关闭 / 4G / 3G / 2G）|
| `throttle_set` | 设置弱网档位（代理运行中立即生效）|
| `traffic_list` | 列出流量（支持域名/状态码/类型筛选）|
| `traffic_search` | 搜索流量内容（URL/请求头/请求体/响应头/响应体）|
| `traffic_get_detail` | 获取请求元数据（请求头、响应头等）|
| `traffic_read_body` | 分片读取大响应体 |
| `traffic_clear` | 清空流量记录 |
| `get_cert_info` | 获取证书安装指南 |

### 快速启动代理

在 Cursor 中直接对话即可，MCP 会自动管理控制服务与代理：

> "启动代理"
> "启动代理并设置 Mac 系统代理"（仅模拟器/本机；真机会被否决）
> "停止代理"

---

## 常见问题

### Q: iOS 模拟器配置代理后无法连接网络？

1. 确认代理已启动（检查终端窗口）
2. 确认 Mac 系统代理设置正确（127.0.0.1:8888）
3. 检查代理是否监听 0.0.0.0（而不是仅 localhost）
4. 确认已安装并信任 mitmproxy CA 证书

### Q: 能抓到 HTTP 但抓不到 HTTPS？

需要在 iOS 模拟器中安装 CA 证书。在 Safari 中访问 `http://mitm.it` 下载安装，并在证书信任设置中启用。

### Q: 安装了证书但某些请求还是失败？

- 确认证书已在"证书信任设置"中启用
- 部分服务可能有 SSL Pinning（证书锁定），需要特殊处理

### Q: Mac 浏览器打开代理后无法访问网页？

**可能原因：**

1. **未安装 CA 证书**（最常见）
   - Mac 浏览器访问 HTTPS 网站需要安装 mitmproxy CA 证书
   - 在浏览器中访问 `http://mitm.it` 下载证书并安装到钥匙串
   - 将证书设置为"始终信任"

2. **代理未正确启动**
   - 检查代理进程是否在运行：`lsof -i :8888`
   - 检查代理日志是否有错误
   - 尝试手动在终端运行 `uv run mitmproxy-start` 查看错误信息

3. **端口被占用**
   - 检查端口是否被其他进程占用：`lsof -i :8888`
   - 使用 `proxy_stop` 停止旧代理，或使用 `--port` 指定其他端口

4. **系统代理设置错误**
   - 确认代理地址为 `127.0.0.1:8888`（不是其他 IP）
   - 确认 HTTP 和 HTTPS 代理都已启用

### Q: 响应太大，MCP 无法返回？

使用 `traffic_search` 搜索关键词定位，然后用 `traffic_read_body` 分片读取。

### Q: 网页里看不到 WSS，或 App 开代理就连不上 WebSocket？

1. **列表是空的**：先确认代理已启动、设备流量能进 `8888`；重启代理**不会**清库，要用页面 Clear 才清空。
2. **只有心跳 `2`/`3`、没有 `0{sid}`**：旧版本会弄坏 `permessage-deflate`；当前 addon 会去掉该扩展，重启 `proxy` 后再连一次。
3. **TLS `certificate unknown`**：未 root 的 Android 默认不信任用户 CA，部分 HTTPS/WSS 会失败，这不是过滤把 Socket 藏起来了。
4. **分类**：WSS 在 **Socket**，不在 HTTP。

### Q: Google Translate 这类 HTML 响应在哪？

顶栏点 **HTML**。详情 Response 里切 **HTML** 可渲染预览，**原文**仍是源码。Content-Type 不是 `text/html` 但 body 是 HTML 片段的，也会归到这一类。

---

## 项目结构

```
mitmproxy-mcp/
├── README.md
├── pyproject.toml
├── src/
│   └── mitm_proxy_mcp/
│       ├── bridge/           # MCP → 控制服务探测/转发
│       ├── cli/              # mitmproxy-start CLI
│       ├── control/          # mitmproxy-control 控制服务
│       ├── core/             # SQLite 流量/Mock 存储
│       ├── tools/            # MCP 工具实现
│       ├── webui/            # 前端构建产物（control 托管）
│       └── server.py         # MCP 服务入口
├── tests/
├── web/                      # 本机网页源码（Vite + React）
├── docs/                     # 文档
└── resources/                # 资源文件
```

用户数据（控制服务模式）：`~/.mitmscope/`（traffic.db、mock.db、runtime.json）

## 开发

```bash
# 安装开发依赖
uv sync --extra dev

# 运行 Python 测试
uv run pytest tests/ -v

# 前端（改 web/ 后必须 build，control 读的是 webui/）
cd web && npm test && npm run build

# 代码格式化
uv run ruff format .
```