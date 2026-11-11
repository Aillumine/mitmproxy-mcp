from fastapi.testclient import TestClient

from mitm_proxy_mcp.control.app import create_app

HEADERS = {"Authorization": "Bearer secret"}


def test_address_selection_tracks_interface_across_dhcp_and_disconnect(
    tmp_path, monkeypatch
):
    from mitm_proxy_mcp.control import proxy_addresses

    monkeypatch.setattr(proxy_addresses, "mitmscope_dir", lambda: tmp_path)
    addresses = [
        {
            "interface": "en9",
            "label": "USB Ethernet",
            "host": "10.110.10.15",
            "kind": "ethernet",
        },
        {
            "interface": "en0",
            "label": "Wi-Fi",
            "host": "192.168.111.180",
            "kind": "wifi",
        },
    ]
    monkeypatch.setattr(proxy_addresses, "discover_addresses", lambda: addresses)
    client = TestClient(create_app("secret"))
    response = client.get("/v1/ui/proxy-address", headers=HEADERS)
    assert response.status_code == 200
    assert response.json()["host"] == "192.168.111.180"
    assert (
        client.get("/v1/health", headers=HEADERS).json()["proxy_host"]
        == "192.168.111.180"
    )
    response = client.put(
        "/v1/ui/proxy-address", headers=HEADERS, json={"interface": "en9"}
    )
    assert response.status_code == 200
    assert response.json()["host"] == "10.110.10.15"
    addresses[0]["host"] = "10.110.10.16"
    restarted = TestClient(create_app("secret"))
    assert (
        restarted.get("/v1/ui/proxy-address", headers=HEADERS).json()["host"]
        == "10.110.10.16"
    )
    addresses.pop(0)
    disconnected = restarted.get("/v1/ui/proxy-address", headers=HEADERS).json()
    assert disconnected["host"] is None
    assert disconnected["selected_interface"] == "en9"
    assert disconnected["warning"]
    assert (
        restarted.get("/v1/health", headers=HEADERS).json()["proxy_host"] == "127.0.0.1"
    )
    automatic = restarted.put(
        "/v1/ui/proxy-address", headers=HEADERS, json={"interface": None}
    )
    assert automatic.json()["host"] == "192.168.111.180"


def test_address_api_rejects_unauthorized_and_inactive_selection(tmp_path, monkeypatch):
    from mitm_proxy_mcp.control import proxy_addresses

    monkeypatch.setattr(proxy_addresses, "mitmscope_dir", lambda: tmp_path)
    monkeypatch.setattr(proxy_addresses, "discover_addresses", lambda: [])
    client = TestClient(create_app("secret"))
    assert client.get("/v1/ui/proxy-address").status_code == 401
    assert (
        client.put("/v1/ui/proxy-address", json={"interface": "en9"}).status_code == 401
    )
    assert (
        client.put(
            "/v1/ui/proxy-address", headers=HEADERS, json={"interface": "en9"}
        ).status_code
        == 400
    )
    data = client.get("/v1/ui/proxy-address", headers=HEADERS).json()
    assert data["host"] is None
    assert data["warning"]


def test_address_picker_is_available_to_authenticated_ui():
    client = TestClient(create_app("secret"))
    assert client.get("/v1/ui/proxy-address", headers=HEADERS).status_code == 200


def test_macos_discovery_uses_hardware_labels_and_active_lan_addresses(monkeypatch):
    from mitm_proxy_mcp.control import proxy_addresses

    monkeypatch.setattr(proxy_addresses.sys, "platform", "darwin")
    outputs = {
        "/usr/sbin/networksetup": """Hardware Port: AX88179B
Device: en9
Ethernet Address: aa:bb:cc:dd:ee:ff

Hardware Port: Wi-Fi
Device: en7
Ethernet Address: aa:bb:cc:dd:ee:00

Hardware Port: Ethernet Adapter
Device: en8
""",
        "/sbin/ifconfig": """lo0: flags=8049<UP,LOOPBACK,RUNNING>
    inet 127.0.0.1 netmask 0xff000000
utun0: flags=8051<UP,POINTOPOINT,RUNNING>
    inet 10.8.0.2 netmask 0xffffff00
    status: active
en9: flags=8863<UP,BROADCAST,RUNNING>
    inet 10.110.10.15 netmask 0xffffff00
    status: active
en7: flags=8863<UP,BROADCAST,RUNNING>
    inet 192.168.111.180 netmask 0xfffffe00
    status: active
en8: flags=8863<UP,BROADCAST,RUNNING>
    inet 169.254.1.2 netmask 0xffff0000
    status: inactive
""",
    }
    monkeypatch.setattr(proxy_addresses, "_command", lambda args: outputs[args[0]])
    assert proxy_addresses.discover_addresses() == [
        {
            "interface": "en9",
            "label": "AX88179B",
            "host": "10.110.10.15",
            "kind": "ethernet",
        },
        {
            "interface": "en7",
            "label": "Wi-Fi",
            "host": "192.168.111.180",
            "kind": "wifi",
        },
    ]


def test_failed_preference_save_preserves_last_selection(tmp_path, monkeypatch):
    from mitm_proxy_mcp.control import proxy_addresses

    monkeypatch.setattr(proxy_addresses, "mitmscope_dir", lambda: tmp_path)
    monkeypatch.setattr(
        proxy_addresses,
        "discover_addresses",
        lambda: [
            {
                "interface": "en0",
                "label": "Wi-Fi",
                "host": "192.168.1.2",
                "kind": "wifi",
            },
        ],
    )
    proxy_addresses.save_preference(proxy_addresses.AddressPreference(interface="en0"))

    def fail_replace(*args):
        raise OSError("disk full")

    monkeypatch.setattr(proxy_addresses.os, "replace", fail_replace)
    client = TestClient(create_app("secret"))
    assert (
        client.put(
            "/v1/ui/proxy-address", headers=HEADERS, json={"interface": None}
        ).status_code
        == 500
    )
    assert (
        client.get("/v1/ui/proxy-address", headers=HEADERS).json()["selected_interface"]
        == "en0"
    )
    assert not list(tmp_path.glob(".proxy-address-*"))
