# MITM Proxy MCP vs Charles / Proxyman 对比文档

## 一、工具定位对比

| 维度 | Charles / Proxyman | MITM Proxy MCP |
|------|-------------------|------------------|
| **本质** | 独立的 GUI 抓包工具 | 集成在 IDE 中的 AI 驱动抓包 + Mock 服务 |
| **交互方式** | 鼠标点击、菜单操作 | 自然语言对话（"帮我找登录接口"） |
| **数据消费者** | 人眼看 | AI 分析 + 人看 |
| **工作流** | 抓包 → 切到 Charles 看 → 切回 IDE 改代码 | 全程在 Cursor 内完成，不切窗口 |
| **Mock 方式** | GUI 配置 Map Local / Map Remote | 自然语言描述，AI 自动创建规则 |
| **价格** | Charles $50，Proxyman $49-69/年 | 免费开源 |

---

## 二、核心优势

### 1. 不用离开 IDE

传统流程：
```
写代码 → 切到 Charles → 找接口 → 看数据 → 切回 IDE → 改代码 → 切到 Charles 验证
```

MCP 流程：
```
写代码 → 对话框里说"帮我找 XX 接口的返回" → AI 直接给你数据 → 继续写代码
```

**窗口切换次数从 6+ 次降到 0 次。**

### 2. AI 理解你的意图

| 你说的话 | Charles 需要你做的 | MCP 自动做的 |
|---------|-------------------|-------------|
| "找持仓列表接口" | 自己在几百条请求里用眼找 | `traffic_search` 搜索，直接返回匹配的接口 |
| "这个接口返回了什么" | 点开请求 → 点 Response → 复制 JSON | `traffic_read_body` 读取，AI 直接分析 |
| "把 vipLevel mock 成 5" | Map Local → 新建规则 → 编辑 JSON → 保存 | 一句话，AI 自动找到接口、读取原始响应、修改字段、创建规则 |
| "对比接口返回和 Model 字段" | 手动复制 JSON → 和代码逐个对比 | AI 自动读取两端数据，输出对比表格 |

### 3. Mock 效率差异巨大

**Charles 创建一个 Mock 的步骤（Map Local）：**
1. 找到目标请求
2. 右键 → Map Local
3. 选择或创建本地 JSON 文件
4. 用编辑器打开 JSON 文件
5. 修改目标字段
6. 保存
7. 刷新验证

**MCP 创建一个 Mock 的步骤：**
1. 说"把 /api/user/info 的 vipLevel mock 成 5"
2. 完成

**更极端的场景——选中 Model 字段 mock：**

MCP 可以做到你选中一个 Dart Model 的字段，说"mock 成 1"，AI 会：
- 自动分析 Model 类名和 JSON key
- 搜索代码找到哪些 API 返回这个 Model
- 读取这些 API 的实际响应
- 只修改对应字段
- 给所有相关接口创建 mock 规则

Charles / Proxyman 要实现同样的效果，你需要**自己**完成上面每一步。

### 4. 与代码上下文打通

MCP 工具运行在 Cursor 内部，AI 同时能看到：
- 你的代码（Model、Repository、API 定义）
- 抓到的流量数据
- 两者之间的关系

这意味着 AI 可以做 Charles / Proxyman 永远做不到的事：
- "这个接口返回了哪些字段是 Model 里没有的？"
- "帮我根据这个接口的返回生成 Dart Model"
- "对比线上返回和 Model 的字段差异"

### 5. 团队协作与自动化

- Mock 规则存在 SQLite 中，可以通过脚本导入导出
- Skill 文件（SKILL.md）可以放在项目仓库里，团队成员 clone 即可使用
- 标准化的抓包/Mock 工作流，减少"我这里能复现你那里不行"的问题

---

## 三、劣势与局限

| 维度 | 说明 | Charles / Proxyman 的优势 |
|------|------|--------------------------|
| **GUI 可视化** | MCP 没有流量瀑布图、时间线视图 | Charles 的请求列表、响应预览、JSON 树形视图非常直观 |
| **实时性** | MCP 需要主动查询，不能像 Charles 那样实时刷新 | Charles / Proxyman 实时展示每一条请求 |
| **高级抓包功能** | 不支持 WebSocket、gRPC 调试、SSL Pinning bypass | Charles / Proxyman 有更完整的协议支持 |
| **断点调试** | 不支持请求断点（拦截请求并手动修改再放行） | Charles 支持 Breakpoints 功能 |
| **带宽限速** | 不支持模拟弱网 | Charles 有 Throttle 功能 |
| **请求重发** | 不支持 Compose / Repeat 请求 | Charles 可以修改并重发请求 |
| **独立运行** | 依赖 Cursor IDE | Charles / Proxyman 是独立应用 |
| **学习曲线** | 需要了解 MCP、Cursor Skill 概念 | Charles / Proxyman 打开即用 |
| **稳定性** | AI 理解可能有偏差（比如 mock 理解成改代码） | GUI 操作确定性更高 |

