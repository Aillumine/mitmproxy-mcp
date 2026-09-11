"""把抓到的请求按源端口归属到设备上的某个应用。"""

import asyncio
import sqlite3
import time
from pathlib import Path

from loguru import logger

from mitm_proxy_mcp.android.proc_net import parse_local_ports

# 采样间隔与回填窗口。窗口不能太长：系统会复用端口，超过窗口的老记录再认领
# 就可能把别的应用的请求算到自己头上。
#
# Sampling interval and backfill window. The window must stay short: the OS
# reuses ports, so claiming rows older than this risks stealing another app's.
SAMPLE_INTERVAL_SECONDS = 1.0
BACKFILL_WINDOW_SECONDS = 30.0


def sample_command() -> str:
    """Shell command that dumps every TCP socket on the device, with uids.

    `adb shell` runs as uid 2000, which sits in the readproc group and may read
    the full /proc/net tables — each row carrying the owning app's real uid. So
    one plain cat covers every app: no root, and no run-as (which Android's
    SELinux policy denies outright, verified on Android 17).

    Both tables matter: plain IPv4 connections land in /proc/net/tcp while app
    traffic mostly shows up in tcp6 as IPv4-mapped rows.

    返回导出设备上全部 TCP 连接（含 uid）的 shell 命令。

    `adb shell` 以 uid 2000 运行，它在 readproc 组里，可以读完整的 /proc/net 表，
    每一行都带着所属应用的真实 uid。所以一条普通的 cat 就覆盖了所有应用：
    既不需要 root，也不需要 run-as（后者被 Android 的 SELinux 策略直接拒绝，
    已在 Android 17 上实测）。

    两张表都要读：纯 IPv4 连接落在 /proc/net/tcp，而应用流量多数以
    IPv4-mapped 形式出现在 tcp6 里。
    """
    return "cat /proc/net/tcp /proc/net/tcp6"


def backfill_packages(
    conn: sqlite3.Connection,
    package: str,
    ports: set[int],
    now: float,
    window: float = BACKFILL_WINDOW_SECONDS,
) -> int:
    """Tag recent rows whose client_port belongs to this app. Returns row count.

    Only rows still unattributed are touched — first writer wins, so a later
    sample of another app can never relabel traffic.

    把最近窗口内、源端口属于该应用的记录打上包名，返回更新行数。

    只改还没归属的行——先到先得，后来的采样不会把已归属的流量改名。
    """
    if not ports:
        return 0
    placeholders = ",".join("?" for _ in ports)
    cursor = conn.execute(
        f"UPDATE traffic SET package = ?"
        f" WHERE package IS NULL AND timestamp >= ?"
        f" AND client_port IN ({placeholders})",
        (package, now - window, *sorted(ports)),
    )
    conn.commit()
    return cursor.rowcount


class PackageAttributor:
    """轮询目标应用的 TCP 连接，把流量记录回填成它的包名。

    Runs as a single asyncio task: one app is being attributed at a time, which
    matches the UI (one selected package). Sampling failures are recorded and
    swallowed — a phone that goes offline must not take the control service with
    it.

    以单个 asyncio 任务运行：同一时刻只归属一个应用，与界面上「选中一个包名」
    一致。采样失败只记录不抛出——手机掉线不能把控制服务一起带走。
    """

    def __init__(
        self,
        adb,
        serial: str,
        package: str,
        uid: int,
        db_path: Path,
    ) -> None:
        self.adb = adb
        self.serial = serial
        self.package = package
        self.uid = uid
        self.db_path = Path(db_path)
        self._task: asyncio.Task | None = None
        self._samples = 0
        self._attributed = 0
        self._last_error: str | None = None

    async def sample_once(self) -> int:
        """跑一轮采样并回填，返回本轮归属到的记录数。"""
        command = sample_command()
        try:
            code, output = await self.adb.shell(self.serial, command, timeout=10.0)
        except Exception as error:  # adb 掉线、超时、设备重启都归到这里
            self._last_error = str(error)
            return 0

        if code != 0:
            self._last_error = output.strip()[:200]
            return 0

        # 表里是全设备的连接，必须按目标应用的 uid 收窄。
        #
        # The dump covers every app on the device, so narrowing by the target
        # uid is what makes the result belong to this package.
        ports = parse_local_ports(output, uid=self.uid)
        self._samples += 1
        self._last_error = None
        if not ports:
            return 0

        conn = sqlite3.connect(str(self.db_path), timeout=10)
        try:
            updated = backfill_packages(conn, self.package, ports, time.time())
        finally:
            conn.close()
        self._attributed += updated
        return updated

    async def _loop(self) -> None:
        while True:
            await self.sample_once()
            await asyncio.sleep(SAMPLE_INTERVAL_SECONDS)

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._loop())
            logger.info(f"开始归属 {self.package} 的流量（uid={self.uid}）")

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None
        logger.info(f"停止归属 {self.package} 的流量")

    def status(self) -> dict:
        return {
            "package": self.package,
            "uid": self.uid,
            "running": self._task is not None and not self._task.done(),
            "samples": self._samples,
            "attributed": self._attributed,
            "last_error": self._last_error,
        }
