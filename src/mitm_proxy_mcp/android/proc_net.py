"""解析 Android /proc/net/tcp{,6}，取出本地端口集合。"""


def parse_local_ports(text: str, uid: int | None = None) -> set[int]:
    """Local ports held by TCP sockets in a /proc/net/tcp{,6} dump.

    Column 1 is `local_address` as `HEXIP:HEXPORT` (8 hex chars for IPv4, 32 for
    IPv6); column 7 is the owning uid. Malformed lines are skipped rather than
    raised on — this parses whatever a phone happened to print, and one odd row
    must not take down the sampler.

    从 /proc/net/tcp{,6} 的内容里取出所有本地端口。

    第 1 列 local_address 形如 `十六进制IP:十六进制端口`（IPv4 是 8 位、
    IPv6 是 32 位），第 7 列是所属 uid。解析不了的行直接跳过而不是抛异常——
    这是在解析手机随手打印出来的东西，一行异常不能把整个采样器带下去。
    """
    ports: set[int] = set()
    for line in text.splitlines():
        fields = line.split()
        if len(fields) < 8:
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
