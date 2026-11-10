"""Device-facing addresses and durable interface preference.

设备连接地址与持久保存的网卡偏好。
"""

from __future__ import annotations

import ipaddress
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from mitm_proxy_mcp.control.paths import mitmscope_dir


class AddressPreference(BaseModel):
    interface: str | None = None


def _command(args: list[str]) -> str:
    try:
        return subprocess.run(
            args,
            capture_output=True,
            text=True,
            check=True,
            timeout=2,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def discover_addresses() -> list[dict[str, str]]:
    # The default internet route can use USB Ethernet while the phone uses Wi-Fi.
    # Enumerate physical LAN interfaces instead; never change routing or proxies.
    #
    # 旧实现将默认上网出口当作手机连接地址，双网卡下会选到 USB 网线。
    # 改为枚举物理局域网网卡，不修改系统路由或代理设置。
    candidates: list[dict[str, str]] = []
    if sys.platform == "darwin":
        ports = _command(["/usr/sbin/networksetup", "-listallhardwareports"])
        labels = dict(
            (device, label.strip())
            for label, device in re.findall(
                r"Hardware Port: ([^\n]+)\nDevice: (\S+)",
                ports,
            )
            if device.startswith("en")
        )
        output = _command(["/sbin/ifconfig"])
        for block in re.split(r"(?=^\w[^\n]*: flags=)", output, flags=re.M):
            interface = block.split(":", 1)[0]
            if interface not in labels or "status: active" not in block:
                continue
            for host in re.findall(r"\binet (\d+\.\d+\.\d+\.\d+)", block):
                label = labels[interface]
                kind = "wifi" if label.lower() in ("wi-fi", "airport") else "ethernet"
                candidates.append(
                    dict(interface=interface, label=label, host=host, kind=kind)
                )
    elif sys.platform.startswith("linux"):
        try:
            interfaces = json.loads(
                _command(["ip", "-j", "-4", "address", "show", "up"]) or "[]"
            )
            for item in interfaces:
                interface = item["ifname"]
                if not (Path("/sys/class/net") / interface / "device").exists():
                    continue
                kind = (
                    "wifi"
                    if (Path("/sys/class/net") / interface / "wireless").exists()
                    else "ethernet"
                )
                for address in item.get("addr_info", []):
                    if address.get("scope") == "global":
                        candidates.append(
                            dict(
                                interface=interface,
                                label=interface,
                                host=address["local"],
                                kind=kind,
                            )
                        )
        except (ValueError, KeyError, TypeError):
            return []
    return [item for item in candidates if _usable_ipv4(item["host"])]


def _usable_ipv4(host: str) -> bool:
    ip = ipaddress.ip_address(host)
    return not (ip.is_loopback or ip.is_link_local or ip.is_unspecified)


def address_state() -> dict[str, Any]:
    addresses = discover_addresses()
    path = mitmscope_dir() / "proxy-address.json"
    try:
        preference = AddressPreference.model_validate_json(path.read_text())
    except FileNotFoundError:
        preference = AddressPreference()
    selected = preference.interface
    # Persist the interface, not its DHCP address. A missing explicit selection
    # must stay unavailable until the user chooses again, avoiding silent rerouting.
    #
    # 保存网卡身份而非 DHCP 地址；手动选定的网卡断开后显式提示重选，
    # 避免悄悄切换到手机无法访问的另一张网卡。
    active = next((item for item in addresses if item["interface"] == selected), None)
    if selected is None:
        active = next((item for item in addresses if item["kind"] == "wifi"), None)
        active = active or next(iter(addresses), None)
    warning = None
    if active is None:
        warning = (
            f"所选网卡 {selected} 已断开，请重新选择连接地址"
            if selected
            else "未找到可用的局域网地址"
        )
    return dict(
        addresses=addresses,
        selected_interface=selected,
        active_interface=active["interface"] if active else None,
        host=active["host"] if active else None,
        warning=warning,
    )


def save_preference(value: AddressPreference) -> None:
    directory = mitmscope_dir()
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    # Replace atomically so a failed save keeps the previous interface preference.
    #
    # 原子替换保证写入失败时仍保留上一次网卡选择。
    fd, name = tempfile.mkstemp(prefix=".proxy-address-", dir=directory)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(value.model_dump(), stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, directory / "proxy-address.json")
    finally:
        Path(name).unlink(missing_ok=True)
