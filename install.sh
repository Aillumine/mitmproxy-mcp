#!/bin/bash
# 用法：curl -fsSL https://raw.githubusercontent.com/Aillumine/mitmproxy-mcp/main/install.sh | bash

set -e

REPO_URL="https://github.com/Aillumine/mitmproxy-mcp.git"
INSTALL_DIR="${MITMPROXY_MCP_DIR:-$HOME/.mitmproxy-mcp}"
MCP_SERVER_NAME="user-mitmproxy"

CURSOR_MCP_CONFIG="$HOME/.cursor/mcp.json"
CLAUDE_MCP_CONFIG="$HOME/.claude.json"
ANTIGRAVITY_MCP_CONFIG="$HOME/.gemini/antigravity/mcp_config.json"

CURSOR_SKILL_DST="$HOME/.cursor/skills/traffic-capture"
CLAUDE_SKILL_DST="$HOME/.claude/commands"

echo "=========================================="
echo "  MITM Proxy MCP 远程安装"
echo "=========================================="
echo ""

# 1. 检查 uv
echo "[1/5] 检查 uv ..."
if ! command -v uv &> /dev/null; then
  echo "  未找到 uv，正在安装 ..."
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
fi
echo "  uv 已就绪: $(uv --version)"

# 2. 克隆或更新仓库
echo ""
echo "[2/5] 安装到 $INSTALL_DIR ..."
if [ -d "$INSTALL_DIR/.git" ]; then
  echo "  已存在，更新中 ..."
  git -C "$INSTALL_DIR" pull --ff-only --quiet
else
  git clone --depth=1 "$REPO_URL" "$INSTALL_DIR"
fi
echo "  代码就绪"

# 3. 安装 Python 依赖
echo ""
echo "[3/5] 安装 Python 依赖 ..."
uv sync --quiet --project "$INSTALL_DIR"
echo "  依赖安装完成"

# 4. 安装 Skill（用户级别）
echo ""
echo "[4/5] 安装 Skill（用户级别）..."

# Cursor：~/.cursor/skills/traffic-capture/
SKILL_SRC="$INSTALL_DIR/.cursor/skills/traffic-capture"
if [ -d "$SKILL_SRC" ]; then
  mkdir -p "$CURSOR_SKILL_DST"
  cp -r "$SKILL_SRC/" "$CURSOR_SKILL_DST/"
  echo "  Cursor Skill -> $CURSOR_SKILL_DST"
else
  echo "  [跳过] 未找到 Cursor Skill 源：$SKILL_SRC"
fi

# Claude Code：~/.claude/commands/traffic-capture.md
if [ -f "$SKILL_SRC/SKILL.md" ]; then
  mkdir -p "$CLAUDE_SKILL_DST"
  cp "$SKILL_SRC/SKILL.md" "$CLAUDE_SKILL_DST/traffic-capture.md"
  echo "  Claude Code Skill -> $CLAUDE_SKILL_DST/traffic-capture.md"
else
  echo "  [跳过] 未找到 Skill 源文件"
fi

# 5. 配置 MCP
echo ""
echo "[5/5] 配置 MCP ..."

add_mcp_entry() {
  local config_file="$1"
  local ide_name="$2"

  python3 - <<PYEOF
import json, os, sys

config_file  = "$config_file"
server_name  = "$MCP_SERVER_NAME"
install_dir  = "$INSTALL_DIR"

os.makedirs(os.path.dirname(os.path.abspath(config_file)), exist_ok=True)

config = {}
if os.path.exists(config_file):
    try:
        with open(config_file, "r") as f:
            config = json.load(f)
    except Exception:
        pass

servers = config.setdefault("mcpServers", {})

if server_name in servers:
    # 更新 directory 指向当前安装路径
    servers[server_name]["args"] = ["--directory", install_dir, "run", "mitmproxy-mcp"]
    with open(config_file, "w") as f:
        json.dump(config, f, indent=2)
    print(f"  已更新 {config_file}")
    sys.exit(0)

servers[server_name] = {
    "command": "uv",
    "args": ["--directory", install_dir, "run", "mitmproxy-mcp"]
}

with open(config_file, "w") as f:
    json.dump(config, f, indent=2)

print(f"  已写入 {config_file}")
PYEOF
}

echo ""
echo "  [Cursor] $CURSOR_MCP_CONFIG"
add_mcp_entry "$CURSOR_MCP_CONFIG" "Cursor"

echo ""
echo "  [Claude Code] $CLAUDE_MCP_CONFIG"
add_mcp_entry "$CLAUDE_MCP_CONFIG" "Claude Code"

echo ""
echo "  [Antigravity] $ANTIGRAVITY_MCP_CONFIG"
add_mcp_entry "$ANTIGRAVITY_MCP_CONFIG" "Antigravity"

echo ""
echo "=========================================="
echo "  安装完成！"
echo "=========================================="
echo ""
echo "MCP server 名称：$MCP_SERVER_NAME"
echo "安装路径：$INSTALL_DIR"
echo ""
echo "下一步："
echo "  1. 重启 Cursor / Claude Code 使 MCP 生效"
echo "  2. 启动代理："
echo ""
echo "     uv run --project $INSTALL_DIR mitmproxy-start --setup-proxy"
echo ""
echo "然后直接对话："
echo '  "帮我抓包看一下 xx 接口"'
echo '  "mock 这个接口返回 {code: 0}"'
echo ""
