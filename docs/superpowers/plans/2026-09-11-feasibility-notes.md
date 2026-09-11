# Task 0 可行性验证记录

**设备：** Pixel 7（`2A221FDH20087C`），Android 17
**日期：** 2026-09-11

## 结论

| 假设 | 结果 |
|---|---|
| `run-as <pkg> cat /proc/net/tcp` 能读到该应用的连接 | ❌ **不成立**，SELinux 拒绝 |
| `adb shell` 能读全量 socket 表并看到每条连接的真实 uid | ✅ **成立**，且不需要 root |
| mitmproxy 看到的对端端口 == 设备 `/proc/net/tcp` 的本地端口 | ✅ **成立** |
| `pm list packages -3 -U` 输出格式为 `package:xxx uid:NNN` | ✅ 与方案一致 |

**净结果：方案的核心机制成立，但实现路径要换，而且换完能力更强** —— 不再需要 root、不再需要 debuggable，任意应用都能精确归属。

## 原始输出

### 1. run-as 路径作废

```
$ adb shell "run-as com.flow.mobile.debug true; echo EXIT=$?"
EXIT=0                                   # run-as 本身可用（该包确实 debuggable）

$ adb shell "run-as com.flow.mobile.debug cat /proc/net/tcp6"
cat: /proc/net/tcp6: Permission denied

$ adb shell "run-as com.flow.mobile.debug cat /proc/net/tcp"
cat: /proc/net/tcp: Permission denied
```

`run-as` 切到了应用的 uid，但也继承了 `untrusted_app` 的 SELinux 域，而该域没有读 `proc_net_tcp` 的权限。原方案「限制反而是实现手段」的推理对了一半：uid 隔离确实存在，但在 Android 11+ 上 SELinux 在 uid 检查之前就把访问挡掉了。

### 2. adb shell 能读全量表，且带真实 uid

```
$ adb shell id
uid=2000(shell) gid=2000(shell) groups=2000(shell),...,3009(readproc),...

$ adb shell cat /proc/net/tcp6 | head -4
  sl  local_address    remote_address   st tx_queue rx_queue tr tm->when retrnsmt   uid  timeout inode
   0: ...0100007F:A4E9 ...0000:0000 0A 00000000:00000000 00:00000000 00000000 10197  0 18038187 ...
   1: ...0100007F:804F ...0000:0000 0A 00000000:00000000 00:00000000 00000000 10203  0 24253434 ...
   2: ...0000:ABC1     ...0000:0000 0A 00000000:00000000 00:00000000 00000000 10133  0 24304761 ...
```

uid 列是 **10197 / 10203 / 10133**，即各个应用自己的 uid，不是 shell 的 2000。`shell` 用户在 `readproc`(3009) 组里，这是它能看到全量 `/proc/net` 的原因。

**这是比 run-as 更好的路径：一条命令拿到全设备所有应用的连接，按 uid 过滤即可归属到任意应用。**

### 3. 端口一致性成立

Mac 侧监听 19777 并 accept，手机侧同时读 socket 表：

```
# 设备侧 /proc/net/tcp
   1: 7A6EA8C0:DE40 FE6EA8C0:4D41 05 ... 2000 ...
      ↑本地 192.168.110.122:56896  ↑远端 192.168.110.254:19777   ↑uid=2000(shell 发起)

# Mac 侧
PEER_PORT=56896 HEX=DE40
```

`DE40 = 56896`，两侧完全一致。设备与 Mac 同网段直连，中间无 NAT。

### 4. 其他实测细节

- **IPv4 连接落在 `/proc/net/tcp`，不在 `tcp6`。** toybox nc 的连接只出现在 `/proc/net/tcp` 里，而应用的连接多数在 `tcp6`（IPv4-mapped 形式）。**两张表都必须读。**
- **TIME_WAIT 行的 uid 是 0。** 同次采样里上一条已关闭的连接显示为 `... :CA7A ... 06 ... 0 ...`（状态 06 = TIME_WAIT，uid 归 0）。按目标 uid 过滤时这类行会被自然排除，不需要额外处理。
- **前台窗口可能不是应用。** 实测 `mCurrentFocus=Window{946be20 u0 NotificationShade}` —— 通知栏没有 `包名/Activity` 形式，`parse_foreground_package` 的正则要求斜杠，此时正确返回 `None`。
- **`pm list packages -3 -U`** 输出 `package:com.flow.mobile.debug uid:10512`，与方案的解析假设一致。

## 对方案的影响

见 plan 文档「Task 0 之后的方案修订」一节。
