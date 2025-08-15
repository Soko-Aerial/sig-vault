import base64
import json
import logging
import os
from PySide6.QtWidgets import (
    QWidget,
    QFormLayout,
    QLineEdit,
    QPushButton,
    QComboBox,
    QLabel,
)
from PySide6.QtCore import Signal
from typing import Callable, Dict, Any

logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.ERROR,
    format="%(asctime)s | %(filename)s:%(lineno)s \t [%(levelname)s] %(message)s",
)

# Single source of truth for settings and credentials
CREDENTIALS_PATH = ".sig/credentials.json"


class ConnectionForm(QWidget):
    connected: Signal = Signal(dict)

    def __init__(self, callback: Callable[[Dict[str, str]], None]) -> None:
        super().__init__()
        self.callback = callback
        self.init_ui()
        self.load_config()
        # Optionally auto-connect using last used configuration
        try:
            self.try_auto_connect_on_startup()
        except Exception:
            # Never block UI on auto-connect issues
            logger.debug("Auto-connect on startup skipped due to error", exc_info=True)

    def init_ui(self) -> None:
        self.server_input = QLineEdit()
        self.share_input = QLineEdit()
        self.username_input = QLineEdit()
        self.password_input = QLineEdit()
        self.storage_input = QComboBox()
        self.storage_input.addItems(["Local", "Cloud"])  # Default to Local
        self.connect_btn = QPushButton("Connect")

        layout = QFormLayout()
        layout.addRow("Storage", self.storage_input)
        self.server_label = QLabel("Server")
        layout.addRow(self.server_label, self.server_input)
        # Keep a dedicated label widget so we can hide/show together with input
        self.share_label = QLabel("Share")
        layout.addRow(self.share_label, self.share_input)
        layout.addRow("Username", self.username_input)
        layout.addRow("Password", self.password_input)
        layout.addWidget(self.connect_btn)

        # Wire events
        self.storage_input.currentTextChanged.connect(self.on_storage_changed)
        self.username_input.textChanged.connect(self.on_username_changed)
        self.connect_btn.clicked.connect(self.on_connect)
        self.setLayout(layout)

    # ---- credentials helpers (single file) ----
    def _mode_key(self, storage: str) -> str:
        s = (storage or "").strip().lower()
        return "local" if s in {"local nas drive", "smb", "local", "nas"} else "cloud"

    def _enc(self, s: str) -> str:
        if not s:
            return ""
        return "b64:" + base64.b64encode(s.encode("utf-8")).decode("ascii")

    def _dec(self, s: str) -> str:
        if not s:
            return ""
        if s.startswith("b64:"):
            try:
                return base64.b64decode(s[4:].encode("ascii")).decode("utf-8")
            except Exception:
                return ""
        return s

    def _read_all_credentials(self) -> Dict[str, Any]:
        try:
            if os.path.exists(CREDENTIALS_PATH):
                with open(CREDENTIALS_PATH, "r") as f:
                    data = json.load(f)
                    if isinstance(data, dict):
                        return data  # type: ignore[return-value]
        except Exception:
            pass
        # default skeleton
        return {
            "default_mode": "local",
            "local": {"server": "", "share": "", "username": "", "password": ""},
            "cloud": {"base_url": "", "username": "", "password": ""},
        }

    def _write_all_credentials(self, data: Dict) -> None:
        try:
            os.makedirs(os.path.dirname(CREDENTIALS_PATH) or ".", exist_ok=True)
            with open(CREDENTIALS_PATH, "w") as f:
                json.dump(data, f)
        except Exception:
            pass

    def _load_mode_credentials(self, storage: str) -> None:
        data = self._read_all_credentials()
        key = self._mode_key(storage)
        mode_data = data.get(key, {})
        self.username_input.setText(mode_data.get("username", ""))
        self.password_input.setText(self._dec(mode_data.get("password", "")))

    def _save_mode_credentials(
        self, storage: str, username: str, password: str
    ) -> None:
        data = self._read_all_credentials()
        key = self._mode_key(storage)
        bucket = data.get(key, {})
        bucket.update({"username": username, "password": self._enc(password)})
        data[key] = bucket
        self._write_all_credentials(data)

    def on_storage_changed(self, text: str) -> None:
        sel = text.strip().lower()
        is_local = sel == "local"
        is_cloud = sel == "cloud"

        # Toggle field visibility
        self.share_input.setVisible(is_local)
        self.share_label.setVisible(is_local)
        self.server_input.setVisible(is_local)  # hide server for cloud; auto-computed
        self.server_label.setVisible(is_local)

        # Load last-used credentials for this mode
        self._load_mode_credentials(text)

        # Persist default mode
        data = self._read_all_credentials()
        data["default_mode"] = "cloud" if is_cloud else "local"
        # For cloud, compute and persist base_url immediately based on current username
        if is_cloud:
            data.setdefault("cloud", {})["base_url"] = self._compute_base_url()
        self._write_all_credentials(data)

    def on_username_changed(self, _: str) -> None:
        # If in cloud mode, refresh base_url on username edits
        sel = self.storage_input.currentText().strip().lower()
        if sel in {"owncloud", "cloud", "webdav"}:
            data = self._read_all_credentials()
            data.setdefault("cloud", {})["base_url"] = self._compute_base_url()
            self._write_all_credentials(data)

    def _compute_base_url(self) -> str:
        username = self.username_input.text().strip()
        if not username:
            return "http://95.111.226.24:81/remote.php/dav/files/"
        return f"http://95.111.226.24:81/remote.php/dav/files/{username}/"

    def _persist_current_to_credentials(self) -> None:
        """Persist current inputs into credentials.json under the selected mode."""
        data = self._read_all_credentials()
        mode = self._mode_key(self.storage_input.currentText())
        if mode == "local":
            data.setdefault("local", {}).update(
                {
                    "server": self.server_input.text().strip(),
                    "share": self.share_input.text().strip(),
                    "username": self.username_input.text().strip(),
                    "password": self._enc(self.password_input.text()),
                }
            )
            data["default_mode"] = "local"
        else:
            data.setdefault("cloud", {}).update(
                {
                    "base_url": self._compute_base_url(),
                    "username": self.username_input.text().strip(),
                    "password": self._enc(self.password_input.text()),
                }
            )
            data["default_mode"] = "cloud"
        self._write_all_credentials(data)

    def load_config(self) -> None:
        # Populate from unified credentials file
        all_data = self._read_all_credentials()
        mode = str(all_data.get("default_mode", "local")).strip().lower()
        self.storage_input.setCurrentIndex(0 if mode in {"local", "smb", "nas"} else 1)
        # Load fields for the current mode
        if mode == "cloud":
            c = all_data.get("cloud", {})
            self.server_input.setText(c.get("base_url", ""))
            self.share_input.setText("")
            self.username_input.setText(c.get("username", ""))
            self.password_input.setText(self._dec(c.get("password", "")))
        else:
            c = all_data.get("local", {})
            self.server_input.setText(c.get("server", ""))
            self.share_input.setText(c.get("share", ""))
            self.username_input.setText(c.get("username", ""))
            self.password_input.setText(self._dec(c.get("password", "")))

        self.on_storage_changed(self.storage_input.currentText())

    def save_config(self, info: Dict[str, str]) -> None:
        # Backed by unified credentials file now; keep for backward compatibility no-op
        try:
            self._persist_current_to_credentials()
        except Exception:
            logger.exception("Failed to save credentials")

    def on_connect(self) -> None:
        # Full info for callback (tests expect only these 4 keys)
        info = {
            "server": self.server_input.text(),
            "share": self.share_input.text(),
            "username": self.username_input.text(),
            "password": self.password_input.text(),
        }
        self.save_config(info)

        # Persist to unified credentials and normalize info for callback
        sel = self.storage_input.currentText().strip().lower()
        if sel == "cloud":
            base_url = self._compute_base_url()
            info["server"] = base_url
        # Save credentials for the selected storage mode
        self._save_mode_credentials(
            self.storage_input.currentText(), info["username"], info["password"]
        )
        # Also persist server/share for local mode
        if sel != "cloud":
            data = self._read_all_credentials()
            data.setdefault("local", {}).update(
                {"server": info.get("server", ""), "share": info.get("share", "")}
            )
            data["default_mode"] = "local"
            self._write_all_credentials(data)
        self.callback(info)

    # ---- startup helpers ----
    def try_auto_connect_on_startup(self) -> bool:
        """Attempt to connect automatically if we have a prior valid config.

        Returns True if a connection attempt was triggered.
        """
        storage = (self.storage_input.currentText() or "").strip().lower()
        server = (self.server_input.text() or "").strip()
        share = (self.share_input.text() or "").strip()
        username = (self.username_input.text() or "").strip()
        password = (self.password_input.text() or "").strip()

        # Basic guards: need credentials at minimum
        if not username or not password:
            return False

        if storage == "cloud":
            # Server (base_url) will be computed inside on_connect
            self.on_connect()
            return True

        # Local/SMB requires server and share
        if server and share:
            self.on_connect()
            return True

        return False
