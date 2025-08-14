from typing import Dict, Any
from pathlib import Path

from src.components.file_tree_viewer import FileExplorer

# Pytest-qt provides qtbot fixture


def _session_info() -> Dict[str, str]:
    return {"server": "srv", "share": "sh", "username": "u", "password": "p"}


def test_file_viewer_loads_files(monkeypatch, qtbot):
    items = [
        {"name": "dir1", "path": "dir1", "size": "0", "is_dir": "true"},
        {"name": "file2.bin", "path": "file2.bin", "size": "2048", "is_dir": "false"},
    ]

    monkeypatch.setattr(
        "src.components.file_tree_viewer.connect_to_smb_share", lambda **k: object()
    )
    monkeypatch.setattr(
        "src.components.file_tree_viewer.list_files_in_directory", lambda root: items
    )

    fe = FileExplorer(_session_info())
    qtbot.addWidget(fe)

    # Verify header and row count
    header_labels = [fe.file_list.headerItem().text(i) for i in range(3)]
    assert header_labels == ["Name", "Size", "Type"]
    assert fe.file_list.topLevelItemCount() == len(items)

    # Extract tuples of (name, size, type)
    rows = []
    for i in range(fe.file_list.topLevelItemCount()):
        it = fe.file_list.topLevelItem(i)
        assert it is not None
        rows.append((it.text(0), it.text(1), it.text(2)))
    assert ("dir1", "", "Folder") in rows
    # Size should be human-readable KB for 2048 bytes
    assert any(
        r[0] == "file2.bin" and ("KB" in r[1]) and r[2] in {"File", ""} for r in rows
    )


def test_file_viewer_load_failure(monkeypatch, qtbot):
    called = {}

    def failing_connect(**k):  # noqa: ARG001
        raise RuntimeError("fail connect")

    def fake_critical(parent, title, text):  # noqa: ARG001
        called["msg"] = text

    monkeypatch.setattr(
        "src.components.file_tree_viewer.connect_to_smb_share", failing_connect
    )
    monkeypatch.setattr(
        "src.components.file_tree_viewer.QMessageBox.critical", fake_critical
    )

    fe = FileExplorer(_session_info())
    qtbot.addWidget(fe)

    # load_files returns False -> layout may still be created but tree should be empty
    assert called.get("msg") == "fail connect"
    assert fe.file_list.topLevelItemCount() == 0


def test_file_viewer_load_failure_dav_401(monkeypatch, qtbot):
    """Simulate a WebDAV 401 mapping up to a user-friendly error message."""
    called = {}

    def fake_critical(parent, title, text):  # noqa: ARG001
        called["msg"] = text

    # Simulate connection succeeds but listing raises a friendly auth message
    monkeypatch.setattr(
        "src.components.file_tree_viewer.connect_to_smb_share", lambda **k: object()
    )

    def failing_list(root):  # noqa: ARG001
        raise RuntimeError(
            "Authentication failed (401) while trying to list directory. Please verify your username, password, and server URL."
        )

    monkeypatch.setattr(
        "src.components.file_tree_viewer.list_files_in_directory", failing_list
    )
    monkeypatch.setattr(
        "src.components.file_tree_viewer.QMessageBox.critical", fake_critical
    )

    fe = FileExplorer(_session_info())
    qtbot.addWidget(fe)

    assert "Authentication failed (401)" in called.get("msg", "")
    assert fe.file_list.topLevelItemCount() == 0


def test_file_viewer_download(monkeypatch, qtbot, tmp_path):
    items = [{"name": "file1.txt", "path": "file1.txt", "size": "5", "is_dir": "false"}]
    monkeypatch.setattr(
        "src.components.file_tree_viewer.connect_to_smb_share", lambda **k: object()
    )
    monkeypatch.setattr(
        "src.components.file_tree_viewer.list_files_in_directory", lambda root: items
    )

    saved: Dict[str, Any] = {}

    def fake_get_save_file_name(parent, title, default):  # noqa: ARG001
        dest = tmp_path / "out.txt"
        return str(dest), ""

    def fake_download(session_info, remote, local):  # noqa: ARG001
        saved["remote"] = remote
        saved["local"] = local
        Path(local).write_text("dummy")

    monkeypatch.setattr(
        "src.components.file_tree_viewer.QFileDialog.getSaveFileName",
        fake_get_save_file_name,
    )
    monkeypatch.setattr("src.components.file_tree_viewer.download_file", fake_download)
    monkeypatch.setattr(
        "src.components.file_tree_viewer.QMessageBox.information", lambda *a, **k: None
    )

    fe = FileExplorer(_session_info())
    qtbot.addWidget(fe)

    # Select item using QTreeWidget API
    it = fe.file_list.topLevelItem(0)
    assert it is not None
    fe.file_list.setCurrentItem(it)
    fe.on_item_selected(it)
    fe.download_selected_file()

    assert saved["remote"] == "file1.txt"
    assert Path(saved["local"]).read_text() == "dummy"


def test_file_viewer_upload(monkeypatch, qtbot, tmp_path):
    items_initial = [{"name": "a.txt", "path": "a.txt", "size": "1", "is_dir": "false"}]
    items_after = items_initial + [
        {"name": "b.txt", "path": "b.txt", "size": "2", "is_dir": "false"}
    ]

    monkeypatch.setattr(
        "src.components.file_tree_viewer.connect_to_smb_share", lambda **k: object()
    )

    # list_files_in_directory will be called twice: first returns initial list, after upload returns extended list
    call = {"n": 0}

    def list_files(root):  # noqa: ARG001
        call["n"] += 1
        return items_initial if call["n"] == 1 else items_after

    monkeypatch.setattr(
        "src.components.file_tree_viewer.list_files_in_directory", list_files
    )

    def fake_get_open_file_name(parent, title):  # noqa: ARG001
        f = tmp_path / "upload.bin"
        f.write_text("payload")
        return str(f), ""

    uploaded: Dict[str, Any] = {}

    def fake_upload(session_info, path):  # noqa: ARG001
        uploaded["path"] = path

    monkeypatch.setattr(
        "src.components.file_tree_viewer.QFileDialog.getOpenFileName",
        fake_get_open_file_name,
    )
    monkeypatch.setattr("src.components.file_tree_viewer.upload_file", fake_upload)
    monkeypatch.setattr(
        "src.components.file_tree_viewer.QMessageBox.information", lambda *a, **k: None
    )

    fe = FileExplorer(_session_info())
    qtbot.addWidget(fe)

    assert fe.file_list.topLevelItemCount() == 1
    fe.upload_file()
    # After upload list refreshed
    assert fe.file_list.topLevelItemCount() == 2
    assert Path(uploaded["path"]).name == "upload.bin"
