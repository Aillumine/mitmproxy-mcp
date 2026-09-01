# 代理接入方式与 DReaction 同步

本文说明两件事：

1. **设备流量怎么进 mitm**（接入层：reverse / Wi‑Fi / Mac 系统代理）
2. **要不要和 dreaction 打通**（产品层：方案 A / B / C）

> 重要澄清：dreaction App 里的 host/port 是调试桥（默认 `ws://host:9600`），**不是**抓包 HTTP 代理。当前 App **没有**「读出来就能用的 mitm 代理设置」。
>
> **实时预览 MCP 抓到的 JSON** 用本机网页（`mitmproxy-control` 打印的 `UI: http://127.0.0.1:{port}/`），不是 dreaction Mitm 页，也不是方案 B。B 只同步代理状态。

---

## 1. 决策总览

```
用户要抓包
    │
    ├─ 目标是 Android 真机/模拟器，且 adb 可用？
    │     └─ 是 → 【接入默认】方案 A：android_reverse_proxy（免手填 Wi‑Fi）
    │
    ├─ 无 USB / 无 adb / 必须无线同网？
    │     └─ 是 → 【接入兜底】android_setup_proxy 或手动 Wi‑Fi 填 MacIP:port
    │
    ├─ 只要抓 Mac 本机 / iOS 模拟器，且用户明确说「开系统代理」？
    │     └─ 是 → proxy_start(setup_proxy=true)
    │
    ├─ 只要在浏览器里实时预览抓到的接口 JSON？
    │     └─ 是 → 做完 A 后打开本机网页（`uv run mitmproxy-control`，访问打印的 UI URL；不是 dreaction Mitm 页，也不是 B）
    │
    ├─ 只要在 dreaction Desktop 里看到当前代理状态 / 一键同步？
    │     └─ 是 → 在 A 之上加方案 B（共享 ~/.mitmscope/runtime.json）
    │
    └─ 系统 http_proxy 抓不到（App 自建 OkHttp、忽略全局代理）？
          └─ 是 → 再上方案 C（App 内 Proxy Plugin + Custom Command）
```

**优先级口诀：** 先 A 解决问题 → 要看包用本机网页 → 需要代理状态/一键同步再 B → 系统代理无效才 C。

---

## 2. 接入层：三种把流量送进 mitm 的方式

这三者和方案 A/B/C 不是同一维度。A/B/C 讨论的是「和 dreaction 怎么同步」；下面是「手机/模拟器怎么连上 8888」。

| 接入方式 | 工具/操作 | 是否手填 Wi‑Fi | 依赖 |
|----------|-----------|----------------|------|
| adb reverse（推荐） | `android_reverse_proxy` | 否 | USB/无线 adb |
| 设备全局代理指 Mac IP | `android_setup_proxy` 或手动 Wi‑Fi | 是（或 MCP 代写） | 同局域网 |
| Mac 系统代理 | `proxy_start(setup_proxy=true)` | 否（改 Mac） | 仅本机/模拟器；**真机禁止** |

### 2.1 触发条件（接入层）

| 条件 | 选用 |
|------|------|
| 用户说「开始抓包 / 抓 Android / 抓真机」，且 `android_list_devices` 有设备 | **adb reverse** |
| 用户明确说「不要 reverse / 用 Wi‑Fi 代理 / 无线抓包且无 adb」 | **setup_proxy 指 Mac IP** 或告知手动填 |
| 用户明确说「也抓电脑 / 开 Mac 系统代理 / 抓本机浏览器」 | **`setup_proxy=true`** |
| 已判定 `capture_target=device`（真机） | **禁止** `setup_proxy=true` |
| reverse 已配但 App 仍无流量 | 先查证书 / 是否走系统代理；再考虑方案 C |

### 2.2 详细步骤：adb reverse（默认推荐）

1. `proxy_status` → 未运行则 `proxy_start(port=8888)`（**不要**传 `setup_proxy=true`）
2. `android_list_devices` → 取 `serial`
3. `android_get_proxy(serial)` → 若已是 `127.0.0.1:8888` 可跳过写入
4. `android_reverse_proxy(serial, port=8888)`
5. 首次 HTTPS：`get_cert_info` / `android_cert_status`，按需装证
6. 让用户在 App 里操作触发请求
7. `traffic_list` / `traffic_search` 确认有流量
8. 结束：`android_reverse_proxy_remove(serial)`（可选）+ `proxy_stop`

### 2.3 详细步骤：设备代理指 Mac IP（无 adb / 必须同网）

