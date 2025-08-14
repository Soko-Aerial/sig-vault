from typing import Dict, Any, List
import os
import json
from PySide6.QtCore import Qt
from datetime import datetime
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QTreeWidget,
    QTreeWidgetItem,
    QMessageBox,
    QPushButton,
    QHeaderView,
    QProgressBar,
    QVBoxLayout,
    QWidget,
)
from src.services.storage_interface import download_file as _storage_download
from src.services.storage_interface import get_backend as _get_backend
from src.services.storage_interface import upload_file as _storage_upload

# Backward-compatible shims for existing tests that monkeypatch these names
_HANDLE_BACKENDS: dict[int, Any] = {}


def connect_to_smb_share(**session_info):  # type: ignore[override]
    # Storage selection expected in session_info; if absent, derive from credentials.json
    storage = (session_info.get("storage") or "").strip().lower()
    if not storage:
        try:
            with open(os.path.join(".sig", "credentials.json"), "r") as f:
                data = json.load(f)
                storage = str(data.get("default_mode", "local")).strip().lower()
        except Exception:
            storage = "local"
        session_info = {**session_info, "storage": storage}

    # If cloud and server is empty, load persisted base_url from credentials.json
    if (session_info.get("storage") == "cloud") and not session_info.get("server"):
        try:
            with open(os.path.join(".sig", "credentials.json"), "r") as f:
                creds = json.load(f)
                base = (creds.get("cloud", {}) or {}).get("base_url")
                if base:
                    session_info = {**session_info, "server": base}
        except Exception:
            pass

    backend = _get_backend(session_info)
    handle = backend.connect(session_info)
    try:
        _HANDLE_BACKENDS[id(handle)] = backend
    except Exception:
        pass
    return handle


def list_files_in_directory(handle) -> List[Dict[str, Any]]:  # type: ignore[override]
    backend = _HANDLE_BACKENDS.get(id(handle))
    if backend is None:
        # Likely overridden in tests; or handle unknown
        raise RuntimeError(
            "Unknown handle; list_files_in_directory should be patched in tests"
        )
    return backend.list_files(handle)


def download_file(session_info: dict, remote_path: str, local_path: str) -> None:
    _storage_download(session_info, remote_path, local_path)


def upload_file(session_info: dict, local_path: str) -> None:
    _storage_upload(session_info, local_path)


