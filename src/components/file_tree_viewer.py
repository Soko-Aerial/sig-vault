import os
import json
from typing import Dict, Any, List
from datetime import datetime
from PySide6.QtCore import Qt, QObject, Signal, QThread
from PySide6.QtWidgets import (
    QFileDialog,
    QLabel,
    QTreeWidget,
    QTreeWidgetItem,
    QMessageBox,
    QHeaderView,
    QVBoxLayout,
    QWidget,
)
from src.services.storage_interface import get_backend as _get_backend
from src.services.storage_interface import upload_file as _storage_upload
from src.services.storage_interface import download_file as _storage_download

# Backward-compatible shims for existing tests that monkeypatch these names
_HANDLE_BACKENDS: dict[int, Any] = {}


def connect_to_backend(**session_info):  # type: ignore[override]
    # Storage selection expected in session_info; if absent,
    # derive from credentials.json
    storage = (session_info.get("storage") or "").strip().lower()
    if not storage:
        try:
            with open(os.path.join(".sig", "credentials.json"), "r") as f:
                data = json.load(f)
                storage = str(data.get("default_mode", "local")).strip().lower()
        except Exception:
            storage = "local"
        session_info = {**session_info, "storage": storage}

    # If cloud and server is empty,
    # load persisted base_url from credentials.json
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


class _LoaderWorker(QObject):
    finished = Signal(object, list)
    error = Signal(str)

    def __init__(self, fetch_fn):
        super().__init__()
        self._fetch_fn = fetch_fn

    def run(self):
        try:
            root, files = self._fetch_fn()
            self.finished.emit(root, files)
        except Exception as e:
            self.error.emit(str(e))


