# dreaction Desktop Mitm 流量预览 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 dreaction Desktop 新增 **Mitm** 页，只读轮询 mitmproxy-control，实时列出 MCP 抓到的流量并用现有 JSON 树预览 body。

**Architecture:** Bun 主进程每次现读 `~/.mitmscope/runtime.json`（token 不出进程），经 Bearer 调控制服务 `GET /v1/health` 与 `POST /v1/tools/{name}`。webview 只走 RPC。列表增量用固定 `after_id` 锚点 + `offset` 连翻（工具倒序返回）。详情点击后再拉 `traffic_get_detail` 并循环 `traffic_read_body`（上限 1MB），映射到现有 `NetworkRequestDetail`。

**Tech Stack:** dreaction Desktop（Electrobun + React 19 + Mantine 7 + Zustand）/ Bun 内置 `bun test` / 控制服务已有 HTTP API。实现仓库是 **dreaction**，不是 mitmproxy-mcp。

## Global Constraints

- 实现工作目录：`/Users/jorim/agentspace/dreaction`
- **不改** mitmproxy-mcp 生产代码（`traffic_list` limit 仍为 10、不实现 SSE、不扩展 `runtime.json`）
- token **不得**出现在 RPC 返回值或 webview 状态里
- 不把 SDK `network` 页与 Mitm 合并；不从 Desktop 启动代理 / reverse / Mock
- 纯逻辑用 `bun test`（app 目前无测试框架，不要为这一功能引入 vitest）
- 提交只在 dreaction 仓库；commit message 不要带 AI 署名
- 规格：`mitmproxy-mcp/docs/superpowers/specs/2026-08-26-dreaction-mitm-traffic-gui-design.md`

## File structure

| 路径（均相对 dreaction） | 职责 |
|--------------------------|------|
| `app/src/shared/rpc-types.ts` | RPC：`getMitmStatus`、`callMitmTool` |
| `app/src/bun/mitm.ts` | 读 runtime、health、转发工具；可注入 `readFile`/`fetchFn` |
| `app/src/bun/mitm.test.ts` | 控制客户端单测 |
| `app/src/bun/index.ts` | 注册两个 RPC handler |
| `app/src/utils/mitm-poll.ts` | 首屏 + 增量 drain（纯函数） |
| `app/src/utils/mitm-poll.test.ts` | drain 单测 |
| `app/src/utils/mitm-detail.ts` | 拼 body、映射 `NetworkRequest`/`NetworkResponse` |
| `app/src/utils/mitm-detail.test.ts` | 映射 / 截断单测 |
| `app/src/components/DeviceMitm.tsx` | 顶栏、表、轮询、空状态、侧栏 |
| `app/src/components/NetworkRequestDetail.tsx` | 增加 `defaultTab`；非对象 body 用 `<pre>` |
| `app/src/utils/menu.tsx` | 侧栏 Mitm 项 |
| `app/package.json` | `"test": "bun test src"` |

---

### Task 1: Bun 控制客户端（读 runtime + 调工具）

**Files:**
- Create: `app/src/bun/mitm.ts`
- Create: `app/src/bun/mitm.test.ts`
- Modify: `app/package.json`（scripts 增加 `"test": "bun test src"`）

**Interfaces:**
- Consumes: 本地 `runtime.json`；`GET {base_url}/v1/health`；`POST {base_url}/v1/tools/{name}`
- Produces:
  - `MitmStatus = { available: boolean; base_url?: string; proxy_running: boolean; proxy_port?: number; capture_target?: string }`
  - `MitmToolCallResult = { http_status: number; body: Record<string, unknown> }`
  - `getMitmStatus(deps?: MitmDeps): Promise<MitmStatus>`
  - `callMitmTool(name: string, args: Record<string, unknown>, deps?: MitmDeps): Promise<MitmToolCallResult>`
  - `MitmDeps = { runtimePath?: string; readFile?: (path: string) => Promise<string>; fetchFn?: typeof fetch }`
- `getMitmStatus` **不得**把 `token` 放进返回值。超时 5s（`AbortSignal.timeout(5000)`）。

- [ ] **Step 1: 加上 bun test 脚本**

在 `app/package.json` 的 `scripts` 里增加：

```json
"test": "bun test src"
```

- [ ] **Step 2: 写失败的测试**

创建 `app/src/bun/mitm.test.ts`：

