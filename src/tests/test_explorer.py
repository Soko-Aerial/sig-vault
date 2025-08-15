import base64
import json
from pathlib import Path
from typing import Dict, Any

from src.components.explorer import Explorer


def _b64(s: str) -> str:
    return "b64:" + base64.b64encode(s.encode("utf-8")).decode("ascii")


def make_creds_file(path: Path, data: Dict[str, Any]) -> None:
    path.write_text(json.dumps(data))


def test_init_with_missing_credentials_shows_not_connected(
    monkeypatch, qtbot, tmp_path
):
    creds = tmp_path / "credentials.json"
    # Redirect credentials path used by Explorer
    monkeypatch.setattr("src.components.explorer.CREDENTIALS_PATH", str(creds))

    # Avoid real backend work triggered by inner FileExplorer.load_files
    monkeypatch.setattr(
        "src.components.file_tree_viewer.FileExplorer.load_files", lambda self: True
    )

    w = Explorer()
    qtbot.addWidget(w)

    # No creds -> Explorer refresh_from_saved sets Not connected state and disables download
    assert w.explorer.status_label.text() in {"Not connected", "No files to display"}
    assert not w.download_btn.isEnabled()


def test_refresh_from_saved_local_and_cloud(monkeypatch, qtbot, tmp_path):
    creds = tmp_path / "credentials.json"
    monkeypatch.setattr("src.components.explorer.CREDENTIALS_PATH", str(creds))

    # Stub load_files so we can observe it being called on successful connect
    called: Dict[str, Any] = {"n": 0}

    def fake_load(self):  # noqa: ANN001
        called["n"] += 1
        # Pretend we loaded some items
        return True

    monkeypatch.setattr(
        "src.components.file_tree_viewer.FileExplorer.load_files", fake_load
    )

    # 1) Local mode with complete creds
    make_creds_file(
        creds,
        {
            "default_mode": "local",
            "local": {
                "server": "srv",
                "share": "share",
                "username": "user",
                "password": _b64("pass"),
            },
            "cloud": {"base_url": "https://cloud", "username": "", "password": ""},
        },
    )

    w = Explorer()
    qtbot.addWidget(w)

    # After init, refresh_from_saved should have connected once
    assert called["n"] >= 1
    assert w.location_display.text().startswith("\\\\srv\\share")

    # 2) Switch to cloud and ensure persistence + reconnection
    make_creds_file(
        creds,
        {
            "default_mode": "cloud",
            "local": {
                "server": "srv",
                "share": "share",
                "username": "user",
                "password": _b64("pass"),
            },
            "cloud": {
                "base_url": "https://example.com/remote.php/dav/files/user/",
                "username": "u",
                "password": _b64("p"),
            },
        },
    )

    # Sync the storage combo to the latest saved mode and reload
    w._set_storage_combo(w._read_storage_selection())
    w.refresh_from_saved()
    # Cloud shows only the path derived from the base URL when no explicit path is set
    assert w.location_display.text() == "user/"
    assert called["n"] >= 2


def test_storage_combo_change_persists(monkeypatch, qtbot, tmp_path):
    creds = tmp_path / "credentials.json"
    monkeypatch.setattr("src.components.explorer.CREDENTIALS_PATH", str(creds))

    make_creds_file(
        creds,
        {
            "default_mode": "local",
            "local": {"server": "", "share": "", "username": "", "password": ""},
            "cloud": {"base_url": "", "username": "", "password": ""},
        },
    )

    monkeypatch.setattr(
        "src.components.file_tree_viewer.FileExplorer.load_files", lambda self: True
    )

    w = Explorer()
    qtbot.addWidget(w)

    # Change to Cloud via UI and ensure file updated
    w.storage_combo.setCurrentText("Cloud")
    # on_storage_changed persists
    saved = json.loads(creds.read_text())
    assert saved["default_mode"] == "cloud"


def test_local_missing_server_or_share_skips_connect(monkeypatch, qtbot, tmp_path):
    creds = tmp_path / "credentials.json"
    monkeypatch.setattr("src.components.explorer.CREDENTIALS_PATH", str(creds))

    # user/pass present but server/share missing
    make_creds_file(
        creds,
        {
            "default_mode": "local",
            "local": {
                "server": "",
                "share": "",
                "username": "u",
                "password": _b64("p"),
            },
            "cloud": {"base_url": "", "username": "", "password": ""},
        },
    )

    # Count load_files calls; one call happens on FileExplorer construction
    called = {"n": 0}

    def fake_load(self):  # noqa: ANN001
        called["n"] += 1
        return True

    monkeypatch.setattr(
        "src.components.file_tree_viewer.FileExplorer.load_files", fake_load
    )

    w = Explorer()
    qtbot.addWidget(w)

    # Should not attempt to connect; status shows Not connected or empty-list status
    assert w.explorer.status_label.text() in {"Not connected", "No files to display"}
    assert called["n"] == 1


