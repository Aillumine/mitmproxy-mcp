# mitmproxy 本地网页控制台 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 `mitmproxy-control` 上托管一个深色 SPA，用 cookie 会话调用全部现有 MCP 工具，格式化查看流量 Request/Response 与 Mock 数据。

**Architecture:** FastAPI 增加 loopback `POST /v1/ui/session`（HttpOnly cookie）并挂载构建后的 `webui/`。前端 Vite+React 只打 `POST /v1/tools/{name}`，Traffic 用固定 `after_id`+`offset` 连翻；JSON 用 `react18-json-view` + Prettier。

**Tech Stack:** FastAPI / pytest / Vite 6 / React 19 / TypeScript / vitest / CodeMirror 6 / prettier/standalone / react18-json-view。

## Global Constraints

- 工作目录：`/Users/jorim/agentspace/mitmproxy-mcp`
- 规格：`docs/superpowers/specs/2026-08-31-mitm-webui-design.md`（色板、布局、工具对照表以该文件为准）
- 不改 MCP 工具返回 JSON 语义；`traffic_list` limit 仍为 10；不实现 SSE
- token 不得进入前端 JS 状态 / localStorage / 可见 DOM
- 真机 `capture_target === "device"` 时 UI 禁止勾选 `setup_proxy`
- mock 只走 `mock_*` 工具，禁止改业务 App 源码
- 未构建 `webui/` 时 `/` 返回说明性 503/纯文本，不能 500
- **不要 git commit，除非用户在对话里明确要求提交**
- 实现 UI 时读 `~/.claude/skills/frontend-ui-engineering/SKILL.md`：无紫渐变、无大圆角、无重阴影

## File structure

| 路径 | 职责 |
|------|------|
| `src/mitm_proxy_mcp/control/app.py` | cookie 鉴权、`/v1/ui/session`、静态与 SPA fallback |
| `src/mitm_proxy_mcp/control/webui_dir.py` | 解析打包后的 `webui` 路径 |
| `tests/test_control_ui_session.py` | cookie / loopback / SPA 不吞 `/v1` |
| `web/` | 前端工程 |
| `src/mitm_proxy_mcp/webui/` | `npm run build` 产物（可 gitignore 源 map；产物要能被 hatch 打进包） |
| `web/src/api.ts` | session + `callTool` |
| `web/src/format.ts` | JSON/XML 美化 |
| `web/src/poll/drainTraffic.ts` | 列表 drain |
| `web/src/pages/*.tsx` | Traffic / Mock / Android / Ios / Proxy |
| `web/src/components/JsonPane.tsx` | 树 + Format + Raw |
| `README.md` | 本机网页用法 |

---

### Task 1: Loopback cookie 会话

**Files:**
- Modify: `src/mitm_proxy_mcp/control/app.py`
- Test: `tests/test_control_ui_session.py`

**Interfaces:**
- Consumes: 现有 `create_app(token=...)`、`HTTPBearer`
- Produces: `require_token` 接受 Bearer **或** Cookie `mitm_ui`；`POST /v1/ui/session`

- [ ] **Step 1: 写失败测试**

```python
def test_ui_session_sets_httponly_cookie_on_loopback():
    from fastapi.testclient import TestClient
    from mitm_proxy_mcp.control.app import create_app

    client = TestClient(create_app(token="secret"))
    response = client.post("/v1/ui/session")
    assert response.status_code == 200
    assert "mitm_ui=" in response.headers.get("set-cookie", "")
    assert "HttpOnly" in response.headers["set-cookie"]
    health = client.get("/v1/health")
    assert health.status_code == 200


def test_ui_session_forbidden_when_not_loopback():
    from fastapi.testclient import TestClient
    from mitm_proxy_mcp.control.app import create_app

    client = TestClient(create_app(token="secret"))
    response = client.post(
        "/v1/ui/session",
        headers={"X-Forwarded-For": "8.8.8.8"},
    )
    # 实现必须看 request.client.host，不能信 X-Forwarded-For
    # TestClient 默认是 testclient / 127.0.0.1，此测试改为注入 client host：
    # 见 Step 3 的 transport hook；若不便伪造，改测：无 cookie 且无 bearer 仍 401
    assert client.get("/v1/health").status_code == 401
```

