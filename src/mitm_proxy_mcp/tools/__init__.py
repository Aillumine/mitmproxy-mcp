"""MCP 工具模块"""

from .proxy_tools import get_cert_info, proxy_start, proxy_stop
from .traffic_tools import (
    traffic_list,
    traffic_get_detail,
    traffic_clear,
    traffic_search,
    traffic_read_body,
    proxy_status,
)
from .mock_tools import (
    mock_add,
    mock_list,
    mock_update,
    mock_delete,
    mock_toggle,
    mock_clear,
)
from .android_tools import (
    android_list_devices,
    android_get_device_info,
    android_setup_proxy,
    android_clear_proxy,
)
from .ios_tools import (
    ios_list_devices,
    ios_list_simulators,
    ios_list_real_devices,
    ios_get_device_info,
    ios_boot_simulator,
    ios_shutdown_simulator,
)

__all__ = [
    # Proxy tools
    "get_cert_info",
    "proxy_status",
    "proxy_start",
    "proxy_stop",
    # Traffic tools
    "traffic_list",
    "traffic_get_detail",
    "traffic_search",
    "traffic_read_body",
    "traffic_clear",
    # Mock tools
    "mock_add",
    "mock_list",
    "mock_update",
    "mock_delete",
    "mock_toggle",
    "mock_clear",
    # Android tools
    "android_list_devices",
    "android_get_device_info",
    "android_setup_proxy",
    "android_clear_proxy",
    # iOS tools
    "ios_list_devices",
    "ios_list_simulators",
    "ios_list_real_devices",
    "ios_get_device_info",
    "ios_boot_simulator",
    "ios_shutdown_simulator",
]