### 适用场景建议

| 场景 | 推荐工具 |
|------|---------|
| 日常开发中快速查看接口返回 | **MCP**（不用切窗口） |
| Mock 接口数据做 UI 调试 | **MCP**（自然语言创建规则） |
| Model 字段与接口对比 | **MCP**（AI 自动分析） |
| 复杂网络问题排查（SSL、WebSocket） | **Charles / Proxyman** |
| 模拟弱网、限速测试 | **Charles** |
| 修改请求并重发（API 调试） | **Charles / Proxyman** |
| 给非开发人员演示接口数据 | **Charles / Proxyman**（GUI 更直观） |
| 团队标准化 Mock 流程 | **MCP**（Skill + 规则可版本管理） |

---

## 四、使用教程

### 前置准备

```bash
# 1. 安装项目
cd mitmproxy-mcp && uv sync

# 2. 配置 Cursor MCP（Cursor Settings → MCP → 添加）
#    command: uv
#    args: --directory /path/to/mitmproxy-mcp run mitmproxy-mcp

# 3. 安装 Skill（自动复制到 ~/.cursor/skills/）
bash install-skill.sh
```

### 基础用法

#### 启动代理

在 Cursor 对话框中说：

> "启动代理"

或者在终端手动启动：

```bash
uv run mitmproxy-start --setup-proxy
```

#### 查看流量

> "显示最近的请求"
> "搜索 URL 包含 /api/v1/user 的请求"
> "读取 req-3 的响应体"

#### 创建 Mock

> "把 /api/v1/user/info 的返回 mock 成 {"code": 0, "data": {"vip": 5}}"

#### 管理 Mock

> "列出所有 mock 规则"
> "关闭第一条 mock 规则"
> "清空所有 mock"

#### 停止代理

> "停止代理"

### 证书安装（HTTPS 抓包必须）

**iOS 模拟器：**
1. 模拟器 Safari 访问 `http://mitm.it`
2. 下载 Apple 证书
3. 设置 → 通用 → VPN与设备管理 → 安装
4. 设置 → 通用 → 关于本机 → 证书信任设置 → 启用

**Mac 浏览器：**
1. 浏览器访问 `http://mitm.it` → 下载证书
2. 双击安装到钥匙串
3. 钥匙串中找到 mitmproxy → 信任 → "始终信任"

---

## 五、效率提升技巧

### 技巧 1：选中 Model 字段直接 Mock

选中 Dart Model 中的某个属性，说：

> "mock 成 1"

AI 会自动：找到对应 API → 读取原始响应 → 只改这个字段 → 创建 mock 规则。

### 技巧 2：批量 Mock 边界情况

一句话覆盖多个测试场景：

> "把 /api/v1/order/list 的 list 字段分别 mock 成空数组、只有 1 条数据、100 条数据"

### 技巧 3：快速复制线上数据

先抓包拿到真实数据，再基于真实数据微调：

> "搜索 /api/v1/product/detail 的响应"
> "把 price 改成 0，其他不变，mock 这个接口"

好处是 mock 的数据结构和线上完全一致，不会因为手动构造 JSON 漏字段。

### 技巧 4：接口对比发现问题

> "对比 /api/v1/user/info 的返回和 UserInfoModel 的字段"

AI 会输出对比表格，告诉你哪些字段接口返回了但 Model 没定义，避免漏字段。

### 技巧 5：Mock 异常状态码

测试错误处理逻辑：

> "把 /api/v1/payment/create mock 成 500，返回 {"code": -1, "message": "server error"}"

### 技巧 6：模拟延迟

测试 loading 状态的 UI 表现：

> "把 /api/v1/user/info mock 成正常返回，但是延迟 3 秒"

对应参数 `delay_ms: 3000`。

### 技巧 7：快速开关 Mock

调试完一个场景后不删除规则，而是暂时禁用：

> "关闭所有 mock"（mock_toggle 逐个禁用）

需要时再：

> "打开所有 mock"

保留规则方便反复测试。

### 技巧 8：结合抓包 + Mock 做回归测试

1. 抓包记录正常流程的所有接口返回
2. 逐个 mock 成异常值，验证 App 行为
3. 验证完恢复，测下一个

这比在代码里写 `if (debug)` 判断干净得多，且不会污染代码。

---

## 六、总结

**MITM Proxy MCP 不是要替代 Charles / Proxyman，而是补充它们在 AI 开发工作流中的不足。**

- 需要**快速查看/Mock 接口数据**且不想离开 IDE → 用 MCP
- 需要**深度网络调试**（断点、重发、限速、WebSocket） → 用 Charles / Proxyman
- 两者可以同时使用，MCP 代理和 Charles 代理可以配置不同端口
