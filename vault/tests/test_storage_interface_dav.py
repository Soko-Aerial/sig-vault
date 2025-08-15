import os
import pytest
from typing import Any, Dict, List, Union

from vault.services.storage_interface import (
    connect as si_connect,
    download_file as si_download,
    upload_file as si_upload,
)
from vault.services.dav.client import WebDAVAuthError


class DummyOwnCloudClient:
    def __init__(self, base_url: str, username: str, password: str, *args, **kwargs):  # noqa: D401, ARG002
        self.base_url = base_url
        self.username = username
        self.password = password
        self.calls = []
        # Provide attributes referenced by tests
        self._list_response: Union[List[Dict[str, Any]], Exception] = []

    def list(self, remote_dir: str = ""):
        self.calls.append(("list", remote_dir))
        # Allow tests to swap response or raise
        resp = self._list_response
        if isinstance(resp, Exception):
            raise resp
        return list(resp)
        return list(self._list_response)

    def download(self, remote_path: str, local_path: str):
        self.calls.append(("download", remote_path, local_path))

    def upload(self, local_path: str, remote_path: str):
        self.calls.append(("upload", local_path, remote_path))


def _patch_dummy_client(monkeypatch):
    # Ensure the lazy import inside storage_interface picks up our dummy
    import vault.services.dav.client as dav_mod

    monkeypatch.setattr(dav_mod, "OwnCloudWebDAVClient", DummyOwnCloudClient)
    return dav_mod


def test_connect_returns_client_and_root(monkeypatch):
    _patch_dummy_client(monkeypatch)
    session = {
        "storage": "cloud",
        "server": "https://cloud.example.com",
        "username": "alice",
        "password": "secret",
    }

    handle = si_connect(session)
    client, path = handle
    assert isinstance(client, DummyOwnCloudClient)
    assert path == ""
    assert client.username == "alice"


def test_list_files_maps_fields_and_handles_auth_error(monkeypatch):
    _patch_dummy_client(monkeypatch)
    session = {
        "storage": "cloud",
        "server": "https://host/remote.php/dav/files/alice/",
        "username": "alice",
        "password": "secret",
    }

    # Build handle manually to avoid calling network
    c = DummyOwnCloudClient(session["server"], session["username"], session["password"])
    handle = (c, "")

    # 1) Happy path mapping
    c._list_response = [
        {"name": "file.txt", "is_dir": False, "size": 42},
        {"name": "folder", "is_dir": True, "size": None},
    ]

    from vault.services.storage_interface import _dav_backend

    backend = _dav_backend()  # type: ignore[call-arg]
    mapped = backend.list_files(handle)
    assert mapped == [
        {"name": "file.txt", "path": "file.txt", "size": "42", "is_dir": "false"},
        {"name": "folder", "path": "folder", "size": "0", "is_dir": "true"},
    ]

    # 2) Auth error mapping to RuntimeError
    c._list_response = WebDAVAuthError("Authentication failed (401)")
    with pytest.raises(RuntimeError) as ei:
        backend.list_files(handle)
    assert "authentication failed" in str(ei.value).lower()


def test_download_and_upload_delegate_to_client(monkeypatch, tmp_path):
    _patch_dummy_client(monkeypatch)
    session = {
        "storage": "cloud",
        "server": "https://cloud.example.com",
        "username": "bob",
        "password": "pw",
    }

    # Connect to produce a client instance
    client, _ = si_connect(session)
    assert isinstance(client, DummyOwnCloudClient)

    # download
    local_target = tmp_path / "out.bin"
    si_download(session, "remote/path.bin", str(local_target))

    # upload uses basename as remote
    local_vault = tmp_path / "payload.bin"
    local_vault.write_bytes(b"x")
    si_upload(session, str(local_vault))

    # Create a fresh client to inspect calls is tricky because si_* creates new instance
    # Instead, patch the constructor to return a shared instance to observe calls
    calls = []

    def shared_ctor(base_url, username, password, *a, **k):  # noqa: ARG001
        inst = DummyOwnCloudClient(base_url, username, password)

        # override methods to capture calls in outer list
        def dl(rp, lp):
            calls.append(("download", rp, lp))

        def ul(lp, rp):
            calls.append(("upload", lp, rp))

        inst.download = dl  # type: ignore[assignment]
        inst.upload = ul  # type: ignore[assignment]
        return inst

    import vault.services.dav.client as dav_mod

    monkeypatch.setattr(dav_mod, "OwnCloudWebDAVClient", shared_ctor)

    # Invoke again to collect calls
    si_download(session, "remote/again.bin", str(local_target))
    si_upload(session, str(local_vault))

    assert ("download", "remote/again.bin", str(local_target)) in calls
    # upload should use basename
    assert calls[-1] == ("upload", str(local_vault), os.path.basename(str(local_vault)))
