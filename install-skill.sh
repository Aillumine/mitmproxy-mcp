#!/bin/bash

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SKILL_SRC="$SCRIPT_DIR/.cursor/skills/traffic-capture"
SKILL_DST="$HOME/.cursor/skills/traffic-capture"

CURSOR_MCP_CONFIG="$HOME/.cursor/mcp.json"
CLAUDE_MCP_CONFIG="$HOME/.claude.json"
ANTIGRAVITY_MCP_CONFIG="$HOME/.gemini/antigravity/mcp_config.json"

MCP_SERVER_NAME="user-mitmproxy"

echo "=========================================="
echo "  MITM Proxy MCP 一键安装"
echo "=========================================="
echo ""

# 1. 检查 uv
echo "[1/4] 检查 uv ..."
if ! command -v uv &> /dev/null; then
  echo "  ⚠️  未找到 uv，正在安装 ..."
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
fi
echo "  ✅ uv 已就绪: $(uv --version)"

# 2. 安装 Python 依赖
echo ""
echo "[2/4] 安装 Python 依赖 ..."
cd "$SCRIPT_DIR"
uv sync --quiet
echo "  ✅ 依赖安装完成"

# 3. 安装 Cursor Skill
echo ""
echo "[3/4] 安装 Cursor Skill ..."
if [ ! -f "$SKILL_SRC/SKILL.md" ]; then
  echo "  ❌ 找不到 Skill 文件: $SKILL_SRC/SKILL.md"
  exit 1
fi
mkdir -p "$SKILL_DST"
cp -r "$SKILL_SRC/" "$SKILL_DST/"
echo "  ✅ Skill 已安装到 $SKILL_DST"

# 4. 配置 MCP
echo ""
echo "[4/4] 配置 MCP ..."

# 通用 MCP 写入函数
# 用法: add_mcp_entry <config_path> <ide_name>
add_mcp_entry() {
  local config_file="$1"
  local ide_name="$2"

  python3 - <<PYEOF
import json, os, sys

config_file = "$config_file"
server_name = "$MCP_SERVER_NAME"
script_dir  = "$SCRIPT_DIR"

# 确保目录存在
os.makedirs(os.path.dirname(config_file), exist_ok=True)

# 读取已有配置
config = {}
if os.path.exists(config_file):
    try:
        with open(config_file, "r") as f:
            config = json.load(f)
    except Exception:
        pass  # 文件损坏时从空对象开始

servers = config.setdefault("mcpServers", {})

if server_name in servers:
    print(f"  ℹ️  {server_name} 已存在于 {config_file}，跳过")
    sys.exit(0)

servers[server_name] = {
    "command": "uv",
    "args": ["--directory", script_dir, "run", "mitmproxy-mcp"]
}

with open(config_file, "w") as f:
    json.dump(config, f, indent=2)

print(f"  ✅ 已写入 {config_file}")
PYEOF
}

# Cursor
echo ""
echo "  [Cursor] ~/.cursor/mcp.json"
add_mcp_entry "$CURSOR_MCP_CONFIG" "Cursor"

# Claude Code
echo ""
echo "  [Claude Code] ~/.claude.json"
add_mcp_entry "$CLAUDE_MCP_CONFIG" "Claude Code"

# Antigravity
echo ""
echo "  [Antigravity] ~/.gemini/antigravity/mcp_config.json"
add_mcp_entry "$ANTIGRAVITY_MCP_CONFIG" "Antigravity"

echo ""
echo "=========================================="
echo "  🎉 安装完成！"
echo "=========================================="
echo ""
echo "请重启对应的 IDE，然后就可以直接对话抓包了："
echo ""
echo '  💬 "帮我抓包看一下 xx 接口"'
echo '  💬 "搜索包含 order-list 的请求"'
echo '  💬 "mock 这个接口返回 {code: 0}"'
echo ""
echo "首次使用前，需要在终端启动代理："
echo ""
echo "  cd $SCRIPT_DIR"
echo "  uv run mitmproxy-start --setup-proxy"
echo ""