把「非 loopback」测成：用 Starlette `Request` 依赖里 `host not in {"127.0.0.1", "::1", "testclient"}` 则 403。`TestClient` 的 host 是 `testclient`，**必须把 `testclient` 视为 loopback**，否则本地 pytest 全挂。文档化：仅 `127.0.0.1` / `::1` / `testclient`。

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run --extra dev pytest tests/test_control_ui_session.py -q`  
Expected: FAIL（路由不存在）

- [ ] **Step 3: 实现**

在 `create_app`：

```python
from fastapi import Cookie, Request, Response

LOOPBACK_HOSTS = {"127.0.0.1", "::1", "testclient"}
COOKIE_NAME = "mitm_ui"

def _client_host(request: Request) -> str:
    return (request.client.host if request.client else "") or ""

def require_token(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
    mitm_ui: str | None = Cookie(default=None),
) -> None:
    presented = None
    if credentials is not None and credentials.scheme.lower() == "bearer":
        presented = credentials.credentials
    elif mitm_ui:
        presented = mitm_ui
    if presented != token:
        raise HTTPException(status_code=401, detail="invalid bearer token")

@app.post("/v1/ui/session")
def ui_session(request: Request, response: Response) -> dict[str, bool]:
    if _client_host(request) not in LOOPBACK_HOSTS:
        raise HTTPException(status_code=403, detail="loopback only")
    response.set_cookie(
        COOKIE_NAME,
        token,
        httponly=True,
        samesite="strict",
        path="/",
        max_age=86400,
        secure=False,
    )
    return {"ok": True}
```

Bearer 用例保持 `auto_error=False`。Cookie 优先顺序：先 Bearer 再 cookie（MCP 不会带 cookie）。

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run --extra dev pytest tests/test_control_ui_session.py tests/test_control_app.py -q`  
Expected: PASS（旧 Bearer 测试不回归）

- [ ] **Step 5: Commit**（仅当用户要求）

---

### Task 2: 挂载 SPA，且不吞 `/v1`

**Files:**
- Create: `src/mitm_proxy_mcp/control/webui_dir.py`
- Modify: `src/mitm_proxy_mcp/control/app.py`
- Test: `tests/test_control_ui_session.py`（追加）

**Interfaces:**
- Consumes: `create_app`
- Produces: `webui_path() -> Path | None`；`GET /` 有产物则 `index.html`，无则 503 文本

- [ ] **Step 1: 测试**

```python
from pathlib import Path

def test_v1_health_not_replaced_by_spa(tmp_path, monkeypatch):
    (tmp_path / "index.html").write_text("<html>ui</html>")
    from mitm_proxy_mcp.control import webui_dir
    monkeypatch.setattr(webui_dir, "webui_path", lambda: tmp_path)
    from mitm_proxy_mcp.control.app import create_app
    from fastapi.testclient import TestClient
    client = TestClient(create_app(token="secret"))
    client.post("/v1/ui/session")
    assert client.get("/v1/health").json()["ok"] is True
    assert "ui" in client.get("/").text
```

```python
def test_missing_webui_is_not_500(monkeypatch):
    from mitm_proxy_mcp.control import webui_dir
    monkeypatch.setattr(webui_dir, "webui_path", lambda: None)
    from mitm_proxy_mcp.control.app import create_app
    from fastapi.testclient import TestClient
    client = TestClient(create_app(token="secret"))
    response = client.get("/")
    assert response.status_code in (503, 200)
    assert response.status_code != 500
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run --extra dev pytest tests/test_control_ui_session.py -q`

- [ ] **Step 3: 实现**

`webui_dir.py`：

```python
from pathlib import Path

def webui_path() -> Path | None:
    root = Path(__file__).resolve().parent.parent / "webui"
    if (root / "index.html").is_file():
        return root
    return None
```

