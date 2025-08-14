from __future__ import annotations

import json
import os
from typing import Dict, Any

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .file_tree_viewer import FileExplorer
from .connection_form import ConnectionForm


# Single consolidated storage file
CREDENTIALS_PATH = ".sig/credentials.json"


class Explorer(QWidget):
    """Single-widget UI that hosts storage selection, config, and the file list.

    Top bar elements:
    - Storage mode dropdown (Local/Cloud)
    - Location display (read-only)
    - Upload button
    - Download button (acts on current selection)
    - Config button (opens ConnectionForm in a dialog)
    """

    def __init__(self) -> None:
        super().__init__()

        self._session_info: Dict[str, str] = {}

        # --- Top bar UI ---
        self.top_bar = QHBoxLayout()

        self.storage_combo = QComboBox()
        self.storage_combo.addItems(["Local", "Cloud"])
        self.storage_combo.currentTextChanged.connect(self.on_storage_changed)
        self.top_bar.addWidget(self.storage_combo)

        self.location_display = QLineEdit()
        self.location_display.setReadOnly(True)
        self.location_display.setPlaceholderText(
            "Location will appear here after connecting…"
        )
        self.top_bar.addWidget(self.location_display, 1)

        self.upload_btn = QPushButton("Upload")
        self.upload_btn.clicked.connect(self.on_upload_clicked)
        self.top_bar.addWidget(self.upload_btn)

        # New: top-level Download button
        self.download_btn = QPushButton("Download")
        self.download_btn.setEnabled(False)
        self.top_bar.addWidget(self.download_btn)

        self.config_btn = QPushButton("Config…")
        self.config_btn.clicked.connect(self.open_config_dialog)
        self.top_bar.addWidget(self.config_btn)

        # --- File explorer ---
        # We'll connect after loading saved settings.
        self.explorer = FileExplorer(session_info={})
        # Hide the explorer's own top bar to avoid duplicated location and upload controls
        try:
            top_bar = getattr(self.explorer, "top_bar", None)
            if isinstance(top_bar, QHBoxLayout):
                for i in range(top_bar.count()):
                    w = top_bar.itemAt(i).widget()
                    if w is not None:
                        w.setVisible(False)
                parent = top_bar.parent()
                if isinstance(parent, QWidget):
                    parent.setVisible(False)
            elif top_bar is not None and hasattr(top_bar, "setVisible"):
                top_bar.setVisible(False)
        except Exception:
            pass

        # Wire the top Download button to explorer action and selection state
        try:
            self.download_btn.clicked.connect(self.explorer.download_selected_file)
            self.explorer.file_list.itemSelectionChanged.connect(
                self._on_selection_changed
            )
        except Exception:
            pass

        # --- Main layout ---
        layout = QVBoxLayout()
        layout.addLayout(self.top_bar)
        layout.addWidget(self.explorer)
        self.setLayout(layout)

        # Initialize storage combo from saved preference and try to connect
        mode = self._read_storage_selection()
        self._set_storage_combo(mode)
        self.refresh_from_saved()

    # ---- UI handlers ----
    def on_storage_changed(self, _text: str) -> None:
        # Persist selection and reconnect using saved credentials for that mode
        self._save_storage_selection(self._combo_mode())
        self.refresh_from_saved()

    def on_upload_clicked(self) -> None:
        try:
            self.explorer.upload_file()
        except Exception as e:
            QMessageBox.critical(self, "Upload", str(e))

    def open_config_dialog(self) -> None:
        # Wrap ConnectionForm inside a dialog
        dlg = QDialog(self)
        dlg.setWindowTitle("Connection Settings")
        v = QVBoxLayout(dlg)

        def on_connected(info: Dict[str, str]) -> None:
            # When the form connects, update our view and close the dialog
            try:
                # The form persists unified credentials and default mode
                self._set_storage_combo(self._read_storage_selection())
                # Update session/location and refresh file list
                self._session_info = info
                self._update_location_display()
                self._refresh_explorer()
            finally:
                dlg.accept()

        form = ConnectionForm(callback=on_connected)

        # Sync the form's storage selector with our combo
        try:
            current = self._combo_mode()
            if current == "cloud":
                form.storage_input.setCurrentIndex(1)
            else:
                form.storage_input.setCurrentIndex(0)
        except Exception:
            pass

        v.addWidget(form)
        # Add a small close button row for UX if the user chooses not to connect
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(dlg.reject)
        v.addWidget(buttons)

        dlg.setModal(True)
        dlg.resize(520, 380)
        dlg.exec()

    # ---- Data wiring ----
    def refresh_from_saved(self) -> None:
        """Build a session from saved settings and connect the explorer."""
        session = self._build_session_from_saved()
        self._session_info = session
        # Guard: if missing credentials, don't auto-connect
        storage = (session.get("storage") or "local").strip().lower()
        user = (session.get("username") or "").strip()
        pwd = (session.get("password") or "").strip()
        if not user or not pwd:
            self.location_display.clear()
            # Keep explorer empty with a friendly status
            try:
                self.explorer.file_list.clear()
                self.explorer.status_label.setText("Not connected")
                self.explorer._update_status()
            except Exception:
                pass
            self._on_selection_changed()  # disable download
            return
        if storage != "cloud":
            if not (session.get("server") and session.get("share")):
                try:
                    self.explorer.file_list.clear()
                    self.explorer.status_label.setText("Not connected")
                    self.explorer._update_status()
                except Exception:
                    pass
                self._on_selection_changed()  # disable download
                return
        self._update_location_display()
        self._refresh_explorer()

    def _refresh_explorer(self) -> None:
        # Swap the explorer's session and reload
        self.explorer.session_info = self._session_info
        # Clear previous selection and list-state before reloading
        try:
            self.explorer.file_list.clear()
            self.explorer.selected_path = None
        except Exception:
            pass
        self._on_selection_changed()  # disable download
        self.explorer.load_files()

    def _update_location_display(self) -> None:
        storage = self._combo_mode()
        if storage == "cloud":
            self.location_display.setText(self._session_info.get("server", ""))
        else:
            server = self._session_info.get("server", "")
            share = self._session_info.get("share", "")
            if server or share:
                self.location_display.setText(f"\\\\{server}\\{share}".rstrip("\\"))
            else:
                self.location_display.clear()

    def _on_selection_changed(self) -> None:
        try:
            self.download_btn.setEnabled(bool(self.explorer.selected_path))
        except Exception:
            self.download_btn.setEnabled(False)

    # ---- Persistence helpers ----
    def _combo_mode(self) -> str:
        return (self.storage_combo.currentText() or "").strip().lower()

    def _set_storage_combo(self, mode: str) -> None:
        mode = (mode or "smb").strip().lower()
        if mode in {"local nas drive", "smb", "local", "nas"}:
            self.storage_combo.setCurrentIndex(0)
        else:
            self.storage_combo.setCurrentIndex(1)

    def _read_storage_selection(self) -> str:
        try:
            with open(CREDENTIALS_PATH, "r") as f:
                data = json.load(f)
                return str(data.get("default_mode", "local")).strip().lower()
        except Exception:
            return "local"

    def _save_storage_selection(self, mode: str) -> None:
        try:
            os.makedirs(os.path.dirname(CREDENTIALS_PATH) or ".", exist_ok=True)
            # Read-modify-write to preserve other fields
            data: Dict = {}
            if os.path.exists(CREDENTIALS_PATH):
                with open(CREDENTIALS_PATH, "r") as f:
                    try:
                        data = json.load(f) or {}
                    except Exception:
                        data = {}
            data["default_mode"] = "cloud" if mode == "cloud" else "local"
            with open(CREDENTIALS_PATH, "w") as f:
                json.dump(data, f)
        except Exception:
            pass

    def _read_all_credentials(self) -> Dict[str, Any]:
        try:
            if os.path.exists(CREDENTIALS_PATH):
                with open(CREDENTIALS_PATH, "r") as f:
                    data = json.load(f)
                    if isinstance(data, dict):
                        return data  # type: ignore[return-value]
        except Exception:
            pass
        return {}

    def _dec_password(self, s: str) -> str:
        if not s:
            return ""
        if isinstance(s, str) and s.startswith("b64:"):
            import base64

            try:
                return base64.b64decode(s[4:].encode("ascii")).decode("utf-8")
            except Exception:
                return ""
        return s

    def _build_session_from_saved(self) -> Dict[str, str]:
        mode = self._read_storage_selection()
        creds = self._read_all_credentials()
        if mode in {"local", "smb", "nas", "local nas drive"}:
            c = creds.get("local", {})
            return {
                "server": c.get("server", ""),
                "share": c.get("share", ""),
                "username": c.get("username", ""),
                "password": self._dec_password(c.get("password", "")),
                "storage": "local",
            }
        else:
            c = creds.get("cloud", {})
            return {
                "server": c.get("base_url", ""),
                "share": "",
                "username": c.get("username", ""),
                "password": self._dec_password(c.get("password", "")),
                "storage": "cloud",
            }