```typescript
import { describe, expect, test } from 'bun:test';
import { callMitmTool, getMitmStatus } from './mitm';

const runtimeJson = JSON.stringify({
  version: 1,
  pid: 1,
  base_url: 'http://127.0.0.1:18765',
  token: 'secret-token',
  traffic_db: '/tmp/t.db',
  mock_db: '/tmp/m.db',
  proxy_port: 8888,
  capture_target: 'device',
  started_at: '2026-08-26T00:00:00Z',
});

describe('getMitmStatus', () => {
  test('returns unavailable when runtime file is missing', async () => {
    const status = await getMitmStatus({
      readFile: async () => {
        throw new Error('ENOENT');
      },
    });
    expect(status).toEqual({ available: false, proxy_running: false });
  });

  test('does not leak token when control is healthy', async () => {
    const status = await getMitmStatus({
      readFile: async () => runtimeJson,
      fetchFn: (async () =>
        new Response(JSON.stringify({ ok: true, proxy_running: true }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        })) as typeof fetch,
    });
    expect(status.available).toBe(true);
    expect(status.proxy_running).toBe(true);
    expect(status.proxy_port).toBe(8888);
    expect(status.capture_target).toBe('device');
    expect(status.base_url).toBe('http://127.0.0.1:18765');
    expect(JSON.stringify(status)).not.toContain('secret-token');
    expect(status).not.toHaveProperty('token');
  });

  test('returns unavailable when health check fails', async () => {
    const status = await getMitmStatus({
      readFile: async () => runtimeJson,
      fetchFn: (async () => new Response('nope', { status: 500 })) as typeof fetch,
    });
    expect(status.available).toBe(false);
    expect(status.proxy_running).toBe(false);
  });
});

describe('callMitmTool', () => {
  test('posts arguments with bearer token and returns tool json', async () => {
    let url = '';
    let auth = '';
    let body = '';
    const result = await callMitmTool(
      'traffic_list',
      { limit: 10 },
      {
        readFile: async () => runtimeJson,
        fetchFn: (async (input, init) => {
          url = String(input);
          auth = String(init?.headers && (init.headers as Record<string, string>)['Authorization']);
          body = String(init?.body);
          return new Response(
            JSON.stringify({ success: true, requests: [], returned: 0 }),
            { status: 200, headers: { 'Content-Type': 'application/json' } }
          );
        }) as typeof fetch,
      }
    );
    expect(url).toBe('http://127.0.0.1:18765/v1/tools/traffic_list');
    expect(auth).toBe('Bearer secret-token');
    expect(JSON.parse(body)).toEqual({ limit: 10 });
    expect(result.http_status).toBe(200);
    expect(result.body.success).toBe(true);
  });

  test('maps http 401 without throwing', async () => {
    const result = await callMitmTool(
      'traffic_list',
      {},
      {
        readFile: async () => runtimeJson,
        fetchFn: (async () => new Response('invalid bearer token', { status: 401 })) as typeof fetch,
      }
    );
    expect(result.http_status).toBe(401);
    expect(result.body.success).toBe(false);
  });
});
```

- [ ] **Step 3: 跑测试，确认失败**

Run（在 dreaction/app）:

```bash
cd /Users/jorim/agentspace/dreaction/app && bun test src/bun/mitm.test.ts
```

Expected: FAIL，找不到 `./mitm` 或 `getMitmStatus` 未定义。

- [ ] **Step 4: 最小实现**

创建 `app/src/bun/mitm.ts`：

