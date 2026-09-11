"""Parse Android's /proc/net/tcp{,6} into a set of local ports.

解析 Android /proc/net/tcp{,6}，取出本地端口集合。
"""

# TCP state as printed in column 3; 01 is ESTABLISHED. Only established sockets
# are real outbound connections to the proxy — a LISTEN (0A) socket's port is a
# server port that may collide with another app's ephemeral source port, which
# would attribute that app's traffic to this one.
#
# 第 3 列是 TCP 状态，01 表示 ESTABLISHED。只有已建立的连接才是真正连到代理的
# 请求——LISTEN（0A）的端口是服务端口，可能和别的应用的临时源端口撞号，
# 撞上就会把别人的流量算到这个应用头上。
_ESTABLISHED = "01"


def parse_local_ports(text: str, uid: int | None = None) -> set[int]:
    """Local ports held by ESTABLISHED TCP sockets in a /proc/net/tcp{,6} dump.

    Column 1 is `local_address` as `HEXIP:HEXPORT` (8 hex chars for IPv4, 32 for
    IPv6); column 3 is the TCP state and column 7 the owning uid. Malformed lines
    are skipped rather than raised on — this parses whatever a phone happened to
    print, and one odd row must not take down the sampler.

    从 /proc/net/tcp{,6} 的内容里取出所有处于 ESTABLISHED 状态的本地端口。

    第 1 列 local_address 形如 `十六进制IP:十六进制端口`（IPv4 是 8 位、
    IPv6 是 32 位），第 3 列是 TCP 状态，第 7 列是所属 uid。解析不了的行直接
    跳过而不是抛异常——这是在解析手机随手打印出来的东西，一行异常不能把整个
    采样器带下去。
    """
    ports: set[int] = set()
    for line in text.splitlines():
        fields = line.split()
        if len(fields) < 8:
            continue
        if fields[3] != _ESTABLISHED:
            continue
        local = fields[1]
        if ":" not in local:
            continue
        try:
            port = int(local.rsplit(":", 1)[1], 16)
            row_uid = int(fields[7])
        except ValueError:
            continue
        if uid is not None and row_uid != uid:
            continue
        ports.add(port)
    return ports
