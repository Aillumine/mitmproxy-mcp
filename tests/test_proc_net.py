from mitm_proxy_mcp.android.proc_net import parse_local_ports

SAMPLE = """  sl  local_address rem_address   st tx_queue rx_queue tr tm->when retrnsmt   uid  timeout inode
   0: 0100007F:1F90 00000000:0000 0A 00000000:00000000 00:00000000 00000000 10234        0 123456 1 0000000000000000 100 0 0 10 0
   1: 0A00020F:D431 8EFB2D22:01BB 01 00000000:00000000 00:00000000 00000000 10234        0 123457 1 0000000000000000 20 4 30 10 -1
   2: 0A00020F:D432 8EFB2D22:01BB 01 00000000:00000000 00:00000000 00000000 10666        0 123458 1 0000000000000000 20 4 30 10 -1
"""


def test_parses_all_but_listen_local_ports():
    """Every state but LISTEN (0A) counts; only LISTEN is excluded.

    样本第 0 行是 uid 10234 监听 8080 的 LISTEN（0A）连接：那是服务端口，
    可能和别的应用连代理时用的临时源端口撞号，撞上就会归属错人，所以必须排除。
    """
    assert parse_local_ports(SAMPLE) == {54321, 54322}
    assert 8080 not in parse_local_ports(SAMPLE, uid=10234)


def test_preserves_recently_finished_connections():
    """FIN_WAIT (04/05) and CLOSE_WAIT (08) still carry the real uid of the
    connection that just finished — excluding only LISTEN must not drop them.

    FIN_WAIT（04/05）、CLOSE_WAIT（08）仍然带着刚结束的那条连接的真实 uid——
    只排除 LISTEN 就不该把它们也丢掉。
    """
    finishing = (
        "  sl  local_address rem_address st tx rx tr tm retr uid\n"
        "   0: 0A00020F:D431 8EFB2D22:01BB 04 00000000:00000000"
        " 00:00000000 00000000 10234 0 1 1\n"
        "   1: 0A00020F:D432 8EFB2D22:01BB 05 00000000:00000000"
        " 00:00000000 00000000 10234 0 1 1\n"
        "   2: 0A00020F:D433 8EFB2D22:01BB 08 00000000:00000000"
        " 00:00000000 00000000 10234 0 1 1\n"
    )
    assert parse_local_ports(finishing, uid=10234) == {54321, 54322, 54323}


def test_filters_by_uid():
    """root 路径下一次能读到全设备的连接，必须按 uid 收窄到目标应用。"""
    assert parse_local_ports(SAMPLE, uid=10234) == {54321}
    assert parse_local_ports(SAMPLE, uid=10666) == {54322}


def test_ignores_header_and_garbage():
    assert parse_local_ports("") == set()
    assert parse_local_ports("not a table at all") == set()
    assert parse_local_ports("  sl  local_address\n  bad line here\n") == set()


def test_time_wait_rows_are_excluded_by_uid():
    """实测：已关闭的连接会残留一行、状态是 TIME_WAIT(06)、uid 归 0。

    TIME_WAIT is no longer caught by the state filter — only LISTEN is now —
    so it depends entirely on the uid filter, and only when a target uid is
    given; the real caller (PackageAttributor) always passes one.

    TIME_WAIT 不再被状态过滤挡住——现在只排除 LISTEN——所以完全靠 uid 过滤
    挡住它，而且要传目标 uid 才行；真实调用方（PackageAttributor）总是会传。
    """
    time_wait = (
        "  sl  local_address rem_address st tx rx tr tm retr uid\n"
        "   0: 7A6EA8C0:CA7A FE6EA8C0:4D41 06 00000000:00000000"
        " 03:0000090F 00000000 0 0 0 3\n"
    )
    assert parse_local_ports(time_wait, uid=10234) == set()
    # No uid filter: state alone no longer excludes TIME_WAIT.
    #
    # 不传 uid 时，单靠状态过滤已经不会排除 TIME_WAIT 了。
    assert parse_local_ports(time_wait) == {51834}


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