```typescript
import { homedir } from 'os';
import { join } from 'path';

export type MitmStatus = {
  available: boolean;
  base_url?: string;
  proxy_running: boolean;
  proxy_port?: number;
  capture_target?: string;
};

export type MitmToolCallResult = {
  http_status: number;
  body: Record<string, unknown>;
};

export type MitmDeps = {
  runtimePath?: string;
  readFile?: (path: string) => Promise<string>;
  fetchFn?: typeof fetch;
};

type RuntimeFile = {
  base_url: string;
  token: string;
  proxy_port: number;
  capture_target: string;
};

const TIMEOUT_MS = 5000;

export function defaultRuntimePath(): string {
  return join(homedir(), '.mitmscope', 'runtime.json');
}

async function readRuntime(deps: MitmDeps): Promise<RuntimeFile | null> {
  const path = deps.runtimePath ?? defaultRuntimePath();
  const readFile = deps.readFile ?? (async (p: string) => Bun.file(p).text());
  try {
    const raw = await readFile(path);
    const data = JSON.parse(raw) as RuntimeFile;
    if (!data.base_url || !data.token) return null;
    return data;
  } catch {
    return null;
  }
}

export async function getMitmStatus(deps: MitmDeps = {}): Promise<MitmStatus> {
  const runtime = await readRuntime(deps);
  if (!runtime) {
    return { available: false, proxy_running: false };
  }
  const fetchFn = deps.fetchFn ?? fetch;
  try {
    const response = await fetchFn(`${runtime.base_url}/v1/health`, {
      headers: { Authorization: `Bearer ${runtime.token}` },
      signal: AbortSignal.timeout(TIMEOUT_MS),
    });
    if (!response.ok) {
      return { available: false, proxy_running: false };
    }
    const health = (await response.json()) as { proxy_running?: boolean };
    return {
      available: true,
      base_url: runtime.base_url,
      proxy_running: Boolean(health.proxy_running),
      proxy_port: runtime.proxy_port,
      capture_target: runtime.capture_target,
    };
  } catch {
    return { available: false, proxy_running: false };
  }
}

export async function callMitmTool(
  name: string,
  args: Record<string, unknown>,
  deps: MitmDeps = {}
): Promise<MitmToolCallResult> {
  const runtime = await readRuntime(deps);
  if (!runtime) {
    return {
      http_status: 0,
      body: { success: false, message: 'control service unavailable' },
    };
  }
  const fetchFn = deps.fetchFn ?? fetch;
  try {
    const response = await fetchFn(`${runtime.base_url}/v1/tools/${name}`, {
      method: 'POST',
      headers: {
        Authorization: `Bearer ${runtime.token}`,
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(args),
      signal: AbortSignal.timeout(TIMEOUT_MS),
    });
    let parsed: Record<string, unknown> = {
      success: false,
      message: response.statusText || 'request failed',
    };
    try {
      const json = await response.json();
      if (json && typeof json === 'object') {
        parsed = json as Record<string, unknown>;
      }
    } catch {
      parsed = { success: false, message: 'invalid json' };
    }
    return { http_status: response.status, body: parsed };
  } catch {
    return {
      http_status: 0,
      body: { success: false, message: 'timeout or network error' },
    };
  }
}
```

Authorization header 必须是普通对象（测试里按 `Record<string, string>` 读）；不要用 `Headers` 除非同步改测试。

- [ ] **Step 5: 跑测试，确认通过**

```bash
cd /Users/jorim/agentspace/dreaction/app && bun test src/bun/mitm.test.ts
```

Expected: PASS。

- [ ] **Step 6: Commit**

```bash
cd /Users/jorim/agentspace/dreaction
git add app/package.json app/src/bun/mitm.ts app/src/bun/mitm.test.ts
git commit -m "$(cat <<'EOF'
feat: add mitmproxy-control client for Desktop

Read runtime.json in the Bun process and forward tool calls without exposing the bearer token to the webview.
EOF
)"
```

---

### Task 2: 注册 RPC

**Files:**
- Modify: `app/src/shared/rpc-types.ts`
- Modify: `app/src/bun/index.ts`

**Interfaces:**
- Consumes: Task 1 的 `getMitmStatus`、`callMitmTool`、`MitmStatus`、`MitmToolCallResult`
- Produces: webview 可调用
  - `rpc.request.getMitmStatus({}) => MitmStatus`
  - `rpc.request.callMitmTool({ name: string; arguments: Record<string, unknown> }) => MitmToolCallResult`

- [ ] **Step 1: 扩展 RPC 类型**

在 `app/src/shared/rpc-types.ts` 的 `bun.requests` 里、`getInitialState` 之后追加：

```typescript
      getMitmStatus: {
        params: Record<string, never>;
        response: {
          available: boolean;
          base_url?: string;
          proxy_running: boolean;
          proxy_port?: number;
          capture_target?: string;
        };
      };
      callMitmTool: {
        params: {
          name: string;
          arguments: Record<string, unknown>;
        };
        response: {
          http_status: number;
          body: Record<string, unknown>;
        };
      };
```

不要从 bun 文件 import 类型到 `rpc-types.ts`（webview 与 bun 共享此文件，避免把 bun 模块拉进 view）。

- [ ] **Step 2: 挂 handler**

在 `app/src/bun/index.ts` 顶部增加：

```typescript
import { callMitmTool, getMitmStatus } from './mitm';
```

在 `handlers.requests` 里、`getInitialState` 旁增加：

```typescript
      getMitmStatus: async () => {
        return getMitmStatus();
      },
      callMitmTool: async ({ name, arguments: toolArgs }) => {
        return callMitmTool(name, toolArgs);
      },
```

- [ ] **Step 3: 确认类型能编过**

```bash
cd /Users/jorim/agentspace/dreaction/app && bunx tsc --noEmit --pretty false 2>&1 | head -40
```

Expected: 与本改动相关的 `rpc-types` / `index.ts` / `mitm.ts` 无新错误。若仓库原本就有无关 tsc 错误，记下但不在本任务修复。