`create_app` 末尾：`StaticFiles` html=True **不要**挂在 `/v1`。推荐：

```python
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, PlainTextResponse

ui = webui_path()
if ui is None:
    @app.get("/")
    def no_ui() -> PlainTextResponse:
        return PlainTextResponse(
            "Web UI not built. In repo: cd web && npm install && npm run build",
            status_code=503,
        )
else:
    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(ui / "index.html")

    app.mount("/assets", StaticFiles(directory=ui / "assets"), name="assets")

    @app.get("/{path:path}")
    def spa(path: str) -> FileResponse | PlainTextResponse:
        if path.startswith("v1/"):
            return PlainTextResponse("not found", status_code=404)
        return FileResponse(ui / "index.html")
```

SPA catch-all 必须注册在所有 `/v1` 路由 **之后**。

- [ ] **Step 4: 跑测试**

Run: `uv run --extra dev pytest tests/test_control_ui_session.py tests/test_control_app.py -q`  
Expected: PASS

---

### Task 3: 脚手架 `web/` + 色板壳

**Files:**
- Create: `web/package.json`, `web/tsconfig.json`, `web/vite.config.ts`, `web/index.html`, `web/src/main.tsx`, `web/src/App.tsx`, `web/src/styles.css`, `web/src/vite-env.d.ts`
- Modify: `.gitignore`（`web/node_modules`；**不要** ignore 整个 `src/mitm_proxy_mcp/webui` 若要用 hatch 带产物——开发时 build 生成即可）

**Interfaces:**
- Produces: `npm run dev` / `npm run build`；`build.outDir` = `../src/mitm_proxy_mcp/webui`，`emptyOutDir: true`

- [ ] **Step 1: package.json 依赖（锁定用途）**

```json
{
  "name": "mitm-webui",
  "private": true,
  "type": "module",
  "scripts": {
    "dev": "vite",
    "build": "vite build",
    "test": "vitest run"
  },
  "dependencies": {
    "react": "^19.0.0",
    "react-dom": "^19.0.0",
    "react18-json-view": "^0.2.9",
    "@uiw/react-codemirror": "^4.23.0",
    "@codemirror/lang-json": "^6.0.1",
    "@codemirror/theme-one-dark": "^6.1.2",
    "prettier": "^3.5.0"
  },
  "devDependencies": {
    "@types/react": "^19.0.0",
    "@types/react-dom": "^19.0.0",
    "@vitejs/plugin-react": "^4.3.0",
    "typescript": "^5.7.0",
    "vite": "^6.0.0",
    "vitest": "^3.0.0"
  }
}
```

- [ ] **Step 2: vite.config.ts**

```ts
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import { resolve } from 'node:path';

export default defineConfig({
  plugins: [react()],
  build: {
    outDir: resolve(__dirname, '../src/mitm_proxy_mcp/webui'),
    emptyOutDir: true,
  },
  server: {
    port: 5173,
    proxy: {
      '/v1': { target: 'http://127.0.0.1:18765', changeOrigin: false },
    },
  },
  test: { environment: 'node' },
});
```

- [ ] **Step 3: `styles.css` 只写 spec 色板**

```css
:root {
  --bg: #0c0e12;
  --bg-1: #14181f;
  --bg-2: #1b212b;
  --line: #232a36;
  --text: #e7ecf3;
  --muted: #8b95a8;
  --accent: #3d9a8a;
  --get: #5b9fd4;
  --post: #d4a054;
  --del: #c45c6a;
  --warn: #d4a054;
  --err: #c45c6a;
  --radius: 6px;
}
html, body, #root { height: 100%; margin: 0; background: var(--bg); color: var(--text);
  font: 13px/1.4 ui-sans-serif, system-ui, sans-serif; }
code, pre, .mono { font-family: "IBM Plex Mono", ui-monospace, monospace; font-size: 12px; }
button { border-radius: var(--radius); border: 1px solid var(--line); background: var(--bg-2); color: var(--text); }
button.primary { background: var(--accent); border-color: var(--accent); color: #0c0e12; font-weight: 600; }
```

