"""MCP 工具模块"""

from .android_tools import (
    android_cert_status,
    android_clear_proxy,
    android_get_device_info,
    android_get_proxy,
    android_list_devices,
    android_setup_proxy,
)
from .ios_tools import (
    ios_boot_simulator,
    ios_get_device_info,
    ios_list_devices,
    ios_list_real_devices,
    ios_list_simulators,
    ios_shutdown_simulator,
)
from .mock_tools import (
    mock_add,
    mock_clear,
    mock_delete,
    mock_export,
    mock_import,
    mock_list,
    mock_toggle,
    mock_update,
)
from .proxy_tools import get_cert_info, proxy_start, proxy_status, proxy_stop
from .traffic_tools import (
    traffic_clear,
    traffic_get_detail,
    traffic_list,
    traffic_read_body,
    traffic_search,
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
    "mock_export",
    "mock_import",
    # Android tools
    "android_list_devices",
    "android_get_device_info",
    "android_setup_proxy",
    "android_clear_proxy",
    "android_get_proxy",
    "android_cert_status",
    # iOS tools
    "ios_list_devices",
    "ios_list_simulators",
    "ios_list_real_devices",
    "ios_get_device_info",
    "ios_boot_simulator",
    "ios_shutdown_simulator",
]