- [ ] **Step 4: Commit**

```bash
cd /Users/jorim/agentspace/dreaction
git add app/src/shared/rpc-types.ts app/src/bun/index.ts
git commit -m "$(cat <<'EOF'
feat: expose mitm control RPCs to the Desktop webview
EOF
)"
```

---

### Task 3: 列表 drain（首屏 + 增量 offset）

**Files:**
- Create: `app/src/utils/mitm-poll.ts`
- Create: `app/src/utils/mitm-poll.test.ts`

**Interfaces:**
- Consumes: 工具 `traffic_list` 的 `{ success, requests, returned }`，其中 `requests` 为倒序摘要
- Produces:
  - `MitmTrafficSummary = { id: string; timestamp: number; method: string; url: string; domain: string; status: number; type: string; size: number; time: number; error: string | null }`
  - `drainTraffic(options): Promise<{ items: MitmTrafficSummary[]; newestId: string | null }>`
  - `options.afterId: string | null` — `null` 只拉一页（首屏，不回放全历史）
  - `options.fetchPage: (args: { after_id?: string; limit: number; offset: number }) => Promise<{ success: boolean; requests?: MitmTrafficSummary[]; returned?: number }>`
- 增量：锚点固定，`offset` 0/10/20… 直到 `returned < 10`，再按时间正序返回。禁止每页更新锚点。

- [ ] **Step 1: 写失败的测试**

创建 `app/src/utils/mitm-poll.test.ts`：

```typescript
import { describe, expect, test } from 'bun:test';
import { drainTraffic, type MitmTrafficSummary } from './mitm-poll';

function row(id: string, timestamp: number): MitmTrafficSummary {
  return {
    id,
    timestamp,
    method: 'GET',
    url: `https://example.com/${id}`,
    domain: 'example.com',
    status: 200,
    type: 'XHR',
    size: 1,
    time: 1,
    error: null,
  };
}

describe('drainTraffic', () => {
  test('first page reverses newest-first into chronological and does not paginate history', async () => {
    const calls: Array<{ after_id?: string; offset: number }> = [];
    const result = await drainTraffic({
      afterId: null,
      fetchPage: async ({ after_id, offset }) => {
        calls.push({ after_id, offset });
        return {
          success: true,
          requests: [row('c', 3), row('b', 2), row('a', 1)],
          returned: 3,
        };
      },
    });
    expect(calls).toEqual([{ after_id: undefined, offset: 0 }]);
    expect(result.items.map((item) => item.id)).toEqual(['a', 'b', 'c']);
    expect(result.newestId).toBe('c');
  });

  test('incremental keeps after_id fixed and uses offset so middle records are not skipped', async () => {
    const calls: Array<{ after_id?: string; offset: number }> = [];
    const result = await drainTraffic({
      afterId: 'anchor',
      fetchPage: async ({ after_id, offset }) => {
        calls.push({ after_id, offset });
        if (offset === 0) {
          return {
            success: true,
            requests: Array.from({ length: 10 }, (_, i) =>
              row(`n${19 - i}`, 100 + (19 - i))
            ),
            returned: 10,
          };
        }
        return {
          success: true,
          requests: Array.from({ length: 5 }, (_, i) =>
            row(`n${9 - i}`, 100 + (9 - i))
          ),
          returned: 5,
        };
      },
    });
    expect(calls).toEqual([
      { after_id: 'anchor', offset: 0 },
      { after_id: 'anchor', offset: 10 },
    ]);
    expect(result.items.map((item) => item.id)).toEqual(
      Array.from({ length: 15 }, (_, i) => `n${5 + i}`)
    );
    expect(result.newestId).toBe('n19');
  });

  test('empty incremental keeps previous newestId', async () => {
    const result = await drainTraffic({
      afterId: 'anchor',
      fetchPage: async () => ({ success: true, requests: [], returned: 0 }),
    });
    expect(result.items).toEqual([]);
    expect(result.newestId).toBe('anchor');
  });
});
```

第二例：第一页 DESC 是 n19…n10（10 条），第二页 n9…n5（5 条）。正序应为 n5…n19。`n0`–`n4` 不在结果里（本轮 API 没返回），不要编造。

- [ ] **Step 2: 跑测试，确认失败**

```bash
cd /Users/jorim/agentspace/dreaction/app && bun test src/utils/mitm-poll.test.ts
```

Expected: FAIL，找不到 `./mitm-poll`。

- [ ] **Step 3: 最小实现**

创建 `app/src/utils/mitm-poll.ts`：

```typescript
export type MitmTrafficSummary = {
  id: string;
  timestamp: number;
  method: string;
  url: string;
  domain: string;
  status: number;
  type: string;
  size: number;
  time: number;
  error: string | null;
};