1. `proxy_start(port=8888)`（`setup_proxy=false`）
2. 确认 Mac 局域网 IP（如 `192.168.x.x`）
3. 有 adb：`android_setup_proxy(serial, proxy_host=MacIP, proxy_port=8888)`  
   无 adb：告知用户在手机 Wi‑Fi → 代理 → 手动填 `MacIP:8888`
4. 证书与抓包确认同 2.2 的 5–7
5. 结束：`android_clear_proxy(serial)` 或用户手动关 Wi‑Fi 代理 + `proxy_stop`

### 2.4 详细步骤：Mac 系统代理（仅本机/模拟器）

**触发口令必须明确**，例如：「开系统代理」「也抓电脑」「抓 Mac 浏览器」。

1. 确认不是真机目标（真机会被强制否决）
2. `proxy_start(port=8888, setup_proxy=true)`
3. iOS 模拟器通常跟随系统代理；否则模拟器 Wi‑Fi 填 `127.0.0.1:8888`
4. 需要 HTTPS 时本机/模拟器安装 CA（`get_cert_info`）
5. 结束：`proxy_stop`（会恢复系统代理）

---

## 3. 产品层：方案 A / B / C（与 dreaction 的关系）

| 方案 | 一句话 | 改哪里 | 和 dreaction 关系 |
|------|--------|--------|-------------------|
| **A** | MCP 启动后自动 reverse，免手填 Wi‑Fi | 仅 MCP 工作流 / Skill | **无关**，也能达成主目标 |
| **B** | Desktop 与 MCP 共享代理状态 | dreaction Desktop + `runtime.json` | UI/状态同步 |
| **C** | App 进程内强制走 HTTP Proxy | dreaction SDK + 业务 OkHttp | 真正下发到 App |

### 3.1 方案 A — MCP「启动即 reverse」

#### 触发条件（满足任一即走 A）

- 用户说「开始抓包」「抓接口」「启动代理」且语境是 Android / 未指定只抓 Mac
- 用户说「不要每次填 Wi‑Fi / 自动配代理 / 免手动设端口」
- `android_list_devices` 返回至少一台 `device`/`emulator`
- 日常 Cursor 内抓包（默认路径）

#### 不触发 / 改走其他

- 无 adb 设备 → 改 2.3
- 用户只要抓 Mac → 问是否开系统代理，走 2.4
- 用户只要「和 dreaction 面板同步」→ A 做完后再做 B
- reverse 成功但仍无流量，且确认 App 忽略系统代理 → 评估 C

#### 详细步骤

1. `proxy_status`；未运行则 `proxy_start(port=8888)`（`setup_proxy` 默认 false）
2. `android_list_devices`
   - 0 台 → 告知连接设备，或改 Wi‑Fi 方案
   - 多台 → 优先用户指定的 serial；否则用第一台并告知
3. `android_reverse_proxy(serial, port=从 status/runtime 读取的端口，默认 8888)`
4. `android_get_proxy(serial)` 自检，期望 `127.0.0.1:<port>`
5. 告知用户：USB 已接好即可操作 App，**无需**改手机 Wi‑Fi
6. 用户操作后 `traffic_search` / `traffic_list` 验证
7. 停止抓包时：`android_reverse_proxy_remove`（若本次由你设置）→ `proxy_stop`

#### 现状

**今天即可落地**（工具已齐）。Skill / 规则应把「开始抓包」默认绑到本流程。

---

### 3.2 方案 B — dreaction Desktop Proxy 同步面板

#### 触发条件（要做 B 的产品/开发诉求）

- 需要在 dreaction 桌面端看到「当前 mitm 端口 / 设备代理是否已配」
- 希望非 Cursor 用户也能在 Desktop 点「同步代理」
- 要把 MCP 与 GUI 的状态对齐到同一真相源

#### 不单独用 B 的情况

- 只想免手填 Wi‑Fi → 做 A 即可，不必等 B
- App 不走系统代理 → B 解决不了，需要 C

#### 详细步骤（实现契约）

1. **真相源**：`~/.mitmscope/runtime.json`（MCP / control 已在维护）  
   建议扩展字段示例：

   ```json
   {
     "proxy_port": 8888,
     "capture_target": "device",
     "device_proxy": {
       "mode": "adb_reverse",
       "host": "127.0.0.1",
       "port": 8888,
       "serial": "XXXX"
     }
   }
   ```

2. **MCP 侧**（写）  
   - `proxy_start` 后写入 `proxy_port`  
   - `android_reverse_proxy` / `android_setup_proxy` 成功后写入 `device_proxy`  
   - `android_reverse_proxy_remove` / `android_clear_proxy` / `proxy_stop` 时清理或标记 disabled