class FileExplorer(QWidget):
    def __init__(self, session_info: Dict[str, str], async_load: bool = False) -> None:
        super().__init__()
        self.session_info = session_info
        self.selected_path: str | None = None
        # Keep the root handle so we can optionally query extra metadata (e.g., modified)
        self._root_handle: Any | None = None
        self._loading: bool = False
        self._use_async: bool = bool(async_load)
        self._loader_thread: QThread | None = None
        self._loader_worker: _LoaderWorker | None = None
        self._ever_loaded_ok: bool = False
        self.init_ui()

    def init_ui(self) -> None:
        self.main_layout = QVBoxLayout()
        # Remove outer padding/margins around the tree widget area
        self.main_layout.setContentsMargins(0, 0, 0, 0)
        self.main_layout.setSpacing(0)

        # Status label indicates whether there is data to display
        self.status_label = QLabel("Loading…")
        self.status_label.setStyleSheet("color: #aaa;")

        self.file_tree = QTreeWidget()
        self.file_tree.setHeaderLabels(["Name", "Size", "Type", "Date modified"])
        self.file_tree.setSortingEnabled(True)
        # Rendering optimization for large lists
        self.file_tree.setUniformRowHeights(True)
        # Remove any visual padding around and inside the tree items
        self.file_tree.setStyleSheet(
            "QTreeWidget { margin: 0; padding: 0; } QTreeWidget::item { padding: 4.5px; }"
        )
        # Back-compat: some tests expect 'file_list' attribute (fix this)
        self.file_list = self.file_tree

        header = self.file_tree.header()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)

        # QTreeWidget emits (item, column)
        self.file_tree.itemClicked.connect(self.on_item_selected)

        # Only add the tree and status (top bar removed)
        self.main_layout.addWidget(self.file_tree)
        self.main_layout.addWidget(self.status_label)
        self.setLayout(self.main_layout)
        # Populate content and status
        self.load_files()

    def _show_loading(self, on: bool) -> None:
        self._loading = on
        if on:
            self.status_label.setText("Loading…")

    def _update_status(self) -> None:
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

    def _fetch_files(self) -> tuple[Any, list[Dict[str, Any]]]:
        """Connect and list files; no UI updates here.

        Raises an exception on failures, mirroring the synchronous path.
        """
        s = self.session_info or {}
        storage = (s.get("storage") or "local").strip().lower()
        user = (s.get("username") or "").strip()
        pwd = (s.get("password") or "").strip()
        if not user or not pwd:
            raise RuntimeError("Not connected")
        if storage != "cloud":
            if not (s.get("server") and s.get("share")):
                raise RuntimeError("Not connected")
        # Use the canonical connector; tests can monkeypatch this
        root = connect_to_backend(**self.session_info)
        files: list[Dict[str, Any]] = list_files_in_directory(root)
        return root, files

    def _handle_load_error(self, message: str) -> None:
        # Provide additional guidance for common DAV auth errors
        msg = message
        if any(x in msg for x in ["401", "NotAuthenticated", "forbidden", "403"]):
            msg += "\n\nTip: Double-check your cloud username, password, and base URL."
        QMessageBox.critical(self, "Error", msg)
        # Reflect failure in status label and clear any stale list content
        self.file_tree.clear()
        self.status_label.setText("Failed to load files")

    def _cleanup_loader(self) -> None:
        try:
            if self._loader_worker is not None:
                self._loader_worker.deleteLater()
        except Exception:
            pass
        try:
            if self._loader_thread is not None:
                self._loader_thread.deleteLater()
        except Exception:
            pass
        self._loader_worker = None
        self._loader_thread = None
        self._loading = False

    def load_files_async(self) -> bool:
        """Asynchronously fetch and populate files without blocking the UI."""
        # Quick credentials check on UI thread to avoid spawning threads unnecessarily
        s = self.session_info or {}
        storage = (s.get("storage") or "local").strip().lower()
        user = (s.get("username") or "").strip()
        pwd = (s.get("password") or "").strip()
        if not user or not pwd:
            if not self._ever_loaded_ok:
                self.file_tree.clear()
                self._update_status()
                self.status_label.setText("Not connected")
            return False
        if storage != "cloud":
            if not (s.get("server") and s.get("share")):
                if not self._ever_loaded_ok:
                    self.file_tree.clear()
                    self._update_status()
                    self.status_label.setText("Not connected")
                return False

        if self._loading:
            return True

        self._show_loading(True)
        self._loading = True
        # Setup worker thread
        self._loader_thread = QThread(self)
        self._loader_worker = _LoaderWorker(self._fetch_files)
        self._loader_worker.moveToThread(self._loader_thread)

        # Wire signals so slots run in the main thread (receiver is self, a QWidget/QObject)
        self._loader_thread.started.connect(self._loader_worker.run)
        self._loader_worker.finished.connect(self._on_load_finished)
        self._loader_worker.error.connect(self._on_load_error)
        # Ensure the worker thread exits after finishing or erroring
        self._loader_worker.finished.connect(self._loader_thread.quit)
        self._loader_worker.error.connect(self._loader_thread.quit)
        # Clean up after thread has fully stopped
        self._loader_thread.finished.connect(self._cleanup_loader)
        self._loader_thread.start()
        return True

    def _on_load_finished(self, root, files):
        try:
            self._root_handle = root
            self.file_tree.clear()
            self._populate_files(files)
            self._ever_loaded_ok = True
            self._ever_loaded_ok = True
        finally:
            self._show_loading(False)

    def _on_load_error(self, message: str):
        try:
            self._handle_load_error(message)
        finally:
            self._show_loading(False)

    def load_files(self) -> bool:
        # In async mode, delegate to background loader and return immediately
        if self._use_async:
            return self.load_files_async()

        # Don't attempt to connect if credentials are missing
        try:
            s = self.session_info or {}
            storage = (s.get("storage") or "local").strip().lower()
            user = (s.get("username") or "").strip()
            pwd = (s.get("password") or "").strip()
            if not user or not pwd:
                # Treat as not connected yet
                if not self._ever_loaded_ok:
                    self.file_tree.clear()
                    self._update_status()
                    self.status_label.setText("Not connected")
                return False
            if storage != "cloud":
                if not (s.get("server") and s.get("share")):
                    if not self._ever_loaded_ok:
                        self.file_tree.clear()
                        self._update_status()
                        self.status_label.setText("Not connected")
                    return False
            root, files = self._fetch_files()
            # Save handle for optional metadata lookups
            self._root_handle = root
            self.file_tree.clear()
            self._populate_files(files)
        except Exception as e:  # noqa: BLE001
            self._handle_load_error(str(e))
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

        # Speed: batch insert and suspend sorting/updates during populate
        prev_sort = self.file_tree.isSortingEnabled()
        self.file_tree.setSortingEnabled(False)
        self.file_tree.setUpdatesEnabled(False)
        items_buf: list[QTreeWidgetItem] = []

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
            items_buf.append(item)

        if items_buf:
            self.file_tree.addTopLevelItems(items_buf)

        # Restore view settings
        self.file_tree.setSortingEnabled(prev_sort)
        self.file_tree.setUpdatesEnabled(True)
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