export type TrafficListPage = {
  success: boolean;
  requests?: MitmTrafficSummary[];
  returned?: number;
};

const PAGE_SIZE = 10;

export async function drainTraffic(options: {
  afterId: string | null;
  fetchPage: (args: {
    after_id?: string;
    limit: number;
    offset: number;
  }) => Promise<TrafficListPage>;
}): Promise<{ items: MitmTrafficSummary[]; newestId: string | null }> {
  const collected: MitmTrafficSummary[] = [];
  const firstPageOnly = options.afterId === null;
  let offset = 0;

  while (true) {
    const page = await options.fetchPage({
      ...(options.afterId ? { after_id: options.afterId } : {}),
      limit: PAGE_SIZE,
      offset,
    });
    const rows = page.success ? page.requests ?? [] : [];
    collected.push(...rows);
    if (firstPageOnly) break;
    if (rows.length < PAGE_SIZE) break;
    offset += PAGE_SIZE;
  }

  const chronological = [...collected].sort(
    (a, b) => a.timestamp - b.timestamp || a.id.localeCompare(b.id)
  );
  const newest = chronological[chronological.length - 1];
  return {
    items: chronological,
    newestId: newest?.id ?? options.afterId,
  };
}
```

- [ ] **Step 4: 跑测试，确认通过**

```bash
cd /Users/jorim/agentspace/dreaction/app && bun test src/utils/mitm-poll.test.ts
```

Expected: PASS。

- [ ] **Step 5: Commit**

```bash
cd /Users/jorim/agentspace/dreaction
git add app/src/utils/mitm-poll.ts app/src/utils/mitm-poll.test.ts
git commit -m "$(cat <<'EOF'
feat: drain mitm traffic pages without skipping bursts

Keep after_id anchored and paginate with offset because traffic_list returns newest-first.
EOF
)"
```

---

### Task 4: Body 拼接与详情映射

**Files:**
- Create: `app/src/utils/mitm-detail.ts`
- Create: `app/src/utils/mitm-detail.test.ts`

**Interfaces:**
- Consumes: `traffic_read_body` 分片 `{ success, content, has_more, message }`；`traffic_get_detail` 的 `request` 对象
- Produces:
  - `BODY_LIMIT = 1024 * 1024`
  - `readBodyFull(requestId, field, readChunk): Promise<{ content: string; truncated: boolean; missing: boolean }>`
  - `toNetworkPair(detail, requestBody, responseBody): { request: NetworkRequest; response: NetworkResponse; duration: number }`
- `field` 只能是 `'request_body' | 'response_body'`。未知 method 用类型断言传给 `NetworkRequest`，运行时仍显示原字符串。

- [ ] **Step 1: 写失败的测试**

创建 `app/src/utils/mitm-detail.test.ts`：

```typescript
import { describe, expect, test } from 'bun:test';
import { BODY_LIMIT, readBodyFull, toNetworkPair } from './mitm-detail';

describe('readBodyFull', () => {
  test('concatenates chunks until has_more is false', async () => {
    const result = await readBodyFull('req-1', 'response_body', async ({ offset }) => {
      if (offset === 0) {
        return { success: true, content: 'hello', has_more: true };
      }
      return { success: true, content: ' world', has_more: false };
    });
    expect(result).toEqual({ content: 'hello world', truncated: false, missing: false });
  });

  test('stops at 1MB and marks truncated', async () => {
    const result = await readBodyFull('req-1', 'response_body', async () => ({
      success: true,
      content: 'x'.repeat(BODY_LIMIT),
      has_more: true,
    }));
    expect(result.content.length).toBe(BODY_LIMIT);
    expect(result.truncated).toBe(true);
    expect(result.missing).toBe(false);
  });

  test('marks missing when the tool cannot find the record', async () => {
    const result = await readBodyFull('req-1', 'request_body', async () => ({
      success: false,
      message: 'Request not found: req-1',
    }));
    expect(result.missing).toBe(true);
    expect(result.content).toBe('');
  });
});

