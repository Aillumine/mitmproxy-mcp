from fastapi.testclient import TestClient

from mitm_proxy_mcp.control.app import create_app

AUTH = {"Authorization": "Bearer secret"}
URL = "/v1/ui/traffic-filter"


def test_filter_survives_new_service_and_explicit_removal(tmp_path, monkeypatch):
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    rules = {"enabled": True, "allow": [], "ignore": ["*.example.com/*"]}
    with TestClient(create_app("secret")) as client:
        response = client.get(URL, headers=AUTH)
        assert response.status_code == 200
        assert response.json() == {"filter": None}
        response = client.put(URL, headers=AUTH, json=rules)
        assert response.status_code == 200
    with TestClient(create_app("new-token")) as restarted:
        headers = {"Authorization": "Bearer new-token"}
        assert restarted.get(URL, headers=headers).json() == {"filter": rules}
        rules["ignore"] = []
        assert restarted.put(URL, headers=headers, json=rules).status_code == 200
    with TestClient(create_app("secret")) as client:
        assert client.get(URL, headers=AUTH).json() == {"filter": rules}


def test_filter_auth_validation_and_failed_write_preserve_rules(tmp_path, monkeypatch):
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    rules = {"enabled": False, "allow": ["*api*"], "ignore": ["*ads*"]}
    with TestClient(create_app("secret")) as client:
        assert client.put(URL, json=rules).status_code == 401
        assert client.get(URL).status_code == 401
        assert client.put(URL, headers=AUTH, json=rules).status_code == 200
        assert client.put(URL, headers=AUTH, json={"ignore": 12}).status_code == 422
        assert client.get(URL, headers=AUTH).json() == {"filter": rules}

        def fail(*args, **kwargs):
            raise OSError("disk full")

        monkeypatch.setattr("os.replace", fail)
        assert (
            client.put(URL, headers=AUTH, json={**rules, "ignore": []}).status_code
            == 500
        )
        assert client.get(URL, headers=AUTH).json() == {"filter": rules}


def test_corrupt_saved_filter_is_reported_and_preserved(tmp_path, monkeypatch):
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    directory = tmp_path / ".mitmscope"
    directory.mkdir()
    path = directory / "traffic-filter.json"
    path.write_text("{broken")
    with TestClient(create_app("secret")) as client:
        assert client.get(URL, headers=AUTH).status_code == 500
    assert path.read_text() == "{broken"