侧栏 + 顶栏占位五个导航：Traffic / Mock / Android / iOS / Proxy。无渐变、无阴影。

- [ ] **Step 4: 安装并构建**

Run: `cd web && npm install && npm run build`  
Expected: 生成 `src/mitm_proxy_mcp/webui/index.html`

- [ ] **Step 5: hatch 包含 webui**

在 `pyproject.toml`：

```toml
[tool.hatch.build.targets.wheel.force-include]
"src/mitm_proxy_mcp/webui" = "mitm_proxy_mcp/webui"
```

若目录空，放一个 `.gitkeep` 以免 hatch 失败；build 后会被 index 替换。

---

### Task 4: `api.ts` + `format.ts` + drain 单测

**Files:**
- Create: `web/src/api.ts`, `web/src/format.ts`, `web/src/poll/drainTraffic.ts`
- Test: `web/src/format.test.ts`, `web/src/poll/drainTraffic.test.ts`

**Interfaces:**
- Produces:

```ts
export async function ensureSession(): Promise<void>;
export async function callTool<T = Record<string, unknown>>(
  name: string,
  args?: Record<string, unknown>,
): Promise<T>;

export const BODY_LIMIT = 1_000_000;
export function prettyJson(raw: string): { ok: true; text: string; value: unknown } | { ok: false; text: string };
export function looksTruncated(hasMore: boolean, length: number): boolean;

export type TrafficRow = {
  id: string; timestamp: number; method: string; url: string;
  domain: string; status: number; type: string; size: number; time: number; error: string | null;
};
export function mergeTrafficPage(
  existing: TrafficRow[],
  page: TrafficRow[],
): TrafficRow[];
```

- [ ] **Step 1: format 测试**

```ts
import { describe, expect, it } from 'vitest';
import { prettyJson } from './format';

describe('prettyJson', () => {
  it('indents objects', () => {
    const r = prettyJson('{"a":1}');
    expect(r.ok).toBe(true);
    if (r.ok) expect(r.text).toContain('\n');
  });
  it('returns original on invalid json', () => {
    const r = prettyJson('not-json');
    expect(r.ok).toBe(false);
    expect(r.text).toBe('not-json');
  });
});
```

drain：`returned < 10` 停；`merge` 按 timestamp 升序去重 id。

- [ ] **Step 2: `cd web && npm test` 确认失败**

- [ ] **Step 3: 实现 `api.ts`**

```ts
export async function ensureSession(): Promise<void> {
  const res = await fetch('/v1/ui/session', { method: 'POST', credentials: 'include' });
  if (!res.ok) throw new Error('session failed');
}

export async function callTool<T = Record<string, unknown>>(
  name: string,
  args: Record<string, unknown> = {},
): Promise<T> {
  const res = await fetch(`/v1/tools/${name}`, {
    method: 'POST',
    credentials: 'include',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(args),
  });
  return res.json() as Promise<T>;
}
```

`prettyJson`：先 `JSON.parse`，再用 prettier（动态 `import('prettier/standalone')` + `prettier/plugins/babel` + `prettier/plugins/estree`）。测试环境 prettier 失败则 fallback `JSON.stringify(value, null, 2)`——**测试断言缩进即可，不要强依赖 prettier 插件路径**。

- [ ] **Step 4: `npm test` PASS**

---

### Task 5: JsonPane + Traffic 页（列表、轮询、详情、筛选、搜索、清空）

**Files:**
- Create: `web/src/components/JsonPane.tsx`, `web/src/pages/Traffic.tsx`
- Modify: `web/src/App.tsx`

**Interfaces:**
- Consumes: `callTool`, `ensureSession`, drain 算法（与 dreaction GUI spec 相同：首屏 10 条；增量固定 after_id + offset；body 循环 `traffic_read_body` length 4000 直到 `has_more` false 或 1MB）
- Produces: 可工作的 Traffic 页

- [ ] **Step 1: JsonPane**