describe('toNetworkPair', () => {
  test('maps headers and bodies onto NetworkRequest/NetworkResponse', () => {
    const pair = toNetworkPair(
      {
        url: 'https://example.com/api',
        method: 'POST',
        status: 201,
        time_ms: 42,
        request_headers: { 'Content-Type': 'application/json' },
        response_headers: { Server: 'test' },
      },
      '{"a":1}',
      '{"ok":true}'
    );
    expect(pair.request.url).toBe('https://example.com/api');
    expect(pair.request.method).toBe('POST');
    expect(pair.request.data).toBe('{"a":1}');
    expect(pair.request.headers['Content-Type']).toBe('application/json');
    expect(pair.response.status).toBe(201);
    expect(pair.response.body).toBe('{"ok":true}');
    expect(pair.duration).toBe(42);
  });

  test('keeps unknown methods as the original string at runtime', () => {
    const pair = toNetworkPair(
      { url: 'https://example.com', method: 'PROPFIND', status: 207, time_ms: 1 },
      '',
      ''
    );
    expect(pair.request.method as string).toBe('PROPFIND');
  });
});
```

- [ ] **Step 2: 跑测试，确认失败**

```bash
cd /Users/jorim/agentspace/dreaction/app && bun test src/utils/mitm-detail.test.ts
```

Expected: FAIL，找不到 `./mitm-detail`。

- [ ] **Step 3: 最小实现**

创建 `app/src/utils/mitm-detail.ts`：

```typescript
import type { NetworkMethod, NetworkRequest, NetworkResponse } from 'dreaction-protocol';

export const BODY_LIMIT = 1024 * 1024;

export type MitmDetailRequest = {
  url: string;
  method: string;
  status: number;
  time_ms: number;
  request_headers?: Record<string, string>;
  response_headers?: Record<string, string>;
};

export type BodyChunk = {
  success: boolean;
  content?: string;
  has_more?: boolean;
  message?: string;
};

export async function readBodyFull(
  requestId: string,
  field: 'request_body' | 'response_body',
  readChunk: (args: {
    request_id: string;
    field: 'request_body' | 'response_body';
    offset: number;
    length: number;
  }) => Promise<BodyChunk>
): Promise<{ content: string; truncated: boolean; missing: boolean }> {
  let content = '';
  let offset = 0;
  while (true) {
    const remaining = BODY_LIMIT - content.length;
    if (remaining <= 0) {
      return { content: content.slice(0, BODY_LIMIT), truncated: true, missing: false };
    }
    const chunk = await readChunk({
      request_id: requestId,
      field,
      offset,
      length: remaining,
    });
    if (!chunk.success) {
      const message = chunk.message ?? '';
      return {
        content,
        truncated: false,
        missing: /not found/i.test(message),
      };
    }
    content += chunk.content ?? '';
    if (content.length >= BODY_LIMIT) {
      return { content: content.slice(0, BODY_LIMIT), truncated: true, missing: false };
    }
    if (!chunk.has_more) {
      return { content, truncated: false, missing: false };
    }
    offset = content.length;
  }
}

