from typing import Dict, Any
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QListWidget,
    QLabel,
    QPushButton,
    QFileDialog,
    QMessageBox,
    QHBoxLayout,
)
from services.smb.client import (
    connect_to_smb_share,
    list_files_in_directory,
    download_file,
    upload_file,
)


class FileExplorer(QWidget):
    def __init__(self, session_info: Dict[str, str]) -> None:
        super().__init__()
        self.session_info = session_info
        self.selected_path: str | None = None
        self.init_ui()

    def init_ui(self) -> None:
        self.main_layout = QVBoxLayout()
        self.top_bar = QHBoxLayout()

        self.path_label = QLabel(
            f"\\\\{self.session_info.get('server')}\\{self.session_info.get('share')}"
        )
        self.upload_btn = QPushButton("Upload File")
        self.upload_btn.clicked.connect(self.upload_file)

        self.top_bar.addWidget(self.path_label)
        self.top_bar.addWidget(self.upload_btn)

        self.file_list = QListWidget()
        self.file_list.setStyleSheet("""
            QListWidget {
                font-size: 13px;
            }
            QListWidget::item {
                padding: 6.5px;
            }
        """)
        self.file_list.itemClicked.connect(self.on_item_selected)

        self.download_btn = QPushButton("Download Selected File")
        self.download_btn.setVisible(False)
        self.download_btn.clicked.connect(self.download_selected_file)

        if self.load_files():
            self.main_layout.addLayout(self.top_bar)
            self.main_layout.addWidget(self.file_list)
            self.main_layout.addWidget(self.download_btn)
            self.setLayout(self.main_layout)

    def load_files(self) -> bool:
        try:
            root = connect_to_smb_share(**self.session_info)
            files: list[Dict[str, Any]] = list_files_in_directory(root)
            from PySide6.QtWidgets import (
                QListWidgetItem,
            )  # local import to avoid circular

            for f in files:
                size = f.get("size", "-")
                is_dir = f.get("is_dir", "false")
                label = f["path"]
                if is_dir == "true":
                    display = f"[DIR] {label}"
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
                        hr = size
                    display = f"{label} ({hr})"
                item = QListWidgetItem(display)
                # Store raw metadata for later use
                item.setData(Qt.ItemDataRole.UserRole, f)
                self.file_list.addItem(item)
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))
            return False
        return True

    def on_item_selected(self, item) -> None:
        # Retrieve raw entry dict if present
        data = item.data(Qt.ItemDataRole.UserRole)
        if isinstance(data, dict) and "path" in data:
            self.selected_path = data["path"]
        else:
            # Fallback: try to parse decorated text
            text = item.text()
            if text.startswith("[DIR] "):
                self.selected_path = text[6:]
            else:
                self.selected_path = text.split(" (")[0]
        self.download_btn.setVisible(True)

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
                self.file_list.clear()
                self.load_files()
            except Exception as e:
                QMessageBox.critical(self, "Error", str(e))
