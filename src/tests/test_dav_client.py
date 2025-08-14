import pytest

from src.services.dav.client import (
    OwnCloudWebDAVClient,
    WebDAVAuthError,
    WebDAVNotFoundError,
    WebDAVConnectionError,
)


def test_dav_list_401_maps_to_auth_error(monkeypatch):
    client = OwnCloudWebDAVClient(
        base_url="http://example.com/remote.php/dav/files/user/",
        username="user",
        password="pass",
        verify=False,
    )

    def raise_401(path):  # noqa: ARG001
        # Simulate the error reported by the user, including 401 and NotAuthenticated
        raise Exception(
            "Request to http://example.com/remote.php/dav/files/user/ failed with code 401 "
            'and message: b\'<?xml version="1.0" encoding="utf-8"?>\n<d:error xmlns:d="DAV:" xmlns:s="http://sabredav.org/ns">\n'
            "  <s:exception>Sabre\\DAV\\Exception\\NotAuthenticated</s:exception>\n"
            "  <s:message>No public access to this resource., Username or password was incorrect</s:message>\n"
            "</d:error>\n'"
        )

    # Patch underlying webdav3 client.list to raise 401
    monkeypatch.setattr(client.client, "list", raise_401)

    with pytest.raises(WebDAVAuthError) as excinfo:
        client.list("")

    assert "Authentication failed (401)" in str(excinfo.value)


def test_base_url_autocorrects_for_owncloud():
    c = OwnCloudWebDAVClient(
        base_url="https://cloud.example.com",  # missing remote.php path
        username="alice",
        password="secret",
        verify=False,
    )
    assert c.base.endswith("/remote.php/dav/files/alice/")


def test_list_parses_file_sizes_and_dirs(monkeypatch):
    c = OwnCloudWebDAVClient(
        base_url="https://cloud.example.com/remote.php/dav/files/alice/",
        username="alice",
        password="secret",
        verify=False,
    )

    # webdav3 client.list returns names, dirs typically end with '/'
    monkeypatch.setattr(
        c.client,
        "list",
        lambda path: ["file.txt", "folder/", "badsize.bin"],
    )

    # client.info provides details including size; simulate one invalid value
    def fake_info(p):
        if p == "file.txt":
            return {"size": "123"}
        if p == "badsize.bin":
            return {"size": "oops"}
        return {}

    monkeypatch.setattr(c.client, "info", fake_info)

    results = c.list("")
    by_name = {r["name"]: r for r in results}
    assert by_name["file.txt"]["is_dir"] is False
    assert by_name["file.txt"]["size"] == 123
    assert by_name["folder"]["is_dir"] is True
    assert by_name["folder"].get("size") in (None,)
    # Invalid size should result in None
    assert by_name["badsize.bin"].get("size") is None


@pytest.mark.parametrize(
    "msg,exc_type,needle",
    [
        ("HTTP 403 Forbidden", WebDAVAuthError, "forbidden"),
        ("404 Not Found", WebDAVNotFoundError, "not found"),
        ("connection timed out", WebDAVConnectionError, "webdav error"),
    ],
)
def test_error_mapping_on_list(monkeypatch, msg, exc_type, needle):
    c = OwnCloudWebDAVClient(
        base_url="https://cloud.example.com/remote.php/dav/files/alice/",
        username="alice",
        password="secret",
        verify=False,
    )

    def boom(path):  # noqa: ARG001
        raise Exception(msg)

    monkeypatch.setattr(c.client, "list", boom)

    with pytest.raises(exc_type) as ei:
        c.list("docs")
    assert needle in str(ei.value).lower()


def test_makedirs_creates_nested_paths_in_order(monkeypatch):
    c = OwnCloudWebDAVClient(
        base_url="https://cloud.example.com/remote.php/dav/files/alice/",
        username="alice",
        password="secret",
        verify=False,
    )
    calls = []
    monkeypatch.setattr(c.client, "mkdir", lambda p: calls.append(p))

    c.makedirs("a/b/c")
    assert calls == ["a/", "a/b/", "a/b/c/"]


def test_upload_calls_makedirs_then_upload_sync(monkeypatch):
    c = OwnCloudWebDAVClient(
        base_url="https://cloud.example.com/remote.php/dav/files/alice/",
        username="alice",
        password="secret",
        verify=False,
    )
    order = []

    def track_makedirs(p):
        order.append(("makedirs", p))

    def track_upload(**kw):
        order.append(("upload", kw))

    monkeypatch.setattr(c, "makedirs", track_makedirs)
    monkeypatch.setattr(c.client, "upload_sync", track_upload)

    c.upload("/local/file.bin", "parent/file.bin")

    assert order[0] == ("makedirs", "parent")
    assert order[1][0] == "upload"
    assert order[1][1]["remote_path"] == "parent/file.bin"
    assert order[1][1]["local_path"].endswith("file.bin")


def test_mirror_down_normalizes_remote_dir(monkeypatch, tmp_path):
    c = OwnCloudWebDAVClient(
        base_url="https://cloud.example.com/remote.php/dav/files/alice/",
        username="alice",
        password="secret",
        verify=False,
    )
    captured = {}
    monkeypatch.setattr(
        c.client,
        "download_sync",
        lambda remote_path, local_path: captured.update(
            {"remote": remote_path, "local": local_path}
        ),
    )

    c.mirror_down("docs", tmp_path.as_posix())
    assert captured["remote"] == "docs/"
    assert captured["local"] == tmp_path.as_posix()