export function toNetworkPair(
  detail: MitmDetailRequest,
  requestBody: string,
  responseBody: string
): { request: NetworkRequest; response: NetworkResponse; duration: number } {
  return {
    request: {
      url: detail.url,
      method: detail.method as NetworkMethod,
      data: requestBody,
      headers: detail.request_headers ?? {},
      params: undefined,
    },
    response: {
      status: detail.status,
      headers: detail.response_headers ?? {},
      body: responseBody,
    },
    duration: detail.time_ms,
  };
}
```

- [ ] **Step 4: 跑测试，确认通过**

```bash
cd /Users/jorim/agentspace/dreaction/app && bun test src/utils/mitm-detail.test.ts
```

Expected: PASS。

- [ ] **Step 5: Commit**

```bash
cd /Users/jorim/agentspace/dreaction
git add app/src/utils/mitm-detail.ts app/src/utils/mitm-detail.test.ts
git commit -m "$(cat <<'EOF'
feat: map mitm traffic bodies onto NetworkRequest detail
EOF
)"
```

---

### Task 5: NetworkRequestDetail 支持默认 Response 与原文 body

**Files:**
- Modify: `app/src/components/NetworkRequestDetail.tsx`

**Interfaces:**
- Consumes: 现有 `NetworkRequestDetailProps`
- Produces: 新增可选 `defaultTab?: 'summary' | 'request' | 'response'`，默认 `'summary'`（SDK Network 页不变）。Request/Response 里若 `tryToParseJSON` 仍得到 string，用 `<pre>` 而不是 JSON 树。

- [ ] **Step 1: 改组件**

把 props 扩成：

```typescript
export interface NetworkRequestDetailProps {
  request: NetworkRequest;
  response?: NetworkResponse;
  duration?: number;
  defaultTab?: 'summary' | 'request' | 'response';
}
```

`Tabs` 使用 `defaultValue={defaultTab ?? 'summary'}`。

在文件内增加：

```tsx
function BodyView({ data }: { data: unknown }) {
  const parsed = tryToParseJSON(data);
  if (typeof parsed === 'string') {
    return (
      <pre className="text-xs overflow-auto whitespace-pre-wrap break-all dark:text-gray-300">
        {parsed}
      </pre>
    );
  }
  return <JSONView data={parsed} />;
}
```

Request panel：`<BodyView data={request.data} />`。  
Response panel：有 response 时 `<BodyView data={response.body} />`，不要再把整个 response 对象塞进 JSONView。

- [ ] **Step 2: 确认 SDK Network 仍默认 summary**

`DeviceNetwork.tsx` **不要**传 `defaultTab`。

- [ ] **Step 3: Commit**

```bash
cd /Users/jorim/agentspace/dreaction
git add app/src/components/NetworkRequestDetail.tsx
git commit -m "$(cat <<'EOF'
feat: render non-JSON network bodies as preformatted text
EOF
)"
```

---

### Task 6: Mitm 页面（菜单、轮询、表、侧栏）

**Files:**
- Create: `app/src/components/DeviceMitm.tsx`
- Modify: `app/src/utils/menu.tsx`

**Interfaces:**
- Consumes: `rpc.request.getMitmStatus`、`rpc.request.callMitmTool`；`drainTraffic`；`readBodyFull`；`toNetworkPair`；`useLayoutStore().activePage`；`NetworkRequestDetail`
- Produces: 侧栏 key `'mitm'`，label `Mitm`

空状态文案（照抄）：

| 条件 | 文案 |
|------|------|
| `!status.available` | `Control service is not running. Say "开始抓包" in Cursor or run mitmproxy-control.` |
| `available && !proxy_running` | `Proxy is not running.` |
| `available && proxy_running && items.length === 0` | `Operate the app to generate traffic. You do not need to change Wi-Fi.` |
| 连续 3 次 `http_status === 401` | `Token expired. Restart the control service.` |
| 详情 `missing` | `This record no longer exists.` |

轮询：`activePage === 'mitm'` 且 `document.visibilityState === 'visible'` 时，available 则 1s，否则 3s。`proxy_running === false` 仍 1s。切走或 hidden 则停。

首屏 `afterId = null`；之后用 `newestId`。新行追加到底部；距底部 40px 内则自动滚到底。

URL 筛选：客户端 `url.toLowerCase().includes(query.trim().toLowerCase())`。

清空：`callMitmTool({ name: 'traffic_clear', arguments: {} })`，成功后清空本地列表、选中项、`afterId`。

详情：点行后立刻显示 method/status/url；并行 `traffic_get_detail` + 两个 `readBodyFull`；用递增 `generation` 丢弃过期结果。`NetworkRequestDetail` 传 `defaultTab="response"`。超过 1MB 在侧栏顶显示 truncated。工具 `success: false`（非 401）在顶栏用一行灰色小字提示，不清列表。

- [ ] **Step 1: 挂菜单**

在 `app/src/utils/menu.tsx`：

- import `IconWorldWww`（或 Tabler 里已有、且不是 `IconNetwork` 的图标）和 `DeviceMitm`
- 在 Network 项**之后**插入：

```tsx
  {
    key: 'mitm',
    icon: IconWorldWww,
    label: 'Mitm',
    component: <DeviceMitm />,
  },
```

- [ ] **Step 2: 实现 DeviceMitm**

创建 `app/src/components/DeviceMitm.tsx`。结构对齐 `DeviceNetwork.tsx`：外层 `h-full flex`，左栏顶栏+表，右栏 `w-[450px]` 详情。

要点实现（必须包含，不要改成瀑布图）：

```tsx
const POLL_FAST_MS = 1000;
const POLL_SLOW_MS = 3000;
const AUTH_FAIL_LIMIT = 3;

function statusColor(status: number): string {
  if (status < 300) return 'green';
  if (status < 400) return 'blue';
  if (status < 500) return 'yellow';
  return 'red';
}

