import os
import json
from typing import Dict, Any, List
from datetime import datetime
from PySide6.QtCore import Qt, QObject, Signal, QThread, QDir, QFileInfo
from PySide6.QtWidgets import (
    QFileDialog,
    QLabel,
    QProgressBar,
    QTreeView,
    QTreeWidget,
    QTreeWidgetItem,
    QFileIconProvider,
    QFileSystemModel,
    QMessageBox,
    QHeaderView,
    QVBoxLayout,
    QWidget,
)
from vault.services.storage_interface import get_backend as _get_backend
from vault.services.storage_interface import upload_file as _storage_upload
from vault.services.storage_interface import download_file as _storage_download

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
    # Emitted whenever selection changes in the native FS view; carries the absolute path
    selection_changed = Signal(str)
    # Emitted whenever the current cloud path changes; carries normalized path like 'user/docs' or '' for root
    path_changed = Signal(str)
    # Emitted when back/forward availability changes
    nav_state_changed = Signal(bool, bool)
    # Back-compat exposed alias used by Explorer and tests
    file_list: QTreeWidget

    def __init__(self, session_info: Dict[str, str], async_load: bool = False) -> None:
        super().__init__()
        self.session_info = session_info
        self.selected_path: str | None = None
        # Keep the root handle so we can optionally query extra metadata (e.g., modified)
        self._root_handle: Any | None = None
        self._loading: bool = False
        self._loader_thread: QThread | None = None
        self._loader_worker: _LoaderWorker | None = None
        self._ever_loaded_ok: bool = False
        # Cloud navigation state
        self._current_path: str = ""  # normalized without leading/trailing slash
        self._history: list[str] = []
        self._history_index: int = -1
        self._pending_nav_target: str | None = None
        self._pending_nav_mode: str | None = (
            None  # 'initial' | 'push' | 'back' | 'forward'
        )
        # Optional native filesystem model/view for local NAS browsing
        self._use_native_fs: bool = bool(
            self.session_info.get("use_qfilesystem_model") or False
        )
        self.fs_model: QFileSystemModel | None = None
        self.fs_view: QTreeView | None = None
        self._fs_click_connected = False
        # Icon provider for QTreeWidget items (cloud view)
        self._icon_provider = QFileIconProvider()
        self.init_ui()

    def init_ui(self) -> None:
        self.main_layout = QVBoxLayout()
        # Remove outer padding/margins around the tree widget area
        self.main_layout.setContentsMargins(0, 0, 0, 0)
        self.main_layout.setSpacing(0)

        # Status label indicates whether there is data to display
        self.status_label = QLabel("Loading…")

        # Progress bar for loading state (indeterminate)
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 0)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setFixedHeight(4)
        self.progress_bar.setVisible(False)

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
        self.file_tree.itemDoubleClicked.connect(self.on_item_double_clicked)

        # Optional native FS view (hidden by default)
        self.fs_view = QTreeView()
        self.fs_view.setVisible(False)
        # Add padding for local files tree view items
        self.fs_view.setStyleSheet(
            "QTreeView { margin: 0; padding: 0; } QTreeView::item { padding: 4.5px; }"
        )
        # Only add the views and status (top bar removed)
        self.main_layout.addWidget(self.file_tree)
        self.main_layout.addWidget(self.fs_view)
        self.main_layout.addWidget(self.progress_bar)
        self.main_layout.addWidget(self.status_label)
        self.setLayout(self.main_layout)
        self.load_files()

    def _show_loading(self, on: bool) -> None:
        self._loading = on
        if on:
            self.status_label.setText("Loading…")
        try:
            if hasattr(self, "progress_bar") and self.progress_bar is not None:
                self.progress_bar.setVisible(on)
        except Exception:
            pass

    def _update_status(self) -> None:
        # If native fs is active, show selection or root path
        try:
            if (
                self._use_native_fs
                and self.fs_view is not None
                and self.fs_view.isVisible()
            ):
                if self.selected_path:
                    self.status_label.setText(self.selected_path)
                else:
                    self.status_label.setText(self._build_unc_root())
                return
        except Exception:
            pass
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
            # Seed cloud path before populating so item metadata builds correct full paths
            try:
                if self.session_info.get("storage", "local").strip().lower() == "cloud":
                    seed = self._normalize_cloud_path(
                        str(
                            self.session_info.get("current_path")
                            or self.session_info.get("path")
                            or self.session_info.get("cwd")
                            or self.session_info.get("dir")
                            or ""
                        )
                    )
                    self._current_path = seed
            except Exception:
                pass
            self.file_tree.clear()
            self._populate_files(files)
            # Deselect any previously selected item after contents load
            try:
                self.file_tree.clearSelection()
            except Exception:
                pass
            self.selected_path = None
            self._update_status()
            self._ever_loaded_ok = True
            # Initialize cloud navigation history/state on first successful load
            try:
                if self.session_info.get("storage", "local").strip().lower() == "cloud":
                    # Seed current path from session_info if provided
                    seed = self._normalize_cloud_path(
                        str(
                            self.session_info.get("current_path")
                            or self.session_info.get("path")
                            or self.session_info.get("cwd")
                            or self.session_info.get("dir")
                            or ""
                        )
                    )
                    self._current_path = seed
                    self._history = [seed]
                    self._history_index = 0
                    self.path_changed.emit(self._current_path)
                    self.nav_state_changed.emit(
                        self.can_go_back(), self.can_go_forward()
                    )
            except Exception:
                pass
        finally:
            self._show_loading(False)

    def _on_load_error(self, message: str):
        try:
            self._handle_load_error(message)
        finally:
            self._show_loading(False)

    def load_files(self) -> bool:
        # If using native filesystem model for local NAS browsing, set it up and skip backend
        try:
            if self._use_native_fs and (
                self.session_info.get("storage", "local").strip().lower() != "cloud"
            ):
                self._setup_native_fs_view()
                return True
        except Exception:
            # Fall back to default behavior
            pass

        # Under pytest, use a synchronous path to keep legacy tests deterministic
        try:
            if os.environ.get("PYTEST_CURRENT_TEST"):
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
                if storage != "cloud" and not (s.get("server") and s.get("share")):
                    if not self._ever_loaded_ok:
                        self.file_tree.clear()
                        self._update_status()
                        self.status_label.setText("Not connected")
                    return False
                try:
                    root, files = self._fetch_files()
                    self._root_handle = root
                    self.file_tree.clear()
                    self._populate_files(files)
                    # Set initial cloud nav state synchronously as well
                    try:
                        if storage == "cloud":
                            seed = self._normalize_cloud_path(
                                str(
                                    s.get("current_path")
                                    or s.get("path")
                                    or s.get("cwd")
                                    or s.get("dir")
                                    or ""
                                )
                            )
                            self._current_path = seed
                            self._history = [seed]
                            self._history_index = 0
                            self.path_changed.emit(self._current_path)
                            self.nav_state_changed.emit(
                                self.can_go_back(), self.can_go_forward()
                            )
                    except Exception:
                        pass
                    return True
                except Exception as e:
                    self._handle_load_error(str(e))
                    return False
        except Exception:
            # If detection fails, default to async path below
            pass

        return self.load_files_async()

    def _setup_native_fs_view(self, root_path: str | None = None) -> None:
        """Initialize and display a QFileSystemModel rooted at the NAS UNC path.

        If root_path is None, it will be built from session_info server/share/current_path.
        """
        # Lazily create model
        if self.fs_model is None:
            self.fs_model = QFileSystemModel(self)
            # Show files and dirs, hide "." and ".."
            self.fs_model.setFilter(
                self.fs_model.filter()
                | QDir.Filter.AllEntries
                | QDir.Filter.NoDotAndDotDot
            )
        if self.fs_view is None:
            self.fs_view = QTreeView(self)
            self.main_layout.insertWidget(0, self.fs_view)

        # Determine root UNC path
        unc = root_path or self._build_unc_root()
        # setRootPath can raise for invalid UNC, but we treat both paths equally
        self.fs_model.setRootPath(unc)
        self.fs_view.setModel(self.fs_model)
        self.fs_view.setRootIndex(self.fs_model.index(unc))
        # Columns similar to QTreeWidget version
        hdr = self.fs_view.header()
        hdr.setStretchLastSection(False)
        try:
            hdr.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
            hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        except Exception:
            pass
        # Selection handling: connect once to avoid duplicate connections
        if not self._fs_click_connected:
            self.fs_view.clicked.connect(self._on_fs_selected)
            self._fs_click_connected = True

        # Toggle visibility
        self.file_tree.setVisible(False)
        self.fs_view.setVisible(True)
        # Update status
        self.status_label.setText(unc)

    def _build_unc_root(self) -> str:
        s = self.session_info or {}
        server = (s.get("server") or "").strip().lstrip("\\")
        share = (s.get("share") or "").strip().strip("\\/")
        # Optional sub-path within the share
        sub = (
            s.get("current_path") or s.get("path") or s.get("cwd") or s.get("dir") or ""
        )
        sub = str(sub or "").replace("/", "\\").strip("\\")
        parts = [p for p in [server, share, sub] if p]
        if server.startswith("\\"):
            base = server
        else:
            base = f"\\\\{server}" if server else "\\\\"
        if parts:
            return (
                base
                + ("\\" if not base.endswith("\\") else "")
                + "\\".join([p for p in parts if p and p != server])
            )
        return base

    def use_qfilesystem_model(
        self, enable: bool = True, root_path: str | None = None
    ) -> None:
        """Public toggle to switch to native QFileSystemModel view for local browsing."""
        self._use_native_fs = bool(enable)
        if enable:
            self._setup_native_fs_view(root_path)
        else:
            # Switch back to legacy view
            if self.fs_view is not None:
                self.fs_view.setVisible(False)
            self.file_tree.setVisible(True)

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
            # For cloud, 'path' from backend may be just a name; normalize display name
            display_name = str(f.get("name") or name)
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
            item = QTreeWidgetItem([display_name, hr, ftype, mod_str])
            # Set OS-style icon via QFileIconProvider
            try:
                if is_dir:
                    icon = self._icon_provider.icon(QFileIconProvider.IconType.Folder)
                else:
                    # Try extension-based icon; fallback to generic File
                    icon = self._icon_provider.icon(QFileInfo(name))
                    if icon.isNull():
                        icon = self._icon_provider.icon(QFileIconProvider.IconType.File)
                item.setIcon(0, icon)
            except Exception:
                # Best effort; ignore if icon resolution fails
                pass
            # Store raw metadata for later use on column 0
            # Store raw metadata plus a computed full path for cloud navigation
            try:
                meta = dict(f)
            except Exception:
                meta = {
                    "name": display_name,
                    "path": name,
                    "is_dir": "true" if is_dir else "false",
                }
            # Compute full relative path for cloud selections
            try:
                if self.session_info.get("storage", "local").strip().lower() == "cloud":
                    full_path = self._join_cloud_path(self._current_path, display_name)
                    meta["full_path"] = full_path
            except Exception:
                pass
            item.setData(0, Qt.ItemDataRole.UserRole, meta)
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
        # For cloud, prefer full_path to ensure downloads use the correct folder
        if (
            isinstance(data, dict)
            and self.session_info.get("storage", "local").strip().lower() == "cloud"
            and (data.get("full_path") or data.get("path"))
        ):
            self.selected_path = str(data.get("full_path") or data.get("path"))
        elif isinstance(data, dict) and "path" in data:
            self.selected_path = data["path"]
        else:
            # Fallback: use the Name column directly
            self.selected_path = item.text(0)
        self._update_status()

    def on_item_double_clicked(self, item, _column=None) -> None:
        """If a folder is double-clicked in cloud mode, navigate into it."""
        try:
            if self.session_info.get("storage", "local").strip().lower() != "cloud":
                return
            data = item.data(0, Qt.ItemDataRole.UserRole)
            name = None
            is_dir = False
            if isinstance(data, dict):
                name = str(data.get("name") or data.get("path") or item.text(0))
                is_dir = str(data.get("is_dir", "false")).lower() == "true"
            else:
                name = item.text(0)
            if is_dir and name:
                target = self._join_cloud_path(self._current_path, name)
                self._navigate_to(target, mode="push")
        except Exception:
            pass

    def _on_fs_selected(self, index) -> None:
        try:
            if self.fs_model is None:
                return
            path = self.fs_model.filePath(index)
            self.selected_path = path
            try:
                self.selection_changed.emit(path)
            except Exception:
                pass
            self._update_status()
        except Exception:
            pass

    def download_selected_file(self) -> None:
        if not self.selected_path:
            return
        local_path, _ = QFileDialog.getSaveFileName(
            self, "Save File As", self.selected_path
        )
        if local_path:
            try:
                if self._use_native_fs and (
                    self.session_info.get("storage", "local").strip().lower() != "cloud"
                ):
                    # Native copy from NAS to local destination
                    import shutil

                    shutil.copy2(self.selected_path, local_path)
                else:
                    # For cloud, selected_path is already a full relative path
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

    # -------- Cloud navigation helpers --------
    def _normalize_cloud_path(self, p: str) -> str:
        s = (p or "").strip().replace("\\", "/")
        # remove leading/trailing slashes
        s = s.strip("/")
        return s

    def _join_cloud_path(self, base: str, name: str) -> str:
        b = self._normalize_cloud_path(base)
        n = self._normalize_cloud_path(name)
        if not b:
            return n
        if not n:
            return b
        return f"{b}/{n}"

    def _start_loader_for_path(self, target_path: str) -> None:
        """Start async loading of a specific cloud path using existing client."""
        # Guard
        if self._root_handle is None:
            return
        backend = _HANDLE_BACKENDS.get(id(self._root_handle))
        if backend is None:
            return
        # client is first element of handle for DAV backend
        client = None
        if isinstance(self._root_handle, tuple) and self._root_handle:
            client = self._root_handle[0]
        else:
            client = self._root_handle

        def fetch():
            handle = (client, target_path)
            files = backend.list_files(handle)
            return self._root_handle, files

        # Setup worker thread
        self._show_loading(True)
        self._loader_thread = QThread(self)
        self._loader_worker = _LoaderWorker(fetch)
        self._loader_worker.moveToThread(self._loader_thread)
        self._loader_thread.started.connect(self._loader_worker.run)
        self._loader_worker.finished.connect(self._on_nav_load_finished)
        self._loader_worker.error.connect(self._on_load_error)
        self._loader_worker.finished.connect(self._loader_thread.quit)
        self._loader_worker.error.connect(self._loader_thread.quit)
        self._loader_thread.finished.connect(self._cleanup_loader)
        self._loader_thread.start()

    def _on_nav_load_finished(self, root, files):
        try:
            # root remains the same
            # Determine the target path now and set as current so metadata uses it
            pending_t = self._pending_nav_target
            mode = self._pending_nav_mode or "push"
            if pending_t is not None:
                t = self._normalize_cloud_path(pending_t)
                # Update current path early for correct meta['full_path']
                self._current_path = t
                try:
                    self.session_info["current_path"] = t
                except Exception:
                    pass
            self.file_tree.clear()
            self._populate_files(files)
            # Deselect any item after navigating into a folder
            try:
                self.file_tree.clearSelection()
            except Exception:
                pass
            self.selected_path = None
            self._update_status()
            # Commit nav state
            if pending_t is not None:
                t = self._normalize_cloud_path(pending_t)
                if mode == "push":
                    # Truncate forward history and append
                    if 0 <= self._history_index < len(self._history) - 1:
                        self._history = self._history[: self._history_index + 1]
                    self._history.append(t)
                    self._history_index = len(self._history) - 1
                elif mode == "back":
                    self._history_index = max(0, self._history_index - 1)
                elif mode == "forward":
                    self._history_index = min(
                        len(self._history) - 1, self._history_index + 1
                    )
                elif mode == "reload":
                    # Don't modify history for reload operations
                    pass
                # Notify listeners
                self.path_changed.emit(self._current_path)
                self.nav_state_changed.emit(self.can_go_back(), self.can_go_forward())
        finally:
            self._pending_nav_target = None
            self._pending_nav_mode = None
            self._show_loading(False)

    def _navigate_to(self, target_path: str, mode: str = "push") -> None:
        """Navigate to a cloud path. mode: 'push' | 'back' | 'forward' | 'reload'"""
        if self.session_info.get("storage", "local").strip().lower() != "cloud":
            return
        t = self._normalize_cloud_path(target_path)
        if t == self._current_path and mode == "push":
            return
        # Record pending and fetch
        self._pending_nav_target = t
        self._pending_nav_mode = mode
        self._start_loader_for_path(t)

    # Public navigation API for Explorer
    def can_go_back(self) -> bool:
        return self._history_index > 0

    def can_go_forward(self) -> bool:
        return 0 <= self._history_index < len(self._history) - 1

    def go_back(self) -> None:
        if not self.can_go_back():
            return
        target = self._history[self._history_index - 1]
        self._pending_nav_target = target
        self._pending_nav_mode = "back"
        self._start_loader_for_path(target)

    def go_forward(self) -> None:
        if not self.can_go_forward():
            return
        target = self._history[self._history_index + 1]
        self._pending_nav_target = target
        self._pending_nav_mode = "forward"
        self._start_loader_for_path(target)
