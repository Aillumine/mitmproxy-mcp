# MITM Proxy MCP

基于 MCP (Model Context Protocol) 的代理抓包服务，支持 iOS 模拟器，让 AI 助手能够帮你抓取和分析 HTTP/HTTPS 流量。

## 功能特点

- **抓包**: 捕获 HTTP/HTTPS 流量，支持按域名、状态码、资源类型筛选
- **智能搜索**: 搜索请求/响应内容，支持大响应分片读取
- **AI 驱动**: 通过自然语言让 AI 助手帮你分析网络请求

## 架构

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
                                  │ HTTP/HTTPS
                         ┌────────┴─────────┐
                         │ 模拟器 / 真机 / Mac │
                         └──────────────────┘
```

- **MCP 服务**（`mitmproxy-mcp`）：Cursor 通过 stdio 调用；启动时自动探测控制服务，健康则 HTTP 转发，否则 fallback 直调 `tools/*`
- **控制服务**（`mitmproxy-control`）：本地 FastAPI 服务，管理代理进程与 `~/.mitmscope/` 下的流量/Mock 数据
- **代理进程**（`mitmproxy-start` / mitmdump）：实际抓包；控制服务模式下由 `proxy_start` 拉起

**Fallback：** 无健康控制服务时，MCP 行为与旧版一致（直调 `tools/*`，默认 `/tmp/*.db`）。

### 数据目录 `~/.mitmscope/`

控制服务模式下，流量与 Mock 数据统一存放在用户目录：

| 文件 | 说明 |
|------|------|
| `traffic.db` | 流量 SQLite 库 |
| `mock.db` | Mock 规则库 |
| `runtime.json` | 控制服务运行时信息（含 Bearer token、端口、抓包目标） |
| `control.log` | 控制服务日志 |

> 不自动迁移 `/tmp` 旧库；需要历史数据请手动复制或重新抓包。

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

安装脚本会在 `~/.zshrc` 中添加 `proxy` 别名（仅启动 mitmdump，**不带** `--setup-proxy`）。新终端窗口直接可用。

**方式三：手动启动**

```bash
# 控制服务（MCP 也会自动拉起，通常无需手动运行）
uv run mitmproxy-control

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

**iOS 模拟器**会自动使用 Mac 的系统代理设置（若已开启）。默认不开启 Mac 系统代理时，可在模拟器内手动配置 Wi-Fi 代理指向 `127.0.0.1:8888`。

**Android / iOS 真机**：使用 MCP 工具 `android_setup_proxy`、`android_reverse_proxy` 等，或将设备 Wi-Fi 代理指向 Mac 局域网 IP（如 `192.168.x.x:8888`）。**不要**开启 Mac 系统代理。

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
| `proxy_start` | **启动代理服务**（后台运行，无需手动在终端运行）|
| `proxy_stop` | **停止代理服务** |
| `proxy_status` | 获取代理状态 |
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
│       └── server.py         # MCP 服务入口
├── tests/
├── docs/                     # 文档
└── resources/                # 资源文件
```

用户数据（控制服务模式）：`~/.mitmscope/`（traffic.db、mock.db、runtime.json）

## 开发

```bash
# 安装开发依赖
uv sync --extra dev

# 运行测试
uv run pytest tests/ -v

# 代码格式化
uv run ruff format .
```