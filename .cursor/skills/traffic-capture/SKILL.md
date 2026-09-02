---
name: traffic-capture
description: 使用 mitmproxy MCP 抓包分析 HTTP/HTTPS 流量，mock 接口数据。支持启动/停止代理、adb reverse 免手填 Wi‑Fi、搜索流量、查看请求详情和响应体、对比接口返回与 Model 字段差异、添加/管理 mock 规则、选中 Model 字段自动关联接口并 mock。触发词：抓包、抓接口、proxy、流量分析、接口对比、网络请求分析、traffic、抓取接口数据、自动配代理、免手填、reverse、mock、mock数据、mock接口、模拟数据、模拟接口、mock返回、拦截接口、mitmproxy、mock字段、mock这个字段、改成、模拟返回、dreaction
---

# 流量抓包、分析与 Mock

通过 `user-mitmproxy` MCP 服务抓取和分析 HTTP/HTTPS 流量，以及 mock 接口返回数据。

## ⚠️ 核心约束（必须遵守）

**当用户说"mock"时，永远是指通过代理拦截来 mock 接口返回的 JSON 数据，绝不是修改源代码。**

- 禁止修改 Model / Repository / Provider 等任何源代码文件来实现 mock
- 禁止在代码中添加硬编码值、默认值、测试数据
- 唯一的 mock 方式：调用 `mock_add` MCP 工具，通过代理在网络层拦截并替换接口响应
- 如果用户选中了一个 Model 字段说"mock 成 X"，意思是：找到返回这个 Model 的 API 接口，在代理层拦截该接口的响应 JSON，把对应字段改成 X

## MCP Server

- server: `user-mitmproxy`
- 所有工具通过 `CallMcpTool` 调用，server 固定为 `user-mitmproxy`

## 核心工具速查

### 代理管理

| 工具 | 用途 | 关键参数 |
|------|------|----------|
| `proxy_start` | 启动代理 | `port`(默认8888), `setup_proxy`(是否设Mac系统代理) |
| `proxy_stop` | 停止代理 | `port`(默认8888) |
| `proxy_status` | 查看代理状态 | 无 |
| `throttle_get` | 查看弱网档位 | 无 |
| `throttle_set` | 设置弱网（立即生效） | `profile`: `off` / `4g` / `3g` / `2g` |

### 流量查看

| 工具 | 用途 | 关键参数 |
|------|------|----------|
| `traffic_list` | 列出最近流量 | `limit`(最大10), `offset`, `filter_domain`, `filter_url`(支持正则), `filter_type`, `filter_status`, `start_time`(Unix时间戳), `end_time`(Unix时间戳) |
| `traffic_search` | 搜索流量内容 | `keyword`(必填), `search_in`(url/request_body/response_body/all等), `method`, `domain`, `limit` |
| `traffic_get_detail` | 获取请求元数据 | `request_id`(必填，从list/search获取) |
| `traffic_read_body` | 读取请求体/响应体 | `request_id`(必填), `field`(request_body/response_body), `offset`, `length`(默认4000) |
| `traffic_clear` | 清空所有流量 | 无 |

### Mock 数据

| 工具 | 用途 | 关键参数 |
|------|------|----------|
| `mock_add` | 添加 mock 规则 | `name`(必填), `url_pattern`(必填), `response_body`(必填), `method`, `match_type`(contains/exact/regex), `status_code`, `response_headers`, `delay_ms`, `enabled` |
| `mock_list` | 列出所有 mock 规则 | `enabled_only` |
| `mock_update` | 更新 mock 规则 | `rule_id`(必填), 以及需要更新的字段 |
| `mock_delete` | 删除 mock 规则 | `rule_id`(必填) |
| `mock_toggle` | 启用/禁用 mock 规则 | `rule_id`(必填), `enabled` |
| `mock_clear` | 清空所有 mock 规则 | 无 |
| `mock_export` | 导出所有规则为 JSON | 无 |
| `mock_import` | 从 JSON 导入规则 | `rules_json`(必填), `merge`(默认false=先清空) |

### 设备管理

| 工具 | 用途 |
|------|------|
| `ios_list_simulators` | 列出 iOS 模拟器 |
| `ios_list_real_devices` | 列出 iOS 真机 |
| `ios_list_devices` | 列出所有 iOS 设备 |
| `ios_boot_simulator` | 启动模拟器 |
| `ios_shutdown_simulator` | 关闭模拟器 |
| `ios_get_device_info` | 获取 iOS 设备信息 |
| `android_list_devices` | 列出 Android 设备 |
| `android_setup_proxy` | 设置 Android 全局代理为 MacIP:port（需同网） |
| `android_clear_proxy` | 清除 Android 全局代理 |
| `android_get_proxy` | 读取设备当前 `http_proxy` |
| `android_reverse_proxy` | **推荐**：adb reverse + `127.0.0.1:port`（免手填 Wi‑Fi） |
| `android_reverse_proxy_remove` | 拆除 reverse 并清除设备代理 |
| `android_get_device_info` | 获取 Android 设备信息 |
| `get_cert_info` | 获取 CA 证书安装指南 |

