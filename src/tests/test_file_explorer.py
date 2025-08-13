from typing import Dict, Any
from pathlib import Path

from src.components.file_explorer import FileExplorer

# Pytest-qt provides qtbot fixture


def _session_info() -> Dict[str, str]:
    return {"server": "srv", "share": "sh", "username": "u", "password": "p"}


def test_file_explorer_loads_files(monkeypatch, qtbot):
    items = [
        {"name": "dir1", "path": "dir1", "size": "0", "is_dir": "true"},
        {"name": "file2.bin", "path": "file2.bin", "size": "2048", "is_dir": "false"},
    ]

    monkeypatch.setattr(
        "src.components.file_explorer.connect_to_smb_share", lambda **k: object()
    )
    monkeypatch.setattr(
        "src.components.file_explorer.list_files_in_directory", lambda root: items
    )

    fe = FileExplorer(_session_info())
    qtbot.addWidget(fe)

    assert fe.file_list.count() == len(items)
    texts = {fe.file_list.item(i).text() for i in range(fe.file_list.count())}
    assert any(t.startswith("[DIR] dir1") for t in texts)
    assert any("file2.bin" in t and ("KB" in t or "2048" in t) for t in texts)


def test_file_explorer_load_failure(monkeypatch, qtbot):
    called = {}

    def failing_connect(**k):  # noqa: ARG001
        raise RuntimeError("fail connect")

    def fake_critical(parent, title, text):  # noqa: ARG001
        called["msg"] = text

    monkeypatch.setattr(
        "src.components.file_explorer.connect_to_smb_share", failing_connect
    )
    monkeypatch.setattr("src.components.file_explorer.QMessageBox.critical", fake_critical)

    fe = FileExplorer(_session_info())
    qtbot.addWidget(fe)

    # load_files returns False -> layout may still be created but list should be empty
    assert called.get("msg") == "fail connect"
    assert fe.file_list.count() == 0


def test_file_explorer_download(monkeypatch, qtbot, tmp_path):
    items = [{"name": "file1.txt", "path": "file1.txt", "size": "5", "is_dir": "false"}]
    monkeypatch.setattr(
        "src.components.file_explorer.connect_to_smb_share", lambda **k: object()
    )
    monkeypatch.setattr(
        "src.components.file_explorer.list_files_in_directory", lambda root: items
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
        "src.components.file_explorer.QFileDialog.getSaveFileName", fake_get_save_file_name
    )
    monkeypatch.setattr("src.components.file_explorer.download_file", fake_download)
    monkeypatch.setattr(
        "src.components.file_explorer.QMessageBox.information", lambda *a, **k: None
    )

    fe = FileExplorer(_session_info())
    qtbot.addWidget(fe)

    # Select item
    fe.file_list.setCurrentRow(0)
    fe.on_item_selected(fe.file_list.currentItem())
    fe.download_selected_file()

    assert saved["remote"] == "file1.txt"
    assert Path(saved["local"]).read_text() == "dummy"


def test_file_explorer_upload(monkeypatch, qtbot, tmp_path):
    items_initial = [{"name": "a.txt", "path": "a.txt", "size": "1", "is_dir": "false"}]
    items_after = items_initial + [
        {"name": "b.txt", "path": "b.txt", "size": "2", "is_dir": "false"}
    ]

    monkeypatch.setattr(
        "src.components.file_explorer.connect_to_smb_share", lambda **k: object()
    )

    # list_files_in_directory will be called twice: first returns initial list, after upload returns extended list
    call = {"n": 0}

    def list_files(root):  # noqa: ARG001
        call["n"] += 1
        return items_initial if call["n"] == 1 else items_after

    monkeypatch.setattr("src.components.file_explorer.list_files_in_directory", list_files)

    def fake_get_open_file_name(parent, title):  # noqa: ARG001
        f = tmp_path / "upload.bin"
        f.write_text("payload")
        return str(f), ""

    uploaded: Dict[str, Any] = {}

    def fake_upload(session_info, path):  # noqa: ARG001
        uploaded["path"] = path

    monkeypatch.setattr(
        "src.components.file_explorer.QFileDialog.getOpenFileName", fake_get_open_file_name
    )
    monkeypatch.setattr("src.components.file_explorer.upload_file", fake_upload)
    monkeypatch.setattr(
        "src.components.file_explorer.QMessageBox.information", lambda *a, **k: None
    )

    fe = FileExplorer(_session_info())
    qtbot.addWidget(fe)

    assert fe.file_list.count() == 1
    fe.upload_file()
    # After upload list refreshed
    assert fe.file_list.count() == 2
    assert Path(uploaded["path"]).name == "upload.bin"