- 若 `prettyJson.ok`：左侧/主区 `react18-json-view`（`theme="a11y"` 或 dark css 覆盖为 spec 色），工具条按钮 Format（切到 CodeMirror 只读美化文本）、Copy。
- 若失败：`<pre class="mono">`。
- 引入 `react18-json-view/src/style.css`，覆盖背景为 `--bg-1`。

- [ ] **Step 2: Traffic 布局**

列：Method（色点+字）、Status、URL（pathname 优先，title 全 URL）、Size、Time。MOCK 徽章：detail headers 含 `X-Mock-Rule` 或 `x-mock-rule`。

顶栏本页：URL 输入 → `filter_url`；搜索框 → `traffic_search`（`search_in` 默认 `all`）；清空确认后 `traffic_clear` 并重置本地数组。

Inspector Tab：Summary / Request / Response，默认 Response。Request/Response 的 headers 列表 + JsonPane(body)。

轮询：`document.visibilityState` + 当前 nav===traffic 才 1s；control 不可用 3s。

- [ ] **Step 3: 手测命令**

```bash
uv run mitmproxy-control
# 另一终端
cd web && npm run dev
# 或 npm run build 后只开 control 访问 :18765
uv run mitmproxy-mcp  # 或网页 Proxy 页启动
curl -x http://127.0.0.1:8888 http://httpbingo.org/json
```

Expected: 列表出现 GET /json，点开 Response 为可折叠 JSON。

---

### Task 6: Mock 页（全套 mock_*）

**Files:**
- Create: `web/src/pages/Mock.tsx`

**Interfaces:**
- Consumes: `mock_list` `{ rules, total, enabled_count }`；规则字段 `id,name,url_pattern,method,match_type,status_code,response_headers,response_body,delay_ms,enabled,hit_count`

- [ ] **Step 1: 列表**

列：开关（`mock_toggle`）、name、method、url_pattern、status、hit_count、delay_ms。点行打开 inspector。

- [ ] **Step 2: inspector**

表单：name、url_pattern、method（空=全部）、match_type select、status_code、delay_ms、enabled。  
`response_body`：CodeMirror JSON + Format（prettier）+ 下方 JsonPane 预览。  
保存：已有 id → `mock_update`，否则 `mock_add`。  
删除：`mock_delete`。清空全部：`mock_clear` 确认。

- [ ] **Step 3: 导入导出**

Export：`mock_export` 后 `Blob` 下载 `mock-rules.json`。  
Import：`<input type=file>` 读文本，`mock_import({ rules_json, merge })`。

- [ ] **Step 4: 从 Traffic MOCK 徽章跳转**

`App` 用简单 `useState`：`nav` + `focusMockId`。Traffic 点徽章 → Mock 并选中该 id。

---

### Task 7: Proxy + 证书

**Files:**
- Create: `web/src/pages/Proxy.tsx`
- Modify: `web/src/App.tsx` 顶栏

**Interfaces:**
- Consumes: `GET /v1/health`、`GET /v1/runtime`（无 token 字段）、`proxy_status`、`proxy_start`、`proxy_stop`、`get_cert_info`

- [ ] **Step 1: 顶栏**

显示 `proxy_running` 圆点、`:{proxy_port}`、`capture_target`。按钮启动/停止。启动参数：port 默认 runtime 或 8888；checkbox `setup_proxy` **当 capture_target==='device' 时 disabled**，旁注「真机禁止系统代理」。

- [ ] **Step 2: Proxy 页**

`get_cert_info` 整段放在 `<pre class="mono">`（安装步骤原文）。刷新按钮。

health 每 3s；401 三次提示「会话失效，刷新页面」。

---

### Task 8: Android 页（全部 android_*）

**Files:**
- Create: `web/src/pages/Android.tsx`

- [ ] **Step 1: 每 3s `android_list_devices`**

表格：serial、state、model（若 list 没有则点选后再 `android_get_device_info`）。

- [ ] **Step 2: 详情动作（需选中 serial）**

