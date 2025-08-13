from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication,
    QMainWindow,
    QWidget,
    QDockWidget,
)
from components.file_explorer import FileExplorer
from components.connection_form import ConnectionForm


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("NAS Media Browser")
        self.setMinimumSize(600, 400)

        # Central widget is an empty placeholder; both panels are docks
        self.setCentralWidget(QWidget())

        # Connection form dock
        self.connection_form = ConnectionForm(self.on_connected)
        self.connection_dock = QDockWidget("Connection", self)
        self.connection_dock.setObjectName("ConnectionDock")
        self.connection_dock.setWidget(self.connection_form)
        self.connection_dock.setAllowedAreas(
            Qt.DockWidgetArea.LeftDockWidgetArea
            | Qt.DockWidgetArea.RightDockWidgetArea
            | Qt.DockWidgetArea.TopDockWidgetArea
        )
        self.connection_dock.setFeatures(
            QDockWidget.DockWidgetFeature.DockWidgetClosable
            | QDockWidget.DockWidgetFeature.DockWidgetMovable
            | QDockWidget.DockWidgetFeature.DockWidgetFloatable
        )
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, self.connection_dock)

        # File explorer dock (created after connection)
        self.file_explorer_dock: QDockWidget | None = None

    def on_connected(self, session_info):
        # Remove existing dock if reconnecting
        if self.file_explorer_dock is not None:
            self.removeDockWidget(self.file_explorer_dock)
            self.file_explorer_dock.deleteLater()
            self.file_explorer_dock = None

        explorer = FileExplorer(session_info)
        dock = QDockWidget("File Explorer", self)
        dock.setObjectName("FileExplorerDock")
        dock.setWidget(explorer)
        dock.setAllowedAreas(
            Qt.DockWidgetArea.LeftDockWidgetArea
            | Qt.DockWidgetArea.RightDockWidgetArea
            | Qt.DockWidgetArea.BottomDockWidgetArea
        )
        dock.setFeatures(
            QDockWidget.DockWidgetFeature.DockWidgetClosable
            | QDockWidget.DockWidgetFeature.DockWidgetMovable
            | QDockWidget.DockWidgetFeature.DockWidgetFloatable
        )
        # Opposite side to connection dock for balanced layout
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, dock)
        self.file_explorer_dock = dock
        dock.raise_()


if __name__ == "__main__":
    app = QApplication([])
    window = MainWindow()
    window.show()
    app.exec()