## 开始抓包：触发条件与接入决策

详细说明见 [`docs/proxy-access-and-dreaction-sync.md`](../../../docs/proxy-access-and-dreaction-sync.md)。

### 话术 → 动作（必须遵守）

| 用户说法（触发） | 动作 |
|------------------|------|
| 「开始抓包 / 抓接口 / 启动代理 / 自动配代理 / 免手填 Wi‑Fi」且目标是 Android 或未声明只抓 Mac | **方案 A**：`proxy_start` → `android_list_devices` → `android_reverse_proxy`（**不要** `setup_proxy=true`） |
| 「用 Wi‑Fi 代理 / 没插 USB / 不要 reverse」 | `proxy_start` → `android_setup_proxy(serial, MacIP, port)` 或口头指导手动填 Wi‑Fi |
| 「也抓电脑 / 开系统代理 / 抓 Mac 浏览器」 | 仅非真机时 `proxy_start(setup_proxy=true)`；真机禁止 |
| 「停止抓包」 | 若本次设过 reverse → `android_reverse_proxy_remove` → `proxy_stop` |
| 「和 dreaction 同步」 | 说明 App 内 host 是调试桥 9600 不是 mitm；今天用方案 A；Desktop 同步属方案 B（未落地） |
| reverse 已配仍无流量（已排证书/包名） | 可能 App 忽略系统代理 → 评估方案 C，勿反复改 Wi‑Fi |

**优先级：** A（reverse）→ 无 adb 再 Wi‑Fi → 明确要求才开 Mac 系统代理 → 系统代理无效才考虑 C。

### 方案 A 详细步骤（默认）

```
1. proxy_status；未运行则 proxy_start(port=8888)（setup_proxy 默认 false）
2. android_list_devices → 取 serial（多台时用用户指定或第一台并告知）
3. android_reverse_proxy(serial, port=8888)   # 或与 status 中端口一致
4. android_get_proxy(serial) 自检，期望 127.0.0.1:8888
5. 告知用户：无需改手机 Wi‑Fi，直接操作 App
6. 首次 HTTPS：get_cert_info / android_cert_status，按需装证
7. 用户操作后 traffic_list / traffic_search 验证
```

### 方案 B / C（产品扩展，非默认执行）

- **B**：`~/.mitmscope/runtime.json` 为真相源，dreaction Desktop 只读展示 / 一键同步 —— 触发：需要 GUI 与 MCP 状态对齐
- **C**：SDK Custom Command `applyHttpProxy` 写入 OkHttp Proxy —— 触发：A 已成功但目标 App 仍抓不到（忽略全局 http_proxy）

Agent **不要**把 dreaction ConfigDialog 的 host 当成 mitm 代理去读。

## 常用工作流

### 1. 抓取指定接口数据

```
步骤：
1. proxy_status → 确认代理运行中
2. traffic_search(keyword="接口路径关键词", search_in=["url"]) → 找到 request_id
3. traffic_read_body(request_id="req-xxx", field="response_body") → 读取响应 JSON
   - 如果 has_more=true，增加 offset 继续读取
```

### 2. 接口返回与 Model 对比

```
步骤：
1. 用 traffic_search 搜索目标接口
2. 用 traffic_read_body 获取完整响应 JSON
3. 读取对应的 Dart Model 文件
4. 逐字段对比：接口返回的 JSON key vs Model 的 @JsonKey(name: 'xxx')
5. 输出对比表格：字段名 | Model是否有 | 类型 | 备注
```

### 3. 首次 / 开始抓包（替代「手填 Wi‑Fi」）

默认走上方 **方案 A**。仅当无 adb 时改用 `android_setup_proxy` 或让用户手填 `MacIP:8888`。

### 4. Mock 接口数据

用户说"mock XX 接口返回 YY"时：

```
步骤：
1. 从用户描述中提取：URL 特征、期望返回的数据
2. 构造 response_body（JSON 字符串）
3. mock_add(name="描述", url_pattern="URL特征", response_body=JSON字符串)
4. 告知用户规则已生效，匹配的请求会直接返回 mock 数据
```

**match_type 选择指引：**
- `contains`（默认）：URL 包含指定字符串即匹配，支持 `*` 通配符。适合大多数场景。
- `exact`：完全精确匹配整个 URL
- `regex`：正则表达式匹配，适合复杂 URL 模式

**典型示例：**

