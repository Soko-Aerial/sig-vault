import json
import os
from PySide6.QtWidgets import QWidget, QFormLayout, QLineEdit, QPushButton
from PySide6.QtCore import Signal
from typing import Callable, Dict

CONFIG_PATH = ".sig/connection.json"

class ConnectionForm(QWidget):
    connected: Signal = Signal(dict)

    def __init__(self, callback: Callable[[Dict[str, str]], None]) -> None:
        super().__init__()
        self.callback = callback
        self.init_ui()
        self.load_config()

    def init_ui(self) -> None:
        self.server_input = QLineEdit()
        self.share_input = QLineEdit()
        self.username_input = QLineEdit()
        self.password_input = QLineEdit()
        self.connect_btn = QPushButton("Connect")

        layout = QFormLayout()
        layout.addRow("Server", self.server_input)
        layout.addRow("Share", self.share_input)
        layout.addRow("Username", self.username_input)
        layout.addRow("Password", self.password_input)
        layout.addWidget(self.connect_btn)

        self.connect_btn.clicked.connect(self.on_connect)
        self.setLayout(layout)

    def load_config(self) -> None:
        if os.path.exists(CONFIG_PATH):
            try:
                with open(CONFIG_PATH, "r") as f:
                    data = json.load(f)
                    self.server_input.setText(data.get("server", ""))
                    self.share_input.setText(data.get("share", ""))
                    self.username_input.setText(data.get("username", ""))
                    self.password_input.setText(data.get("password", ""))
            except Exception:
                pass  # Fail silently

    def save_config(self, info: Dict[str, str]) -> None:
        try:
            with open(CONFIG_PATH, "w") as f:
                json.dump(info, f)
        except Exception:
            pass  # Fail silently

    def on_connect(self) -> None:
        info = {
            "server": self.server_input.text(),
            "share": self.share_input.text(),
            "username": self.username_input.text(),
            "password": self.password_input.text()
        }
        self.save_config(info)
        self.callback(info)
