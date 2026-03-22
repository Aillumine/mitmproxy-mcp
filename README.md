# MITM Proxy MCP

基于 MCP (Model Context Protocol) 的代理抓包服务，支持 iOS 模拟器，让 AI 助手能够帮你抓取和分析 HTTP/HTTPS 流量。

## 功能特点

- **抓包**: 捕获 HTTP/HTTPS 流量，支持按域名、状态码、资源类型筛选
- **智能搜索**: 搜索请求/响应内容，支持大响应分片读取
- **AI 驱动**: 通过自然语言让 AI 助手帮你分析网络请求

## 架构

```
┌─────────────────┐     SQLite      ┌─────────────────┐
│  代理服务        │ ─────────────→  │  MCP 服务        │
│  (终端手动启动)   │   流量数据共享   │  (Cursor 调用)   │
│  mitmdump       │                 │  查询/搜索/分析   │
└─────────────────┘                 └─────────────────┘
        ↑
        │ HTTP/HTTPS
        │
   ┌─────────────┐
   │ iOS 模拟器   │
   └─────────────┘
```

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

```bash
bash install-skill.sh
```

自动完成：安装依赖、配置 MCP、安装 Cursor Skill，支持 Cursor、Claude Code、Antigravity。

### 4. 手动配置 MCP

将以下内容写入对应 IDE 的配置文件，并将 `/path/mitmproxy-mcp` 替换为实际项目路径。

**Cursor** — `~/.cursor/mcp.json`

```json
{
  "mcpServers": {
    "mitmproxy": {
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
    "mitmproxy": {
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
    "mitmproxy": {
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

有两种启动方式：

**方式一：启动代理 + 自动设置 Mac 系统代理（推荐）**

```bash
uv run mitmproxy-start --setup-proxy
```

此命令会自动将 Mac Wi-Fi 的 HTTP/HTTPS 代理设置为 `127.0.0.1:8888`，关闭时（Ctrl+C）自动恢复。

**方式二：仅启动代理（手动配置系统代理）**

```bash
uv run mitmproxy-start
```

需要手动在 Mac 系统设置中配置代理，详见下方「手动配置代理」。

> 保持终端窗口运行，不要关闭。

### 第二步：配置 iOS 模拟器代理

**iOS 模拟器会自动使用 Mac 的系统代理设置。**

如果使用了 `--setup-proxy`，代理已自动配置完毕，跳到第三步。

<details>
<summary><strong>手动配置代理</strong>（未使用 --setup-proxy 时）</summary>

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

在运行代理的终端窗口按 `Ctrl+C` 停止代理。

- 如果使用了 `--setup-proxy`，Mac 系统代理会自动关闭。
- 如果手动配置了代理，记得在 Mac 系统设置中关闭代理。

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

现在可以直接在 Cursor 中启动代理，无需手动在终端运行命令：

> "启动代理"
> "启动代理并设置 Mac 系统代理"
> "停止代理"

Cursor 会自动调用 `proxy_start` 和 `proxy_stop` 工具来管理代理服务。

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
│       ├── cli/              # 命令行工具
│       │   └── start.py      # 代理启动脚本
│       ├── core/             # 核心模块
│       │   └── sqlite_store.py  # SQLite 流量存储
│       ├── tools/            # MCP 工具
│       └── server.py         # MCP 服务入口
├── tests/
├── docs/                     # 文档
└── resources/                # 资源文件
    └── MoveCertificate-v1.5.5.zip  # 证书移动模块
```

## 开发

```bash
# 安装开发依赖
uv sync --extra dev

# 运行测试
uv run pytest tests/ -v

# 代码格式化
uv run ruff format .
```