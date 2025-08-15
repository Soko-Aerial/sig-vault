from PySide6.QtGui import QGuiApplication, QPalette
from PySide6.QtWidgets import QApplication


def get_theme_mode() -> str:
    """Return 'dark' or 'light' based on the current system/application theme."""
    if QApplication.instance() is None:
        raise RuntimeError("QApplication instance not found. Initialize it first.")
    palette = QGuiApplication.palette()
    bg_color = palette.color(QPalette.ColorRole.Window)
    brightness = (
        bg_color.red() * 0.299 + bg_color.green() * 0.587 + bg_color.blue() * 0.114
    )
    return "dark" if brightness < 128 else "light"