```
用户: "把 /api/v1/user/info 接口的返回 mock 成 {code: 0, data: {name: "test"}}"
→ mock_add(
    name="mock user info",
    url_pattern="/api/v1/user/info",
    response_body='{"code": 0, "data": {"name": "test"}}',
    match_type="contains"
  )

用户: "把这个接口返回的 list 改成空数组"
→ 先用 traffic_search 找到接口 URL
→ 用 traffic_read_body 读取原始响应
→ 修改响应中的 list 字段为 []
→ mock_add(name="empty list", url_pattern="接口URL特征", response_body=修改后的JSON)

用户: "关闭 mock"
→ mock_list() 查看规则
→ mock_toggle(rule_id="xxx", enabled=false) 或 mock_delete(rule_id="xxx")
```

### 5. 从抓包数据创建 Mock

当用户要基于已抓到的接口数据来修改并 mock 时：

```
步骤：
1. traffic_search(keyword="接口路径") → 找到 request_id
2. traffic_read_body(request_id="req-xxx") → 读取原始响应
3. 根据用户需求修改响应 JSON
4. mock_add(name="描述", url_pattern="接口路径", response_body=修改后的JSON)
```

### 6. 选中 Model 字段 → 自动 Mock 对应接口

用户选中一个 Model 的字段（如 Dart Model 的属性），要求 mock 该字段的返回值时：

```
步骤：

Phase 1 — 解析 Model 信息
1. 读取用户选中的字段所在的 Model 文件
2. 提取关键信息：
   - Model 类名（如 UserInfoModel）
   - 选中字段的 JSON key（优先看 @JsonKey(name: 'xxx')，否则用变量名）
   - 字段类型（用于生成合理的 mock 值）

Phase 2 — 查找关联接口
3. 在代码中搜索该 Model 被哪些 API 使用：
   - 搜索 Repository/Provider/Api 文件中引用了该 Model 类名的位置
   - 从 Retrofit 注解（@GET/@POST）或方法签名提取接口路径
   - 记录所有关联的接口路径列表
4. 如果代码中找不到，用 traffic_search 在已抓流量中搜索该字段的 JSON key：
   - traffic_search(keyword="字段jsonKey", search_in=["response_body"])
   - 从匹配结果的 URL 中提取接口路径

Phase 3 — 决策：mock 哪些接口
5. 判断逻辑：
   - 用户明确指定了接口 → 只 mock 该接口
   - 只找到 1 个接口 → mock 该接口
   - 找到多个接口 → 全部 mock（每个接口各创建一条规则）
   - 找不到接口 → 告知用户未找到关联接口，请确认代理已抓到该接口的流量，或手动指定接口路径

Phase 4 — 构造 Mock 数据
6. 对每个需要 mock 的接口：
   a. traffic_search(keyword="接口路径", search_in=["url"]) → 找到 request_id
   b. traffic_read_body(request_id) → 读取原始完整响应 JSON
   c. 在原始 JSON 中只修改用户指定的字段值，保持其他字段不变
   d. mock_add(
        name="mock {Model类名}.{字段名}",
        url_pattern="接口路径特征",
        response_body=修改后的完整JSON
      )
7. 汇报：列出创建的 mock 规则，说明影响了哪些接口
```

**关键原则：**
- 只改用户指定的字段，其余字段保持原始响应不变
- 多接口对应同一 Model 时，默认全部 mock，除非用户指定了某一个
- 如果流量中没有该接口的数据，提醒用户先操作 App 触发该接口再来 mock

**示例：**

```
用户选中 UserInfoModel 的 `vipLevel` 字段，说"mock 成 5"

Phase 1: 读取 Model → UserInfoModel, @JsonKey(name: 'vip_level'), int 类型
Phase 2: 搜索代码 → 发现 UserApi.getUserInfo() 和 UserApi.getAccountDetail() 都返回 UserInfoModel
         对应路径: /api/v1/user/info 和 /api/v1/account/detail
Phase 3: 用户没指定接口 → 两个都 mock
Phase 4: 分别读取两个接口的原始响应，把 vip_level 改为 5，创建两条 mock 规则
```

## 注意事项

- `traffic_read_body` 默认只读 4000 字符，大响应需要用 `offset` 分页读取
- `traffic_search` 的 `search_in` 是数组，如 `["url"]`、`["response_body"]`、`["url", "response_body"]`
- `traffic_list` 的 `filter_url` 支持正则表达式
- 如果代理未运行，先用 `proxy_start` 启动，或提醒用户在终端执行 `proxy`（`uv run mitmproxy-control --proxy`）
- Mock 规则实时生效，代理运行中无需重启
- 被 mock 的请求仍会记录到流量中，响应头带 `X-Mock-Rule` 标识
- Mock 规则按 `updated_at` 倒序匹配，最新更新的规则优先