def test_update_location_display_branches(monkeypatch, qtbot, tmp_path):
    creds = tmp_path / "credentials.json"
    monkeypatch.setattr("src.components.explorer.CREDENTIALS_PATH", str(creds))
    make_creds_file(
        creds,
        {
            "default_mode": "local",
            "local": {
                "server": "NAS",
                "share": "docs",
                "username": "u",
                "password": _b64("p"),
            },
            "cloud": {
                "base_url": "https://oc/",
                "username": "u",
                "password": _b64("p"),
            },
        },
    )

    monkeypatch.setattr(
        "src.components.file_tree_viewer.FileExplorer.load_files", lambda self: True
    )

    w = Explorer()
    qtbot.addWidget(w)

    # Local branch
    w._session_info = {
        "server": "NAS",
        "share": "docs",
        "username": "u",
        "password": "p",
        "storage": "local",
    }
    w._set_storage_combo("local")
    w._update_location_display()
    assert w.location_display.text().startswith("\\\\NAS\\docs")

    # Cloud branch
    w._session_info = {
        "server": "https://oc/",
        "share": "",
        "username": "u",
        "password": "p",
        "storage": "cloud",
    }
    w._set_storage_combo("cloud")
    w._update_location_display()
    # Cloud shows root path when base URL has no user/path segment
    assert w.location_display.text() == "/"


def test_config_dialog_connected_flow(monkeypatch, qtbot, tmp_path):
    creds = tmp_path / "credentials.json"
    monkeypatch.setattr("src.components.explorer.CREDENTIALS_PATH", str(creds))
    make_creds_file(
        creds,
        {
            "default_mode": "cloud",
            "local": {"server": "", "share": "", "username": "", "password": ""},
            "cloud": {
                "base_url": "https://cloud/",
                "username": "u",
                "password": _b64("p"),
            },
        },
    )

    # Make FileExplorer.load_files observable
    called = {"loaded": False}

    def fake_load(self):  # noqa: ANN001
        called["loaded"] = True
        return True

    monkeypatch.setattr(
        "src.components.file_tree_viewer.FileExplorer.load_files", fake_load
    )

    # Replace ConnectionForm with a fake that immediately calls the callback
    from PySide6.QtWidgets import QWidget, QComboBox

    class FakeForm(QWidget):
        def __init__(self, callback):  # noqa: D401, ANN001
            super().__init__()
            self.storage_input = QComboBox()
            # Simulate a successful connection; explorer should update
            callback(
                {
                    "server": "https://cloud/",
                    "share": "",
                    "username": "u",
                    "password": "p",
                    "storage": "cloud",
                }
            )

    monkeypatch.setattr("src.components.explorer.ConnectionForm", FakeForm)

    # Ensure dialog.exec doesn't block
    monkeypatch.setattr("src.components.explorer.QDialog.exec", lambda self: 0)

    w = Explorer()
    qtbot.addWidget(w)
    w.open_config_dialog()

    # Cloud shows root path when base URL has no user/path segment
    assert w.location_display.text() == "/"
    assert called["loaded"] is True


def test_upload_button_and_selection_toggle(monkeypatch, qtbot, tmp_path):
    creds = tmp_path / "credentials.json"
    monkeypatch.setattr("src.components.explorer.CREDENTIALS_PATH", str(creds))
    make_creds_file(
        creds,
        {
            "default_mode": "local",
            "local": {
                "server": "s",
                "share": "sh",
                "username": "u",
                "password": _b64("p"),
            },
            "cloud": {"base_url": "", "username": "", "password": ""},
        },
    )

    class DummyFE:
        def __init__(self):
            self.selected_path = None

        def upload_file(self):  # noqa: D401
            self.selected_path = "x"

        def load_files(self):  # noqa: D401
            return True

    # Inject a dummy explorer instance after construction
    monkeypatch.setattr(
        "src.components.file_tree_viewer.FileExplorer.load_files", lambda self: True
    )

    w = Explorer()
    qtbot.addWidget(w)
    w.explorer = DummyFE()  # type: ignore[assignment]

    # Upload triggers the delegated method without raising
    w.on_upload_clicked()
    assert w.explorer.selected_path == "x"

    # Download button enables when a selection exists
    w._on_selection_changed()
    assert w.download_btn.isEnabled() is True


def test_helpers_and_persistence(monkeypatch, qtbot, tmp_path):
    creds = tmp_path / "credentials.json"
    monkeypatch.setattr("src.components.explorer.CREDENTIALS_PATH", str(creds))

    # Build sample creds with encoded passwords
    data = {
        "default_mode": "local",
        "local": {
            "server": "s",
            "share": "sh",
            "username": "u",
            "password": _b64("p1"),
        },
        "cloud": {"base_url": "https://c/", "username": "u2", "password": _b64("p2")},
    }
    make_creds_file(creds, data)

    monkeypatch.setattr(
        "src.components.file_tree_viewer.FileExplorer.load_files", lambda self: True
    )

    w = Explorer()
    qtbot.addWidget(w)

    # _build_session_from_saved reflects default_mode and decodes password
    sess = w._build_session_from_saved()
    assert sess["storage"] == "local"
    assert sess["password"] == "p1"

    # Switch default to cloud and verify
    data["default_mode"] = "cloud"
    make_creds_file(creds, data)
    sess2 = w._build_session_from_saved()
    assert sess2["storage"] == "cloud"
    assert sess2["password"] == "p2"
    assert sess2["server"] == "https://c/"

    # _save_storage_selection preserves other fields
    w._save_storage_selection("cloud")
    saved = json.loads(creds.read_text())
    assert saved["default_mode"] == "cloud"
    assert "local" in saved and "cloud" in saved

    # _dec_password handles raw, encoded, and empty
    assert w._dec_password("") == ""
    assert w._dec_password("plain") == "plain"
    assert w._dec_password(_b64("abc")) == "abc"

    # _set_storage_combo handles synonyms and _combo_mode reflects it
    w._set_storage_combo("smb")
    assert w._combo_mode() == "local"
    w._set_storage_combo("cloud")
    assert w._combo_mode() == "cloud"