3. **dreaction Desktop 侧**（读 + 可选触发）  
   - 增加 Proxy 面板：展示 port / serial / mode  
   - 「一键同步」：读 `runtime.json` 的 port，调用与 MCP 相同的 adb reverse 逻辑（或提示用户去 Cursor 执行 A）  
   - 不把 dreaction 的 9600 调试端口当成 mitm 端口

4. **验收**  
   - Cursor 跑完 A 后，打开 dreaction Desktop 能看到同一 port  
   - Desktop 刷新/一键后与 `android_get_proxy` 一致

#### 现状

**设计约定**；Desktop 一键 reverse / `device_proxy` 字段尚未实现。落地前继续用 A。

流量列表 + JSON 预览走 **本机网页**（`mitmproxy-control` 打印的 UI URL，与 MCP 共用 `~/.mitmscope/`），不再使用 dreaction Desktop Mitm 页。不要把 B 扩成抓包界面。

---

### 3.3 方案 C — SDK `applyHttpProxy`（App 内代理）

#### 触发条件（必须同时大致满足）

- 已按 A（或 Wi‑Fi）配好系统代理，**仍抓不到**目标 App 流量
- 已排除：证书未装、代理未生效、看错包名、流量走了非 HTTP
- 目标 App 使用自建 `OkHttpClient` / 自定义网络栈，**忽略** `Settings.Global.HTTP_PROXY`
- 业务愿意接入 dreaction Android SDK，并允许重建带 `Proxy` 的 Client

#### 不触发 C

- 系统代理已能抓到 → 停在 A（或 B）
- 仅 RN/JS 层、无原生 OkHttp 控制点 → C 收益有限，优先系统代理 / 原生模块
- 用户只是不想填 Wi‑Fi → 用 A，不要上 C

#### 详细步骤（实现契约）

1. **SDK 存储**  
   - 如 SharedPreferences / AsyncStorage：`__dreaction_http_proxy = { enabled, host, port }`

2. **Android 应用代理**  
   - `OkHttpClient.Builder().proxy(Proxy(HTTP, InetSocketAddress(host, port)))`  
   - 与现有 `DReactionInterceptor` 挂在同一 Client（或提供 factory）

3. **协议**  
   - Desktop → App：Custom Command `applyHttpProxy`（args: host, port, enabled）  
   - App → Desktop：`proxy.status` 上报当前配置  
   - 挂载点：`CustomCommandPlugin` / `registerCustomCommand`

4. **与 MCP 联动（推荐方向：MCP 写 → App 应用）**  
   - MCP `proxy_start` 写入 `runtime.json`  
   - （可选）Desktop 读到后 `sendCommandToClient("custom", { command: "applyHttpProxy", ... })`  
   - 真机 host 优先 `127.0.0.1` + 已做 `adb reverse`；无线场景用 Mac 局域网 IP

5. **验收**  
   - 关闭系统 `http_proxy` 后，仅靠 App 内 Proxy 仍能在 mitm 看到业务请求  
   - 关闭 `applyHttpProxy` 后流量不再进 mitm

#### 现状

**未实现**。仅当 A 证实无效时再开。

---

## 4. Agent 话术 → 动作速查

| 用户说法 | 动作 |
|----------|------|
| 开始抓包 / 抓 Android / 自动配代理 | A：`proxy_start` → `android_reverse_proxy` |
| 不要填 Wi‑Fi / 免手动端口 | 同 A |
| 用 Wi‑Fi 代理 / 没插 USB | 2.3：`android_setup_proxy` 或口头指导 |
| 也抓电脑 / 开系统代理 | 2.4：仅非真机时 `setup_proxy=true` |
| 停止抓包 | reverse_remove（若本次设置）→ `proxy_stop` |
| 和 dreaction 同步 / Desktop 显示代理 | 说明现状用 A；B 为后续能力 |
| 在浏览器里看抓包 / JSON 预览 | 说明走本机网页（`http://127.0.0.1:18765/`，端口以 runtime 为准）；不是 dreaction Mitm 页，也不是 B |
| 系统代理开了还是抓不到 | 排障证书/包名后，评估 C |

---

## 5. 相关文件

| 路径 | 说明 |
|------|------|
| `.cursor/skills/traffic-capture/SKILL.md` | Agent 抓包 Skill（执行层） |
| `.cursor/rules/proxy.mdc` | 仓库内 Agent 规则 |
| `src/mitm_proxy_mcp/tools/android_tools.py` | `android_reverse_proxy` 等实现 |
| `~/.mitmscope/runtime.json` | 控制服务运行时（B 与本机网页的发现入口） |
| `README.md` | 本机网页：`cd web && npm run build` 后 `uv run mitmproxy-control` |
| dreaction `CustomCommandPlugin` | C 的命令下发挂载点 |