class FileExplorer(QWidget):
    def __init__(self, session_info: Dict[str, str]) -> None:
        super().__init__()
        self.session_info = session_info
        self.selected_path: str | None = None
        # Keep the root handle so we can optionally query extra metadata (e.g., modified)
        self._root_handle: Any | None = None
        # Loading state (no threads)
        self._loading: bool = False
        self.init_ui()

    def init_ui(self) -> None:
        self.main_layout = QVBoxLayout()
        # Remove outer padding/margins around the tree widget area
        self.main_layout.setContentsMargins(0, 0, 0, 0)
        self.main_layout.setSpacing(0)

        self.top_bar = QHBoxLayout()
        self.top_bar.setContentsMargins(0, 0, 0, 0)
        self.top_bar.setSpacing(0)

        self.path_label = QLabel(self._compute_path_label())
        self.upload_btn = QPushButton("Upload File")
        self.upload_btn.clicked.connect(self.upload_file)

        self.top_bar.addWidget(self.path_label)
        # Busy indicator for async loads
        self.loading_bar = QProgressBar()
        self.loading_bar.setRange(0, 0)  # indeterminate
        self.loading_bar.setFixedHeight(16)
        self.loading_bar.setTextVisible(False)
        self.loading_bar.hide()
        self.top_bar.addWidget(self.loading_bar)
        self.top_bar.addWidget(self.upload_btn)
        # self.top_bar.addWidget(self.download_btn)

        # Status label indicates whether there is data to display
        self.status_label = QLabel("Loading…")
        self.status_label.setStyleSheet("color: #666;")

        self.file_tree = QTreeWidget()
        self.file_tree.setHeaderLabels(["Name", "Size", "Type", "Date modified"])
        self.file_tree.setSortingEnabled(True)
        # Remove any visual padding around and inside the tree items
        self.file_tree.setStyleSheet(
            "QTreeWidget { margin: 0; padding: 0; } QTreeWidget::item { padding: 4.5px; }"
        )
        # Back-compat: some tests expect 'file_list' attribute
        self.file_list = self.file_tree

        header = self.file_tree.header()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        # Name column: stretch
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        # Name column: back to interactive
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)

        # QTreeWidget emits (item, column)
        self.file_tree.itemClicked.connect(self.on_item_selected)

        self.main_layout.addLayout(self.top_bar)
        self.main_layout.addWidget(self.file_tree)
        self.main_layout.addWidget(self.status_label)
        self.setLayout(self.main_layout)
        # Populate content and status
        self.load_files()

    def _show_loading(self, on: bool) -> None:
        self._loading = on
        self.loading_bar.setVisible(on)
        self.upload_btn.setEnabled(not on)
        if on:
            self.status_label.setText("Loading…")

    def _update_status(self) -> None:
        # Count items directly from QTreeWidget
        count = self.file_tree.topLevelItemCount()
        if count == 0:
            self.status_label.setText("No files to display")
        else:
            # If a selection exists, show selection details; else show count
            if self.selected_path:
                try:
                    item = self.file_tree.currentItem()
                    meta = item.data(0, Qt.ItemDataRole.UserRole) if item else None
                    if isinstance(meta, dict):
                        size_val = meta.get("size", "")
                        is_dir = str(meta.get("is_dir", "false")).lower() == "true"
                        if is_dir:
                            size_str = "Folder"
                        else:
                            try:
                                sz = int(size_val)
                                if sz >= 1024 * 1024:
                                    size_str = f"{sz / (1024 * 1024):.1f} MB"
                                elif sz >= 1024:
                                    size_str = f"{sz / 1024:.1f} KB"
                                else:
                                    size_str = f"{sz} B"
                            except Exception:
                                size_str = str(size_val)
                        self.status_label.setText(
                            f"{count} item{'' if count == 1 else 's'} | 1 item selected | {size_str}"
                        )
                        return
                except Exception:
                    pass
            self.status_label.setText(f"{count} item{'' if count == 1 else 's'}")

    def load_files(self) -> bool:
        # Don't attempt to connect if credentials are obviously missing
        try:
            s = self.session_info or {}
            storage = (s.get("storage") or "local").strip().lower()
            user = (s.get("username") or "").strip()
            pwd = (s.get("password") or "").strip()
            if not user or not pwd:
                # Treat as not connected yet
                self.file_tree.clear()
                self._update_status()
                self.status_label.setText("Not connected")
                return False
            if storage != "cloud":
                # For local, also require server and share
                if not (s.get("server") and s.get("share")):
                    self.file_tree.clear()
                    self._update_status()
                    self.status_label.setText("Not connected")
                    return False

            # Proceed synchronously
            root = connect_to_smb_share(**self.session_info)
            # Save handle for optional metadata lookups
            self._root_handle = root
            files: list[Dict[str, Any]] = list_files_in_directory(root)

            self.file_tree.clear()
            self._populate_files(files)
        except Exception as e:
            message = str(e)
            # Provide additional guidance for common DAV auth errors
            if any(
                x in message for x in ["401", "NotAuthenticated", "forbidden", "403"]
            ):
                message += (
                    "\n\nTip: Double-check your cloud username, password, and base URL."
                )
            QMessageBox.critical(self, "Error", message)
            # Reflect failure in status label and clear any stale list content
            self.file_tree.clear()
            self.status_label.setText("Failed to load files")
            return False
        return True

    def _populate_files(self, files: list[Dict[str, Any]]) -> None:
        def _format_modified(val: Any) -> str:
            try:
                if val is None:
                    return ""
                # If numeric (epoch seconds or ms)
                if isinstance(val, (int, float)):
                    ts = float(val)
                    # Heuristic: if ts is likely in ms, convert to seconds
                    if ts > 10_000_000_000:  # > ~Sat Nov 20 2286 UTC in seconds
                        ts = ts / 1000.0
                    dt = datetime.fromtimestamp(ts)
                    return dt.strftime("%Y-%m-%d %H:%M")
                # If string, try ISO8601
                if isinstance(val, str):
                    s = val.strip()
                    # Try integer string
                    if s.isdigit():
                        return _format_modified(int(s))
                    # Handle trailing Z
                    try:
                        iso = s.replace("Z", "+00:00")
                        dt = datetime.fromisoformat(iso)
                        # Normalize to local time for display
                        if dt.tzinfo is not None:
                            dt = dt.astimezone().replace(tzinfo=None)
                        return dt.strftime("%Y-%m-%d %H:%M")
                    except Exception:
                        return s
            except Exception:
                return ""
            return ""

        # Try to get a DAV client's low-level info() if available (cloud mode)
        dav_info = None
        try:
            if (
                isinstance(self._root_handle, tuple)
                and self.session_info.get("storage", "").strip().lower() == "cloud"
            ):
                root_client = self._root_handle[0]
                low = getattr(root_client, "client", None)
                if low is not None and callable(getattr(low, "info", None)):
                    dav_info = low  # webdav3 client
        except Exception:
            dav_info = None

        for f in files:
            size = f.get("size", "-")
            is_dir = str(f.get("is_dir", "false")).lower() == "true"
            name = f.get("path") or f.get("name") or ""
            # Pick a modified field if present
            mod_raw = (
                f.get("modified")
                or f.get("last_modified")
                or f.get("mtime")
                or f.get("modified_time")
                or f.get("date_modified")
                or f.get("updated_at")
            )
            # If missing and DAV info available, try to fetch it (best-effort, non-fatal)
            if not mod_raw and dav_info is not None and name:
                try:
                    info = dav_info.info(name)
                    if isinstance(info, dict):
                        mod_raw = (
                            info.get("modified")
                            or info.get("last_modified")
                            or info.get("mtime")
                            or info.get("updated_at")
                            or info.get("date")
                        )
                except Exception:
                    pass
            # Human-readable size
            if is_dir:
                hr = ""
                ftype = "Folder"
                mod_str = _format_modified(mod_raw) if mod_raw else ""
            else:
                try:
                    sz_int = int(size)
                    if sz_int >= 1024 * 1024:
                        hr = f"{sz_int / (1024 * 1024):.1f} MB"
                    elif sz_int >= 1024:
                        hr = f"{sz_int / 1024:.1f} KB"
                    else:
                        hr = f"{sz_int} B"
                except Exception:
                    hr = str(size)
                ftype = "File"
                mod_str = _format_modified(mod_raw)
            item = QTreeWidgetItem([name, hr, ftype, mod_str])
            # Store raw metadata for later use on column 0
            item.setData(0, Qt.ItemDataRole.UserRole, f)
            self.file_tree.addTopLevelItem(item)
        self._update_status()

    def _compute_path_label(self) -> str:
        # Hide base URL/UNC path for both cloud and local. Only show '/'
        # for root, or the folder currently being viewed if provided.
        # We look for common keys that might carry the current folder.
        possible_paths = [
            self.session_info.get("current_path"),
            self.session_info.get("path"),
            self.session_info.get("cwd"),
            self.session_info.get("dir"),
        ]
        for p in possible_paths:
            if p is None:
                continue
            s = str(p).strip()
            if s in ("", "/", "\\", "."):
                return "/"
            # Normalize to a leading slash and forward slashes
            s = s.replace("\\", "/")
            if not s.startswith("/"):
                s = "/" + s.lstrip("/")
            return s
        return "/"

    def _read_storage_selection(self) -> str:
        try:
            with open(os.path.join(".sig", "credentials.json"), "r") as f:
                data = json.load(f)
                return str(data.get("default_mode", "local")).strip().lower()
        except Exception:
            return "local"

    def on_item_selected(self, item, _column=None) -> None:
        # Retrieve raw entry dict if present
        try:
            data = item.data(0, Qt.ItemDataRole.UserRole)
        except Exception:
            data = None
        if isinstance(data, dict) and "path" in data:
            self.selected_path = data["path"]
        else:
            # Fallback: use the Name column directly
            self.selected_path = item.text(0)
        self._update_status()

    def download_selected_file(self) -> None:
        if not self.selected_path:
            return
        local_path, _ = QFileDialog.getSaveFileName(
            self, "Save File As", self.selected_path
        )
        if local_path:
            try:
                download_file(self.session_info, self.selected_path, local_path)
                QMessageBox.information(
                    self, "Success", "File downloaded successfully."
                )
            except Exception as e:
                QMessageBox.critical(self, "Error", str(e))

    def upload_file(self) -> None:
        file_path, _ = QFileDialog.getOpenFileName(self, "Select File to Upload")
        if file_path:
            try:
                upload_file(self.session_info, file_path)
                QMessageBox.information(self, "Success", "File uploaded successfully.")
                self.file_tree.clear()
                self.load_files()
                self._update_status()
            except Exception as e:
                QMessageBox.critical(self, "Error", str(e))
