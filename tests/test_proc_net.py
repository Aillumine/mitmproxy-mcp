from mitm_proxy_mcp.android.proc_net import parse_local_ports

SAMPLE = """  sl  local_address rem_address   st tx_queue rx_queue tr tm->when retrnsmt   uid  timeout inode
   0: 0100007F:1F90 00000000:0000 0A 00000000:00000000 00:00000000 00000000 10234        0 123456 1 0000000000000000 100 0 0 10 0
   1: 0A00020F:D431 8EFB2D22:01BB 01 00000000:00000000 00:00000000 00000000 10234        0 123457 1 0000000000000000 20 4 30 10 -1
   2: 0A00020F:D432 8EFB2D22:01BB 01 00000000:00000000 00:00000000 00000000 10666        0 123458 1 0000000000000000 20 4 30 10 -1
"""


def test_parses_only_established_local_ports():
    """只取 ESTABLISHED（01）的行。

    样本第 0 行是 uid 10234 监听 8080 的 LISTEN（0A）连接：那是服务端口，
    可能和别的应用连代理时用的临时源端口撞号，撞上就会归属错人，所以必须排除。
    """
    assert parse_local_ports(SAMPLE) == {54321, 54322}
    assert 8080 not in parse_local_ports(SAMPLE, uid=10234)


def test_filters_by_uid():
    """root 路径下一次能读到全设备的连接，必须按 uid 收窄到目标应用。"""
    assert parse_local_ports(SAMPLE, uid=10234) == {54321}
    assert parse_local_ports(SAMPLE, uid=10666) == {54322}


def test_ignores_header_and_garbage():
    assert parse_local_ports("") == set()
    assert parse_local_ports("not a table at all") == set()
    assert parse_local_ports("  sl  local_address\n  bad line here\n") == set()


def test_time_wait_rows_are_excluded():
    """实测：已关闭的连接会残留一行、状态是 TIME_WAIT(06)、uid 归 0。

    状态过滤和 uid 过滤各能挡住它一次，两道都要验。
    """
    time_wait = (
        "  sl  local_address rem_address st tx rx tr tm retr uid\n"
        "   0: 7A6EA8C0:CA7A FE6EA8C0:4D41 06 00000000:00000000"
        " 03:0000090F 00000000 0 0 0 3\n"
    )
    assert parse_local_ports(time_wait) == set()
    assert parse_local_ports(time_wait, uid=10234) == set()


def test_handles_ipv6_rows():
    """tcp6 的地址字段是 32 位十六进制，端口仍在冒号之后。"""
    ipv6 = (
        "  sl  local_address                         remote_address"
        "                        st tx_queue rx_queue tr tm->when retrnsmt   uid\n"
        "   0: 00000000000000000000000000000000:C1B4"
        " 00000000000000000000000000000000:0000 01 00000000:00000000"
        " 00:00000000 00000000 10234 0 1 1\n"
    )
    assert parse_local_ports(ipv6) == {49588}