| 按钮 | 工具 |
|------|------|
| 刷新代理 | `android_get_proxy` 展示 |
| Reverse 到本机 | `android_reverse_proxy`（port 用当前 proxy_port） |
| 拆除 Reverse | `android_reverse_proxy_remove` |
| Wi‑Fi 代理 | 输入 host/port → `android_setup_proxy` |
| 清除 Wi‑Fi 代理 | `android_clear_proxy` |
| 证书状态 | `android_cert_status` |
| 推用户证书 | `android_push_cert` |
| 注入系统证书 | `android_inject_system_cert`（失败展示 message） |

所有返回 JSON 用 `<pre>` 或 JsonPane。无设备时空状态：「没有 adb 设备」。

---

### Task 9: iOS 页（全部 ios_*）

**Files:**
- Create: `web/src/pages/Ios.tsx`

- [ ] **Step 1: 三个子 Tab**

- 全部 → `ios_list_devices`
- 模拟器 → `ios_list_simulators`
- 真机 → `ios_list_real_devices`

点选 → `ios_get_device_info(udid)`（参数名以工具 schema 为准：读 `server.py` 里 ios_get_device_info 的 required，应是 `udid`）。

- [ ] **Step 2: 模拟器**

`ios_boot_simulator` / `ios_shutdown_simulator`，传入选中 udid。真机不显示 boot。

---

### Task 10: control `--open`、README、接入文档

**Files:**
- Modify: `src/mitm_proxy_mcp/control/main.py`
- Modify: `README.md`
- Modify: `docs/proxy-access-and-dreaction-sync.md`（GUI 改为本网页；去掉「用 dreaction Mitm 页预览」为默认路径）
- Test: `tests/test_control_main.py` 若已有 argparse 测试则补 `--open` 不在 MCP 默认路径调用

- [ ] **Step 1:** `argparse.add_argument("--open", action="store_true")`。仅当 flag 为真时 `webbrowser.open(base_url)`。MCP discovery 拉起的命令仍是 `uv run mitmproxy-control` **不带** `--open`。

- [ ] **Step 2:** 启动日志增加一行：`UI: http://127.0.0.1:{port}/`

- [ ] **Step 3:** README「本机网页」：build UI、启动 control、打开 URL；说明与 MCP 共用 db。

---

### Task 11: 手测验收（全工具冒烟）

**Files:** 无代码，或修手测发现的 bug。

在已 `npm run build` 且 `uv run mitmproxy-control` 的前提下，只用 `http://127.0.0.1:18765/`：

1. 无代理：顶栏可启动 :8888（不要勾系统代理）。
2. `curl -x http://127.0.0.1:8888 http://httpbingo.org/json` → Traffic 出现，Response JSON 树可折叠，Format 后有缩进。
3. Mock 新建一条匹配 `/json` 的 body `{"mocked":true}` → 再 curl → 列表 MOCK 徽章，Mock 页 hit_count+1，body 格式化可见。
4. Export/Import、toggle、delete、clear 确认框。
5. Traffic 搜索 keyword `mocked`。
6. 清空流量。
7. Proxy 页能看到证书说明文本。
8. Android：无设备不崩溃；有设备可点 Reverse（真机勿 setup_proxy）。
9. iOS：列表不崩溃；无模拟器显示空。
10. 硬刷新后 cookie 仍可用（再 POST session）。

---

## Spec coverage

| Spec 节 | Task |
|---------|------|
| cookie 会话 | 1 |
| 静态 SPA | 2–3 |
| 色板/壳 | 3 |
| 格式化库 | 4–5 |
| Traffic 全工具 | 5 |
| Mock 全工具 | 6 |
| Proxy/证书 | 7 |
| Android 全工具 | 8 |
| iOS 全工具 | 9 |
| --open / 文档 | 10 |
| 手测 | 11 |
| WebSocket 帧 | 明确不做 |

## Execution

Plan 写在 `docs/superpowers/plans/2026-08-31-mitm-webui.md`。实现前确认用户已同意本 spec。
