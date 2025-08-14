from typing import Dict, Any

from src.services.dav.client import OwnCloudWebDAVClient


def test_list_enriches_file_and_dir_modified(monkeypatch):
    c = OwnCloudWebDAVClient(
        base_url="https://cloud.example.com/remote.php/dav/files/alice/",
        username="alice",
        password="secret",
        verify=False,
    )

    # Provide one file and one directory
    monkeypatch.setattr(c.client, "list", lambda path: ["file.txt", "folder/"])

    # Track info() calls to ensure both file and dir are queried
    calls: Dict[str, Any] = {}

    def fake_info(p: str):
        calls[p] = True
        if p.endswith("/"):
            return {"modified": "2025-01-01T10:00:00Z"}
        else:
            return {"size": "2048", "last_modified": "2025-01-02T12:34:00Z"}

    monkeypatch.setattr(c.client, "info", fake_info)
    # Ensure directory metadata is enabled
    monkeypatch.setenv("SIG_WEB_DAV_INFO_DIRECTORIES", "1")
    monkeypatch.setenv("SIG_WEB_DAV_INFO_WORKERS", "2")

    results = c.list("")
    by_name = {r["name"]: r for r in results}
    assert by_name["file.txt"]["size"] == 2048 or by_name["file.txt"]["size"] == "2048"
    assert by_name["file.txt"]["modified"] is not None
    assert by_name["folder"]["modified"] is not None
    # Both entries should have triggered info()
    assert "file.txt" in calls and "folder/" in calls


def test_list_respects_directory_metadata_toggle(monkeypatch):
    c = OwnCloudWebDAVClient(
        base_url="https://cloud.example.com/remote.php/dav/files/alice/",
        username="alice",
        password="secret",
        verify=False,
    )

    monkeypatch.setattr(c.client, "list", lambda path: ["a.txt", "dir/"])

    # Only files will be enriched when env toggle disables dirs
    monkeypatch.setenv("SIG_WEB_DAV_INFO_DIRECTORIES", "0")
    monkeypatch.setenv("SIG_WEB_DAV_INFO_WORKERS", "1")

    def info_only_file(p: str):
        # If called for directory, raise to catch unwanted calls
        if p.endswith("/"):
            raise AssertionError("directory info() should be disabled by env toggle")
        return {"size": 1, "modified": "2025-01-01T00:00:00Z"}

    monkeypatch.setattr(c.client, "info", info_only_file)

    results = c.list("")
    by = {r["name"]: r for r in results}
    assert by["a.txt"]["size"] in (1, "1")
    # Directory may not have modified when disabled
    # but presence is optional; just ensure no crash and structure present
    assert by["dir"]["is_dir"] is True
