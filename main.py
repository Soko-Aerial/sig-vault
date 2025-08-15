from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QMainWindow, QDockWidget, QWidget
from src.components.explorer import Explorer
from PySide6.QtGui import QFont


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Sig/Vault")
        self.setCentralWidget(QWidget())

        font = QFont()
        font.setPixelSize(13)
        app.setFont(font)

        explorer_dock = QDockWidget("Explorer", self)
        explorer_dock.setObjectName("ExplorerDock")
        explorer_dock.setAllowedAreas(
            Qt.DockWidgetArea.LeftDockWidgetArea | Qt.DockWidgetArea.RightDockWidgetArea
        )
        explorer_dock.setWidget(Explorer())

        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, explorer_dock)


if __name__ == "__main__":
    app = QApplication([])
    window = MainWindow()
    window.show()
    app.exec()