function pathOf(url: string): string {
  try {
    return new URL(url).pathname + new URL(url).search;
  } catch {
    return url;
  }
}
```

轮询核心（写在组件里即可）：

```tsx
async function fetchPage(args: {
  after_id?: string;
  limit: number;
  offset: number;
}) {
  const result = await rpc.request.callMitmTool({
    name: 'traffic_list',
    arguments: args,
  });
  if (result.http_status === 401) {
    // 由外层计数
  }
  return result.body as {
    success: boolean;
    requests?: MitmTrafficSummary[];
    returned?: number;
    message?: string;
  };
}
```

用 `useEffect` 依赖 `activePage`：不是 `mitm` 就 return。内部 `let cancelled = false`，`setTimeout` 递归调度（不要与进行中的 drain 重叠：用 `inFlight` 锁，忙则跳过本轮）。`visibilitychange` 时同步停/启。

表格列：Method（Badge）、Status（Badge + `statusColor`）、URL（`pathOf`，`title={url}`）、Size（`formatBytes`）、Time（`Math.round(time)` + `ms`）。

ESC 关闭侧栏（仅 `activePage === 'mitm'` 时监听，抄 DeviceNetwork）。

详情加载：

```tsx
const detail = await rpc.request.callMitmTool({
  name: 'traffic_get_detail',
  arguments: { request_id: id },
});
const detailRequest = (detail.body as { request?: MitmDetailRequest }).request;
```

`readChunk` 包一层 `traffic_read_body`。`generation` 在 click 时 `++`，await 后若过期则 return。

顶栏展示：`available` 时显示 `:{proxy_port}`、`capture_target`、条数；圆点颜色 running 绿 / 否则灰。`TextInput` 筛选。`ActionIcon` + `IconTrash` 清空。

- [ ] **Step 3: 跑全部 bun 测试，确认未破坏**

```bash
cd /Users/jorim/agentspace/dreaction/app && bun test src
```

Expected: Task 1/3/4 的测试仍 PASS。

- [ ] **Step 4: Commit**

```bash
cd /Users/jorim/agentspace/dreaction
git add app/src/components/DeviceMitm.tsx app/src/utils/menu.tsx
git commit -m "$(cat <<'EOF'
feat: add Mitm traffic preview page to Desktop

Poll mitmproxy-control for captured requests and show formatted JSON in the existing detail pane.
EOF
)"
```

---

### Task 7: 手测对照规格验收

**Files:** 无代码。工作目录 dreaction + 本机已装的 mitmproxy-control。

**Interfaces:** 无。对照 spec 第 8 节。

- [ ] **Step 1: 未起控制服务**

停掉 `mitmproxy-control`（或暂时移走 `~/.mitmscope/runtime.json`）。打开 Desktop Mitm 页。

Expected: 「Control service is not running…」

- [ ] **Step 2: 方案 A 抓包后看顶栏**

Cursor 里开始抓包（`proxy_start` + reverse）。刷新/等待 Mitm 页。

Expected: 顶栏出现端口（默认 `:8888`）且代理在跑。

- [ ] **Step 3: JSON 接口**

App 打一个 JSON API。约 1s 内列表出现。点开。

Expected: 默认 Response；JSON 树可展开；字段与 MCP `traffic_read_body` 一致。

- [ ] **Step 4: 非 JSON**

若方便，打一个 HTML/text 响应（或 mock）。

Expected: `<pre>` 原文，页面不崩。

- [ ] **Step 5: 切页再回来**

切到 Home 再回 Mitm。再打一个新接口。

Expected: 旧行还在，只追加新行，不整表闪成空再填。

- [ ] **Step 6: 清空**

点垃圾桶。再打一个接口。

Expected: 先空，随后新请求仍出现。

- [ ] **Step 7: 重启控制服务**

停控制服务，看到空状态；再拉起后继续抓。

Expected: 自动恢复（新 token），不必重启 Desktop。

- [ ] **Step 8: SDK Network 回归**

打开原 Network 页，让 RN/JS 发一个 XHR。

Expected: 行为与改前一致；默认 Tab 仍是 Summary。

- [ ] **Step 9: mitmproxy-mcp 测试未动**

```bash
cd /Users/jorim/agentspace/mitmproxy-mcp && uv run pytest -q
```

Expected: 绿。本计划不应出现该仓库的代码 diff。文档交叉引用若已在此前写入，保持即可。

---

## Self-review

1. **Spec coverage:** 架构 / RPC / 倒序+offset drain / 1MB body / JSON vs pre / 空状态 / 401 / 清空 / 不泄漏 token / 不改 MCP 工具 — 均有对应 Task。方案 B 字段、SSE、SwiftUI、从 Desktop 启代理 — 明确不在任务里。
2. **Placeholders:** 无 TBD；测试与实现代码写全。Task 6 的 JSX 以要点+必含片段给出（整页 300+ 行抄 DeviceNetwork 骨架），实现者按 `DeviceNetwork.tsx` 补布局 class，不得改数据流。
3. **Types:** `MitmStatus` / `MitmToolCallResult` / `MitmTrafficSummary` / `drainTraffic` / `readBodyFull` / `toNetworkPair` 在后续任务中的名字与 Task 1/3/4 一致。
