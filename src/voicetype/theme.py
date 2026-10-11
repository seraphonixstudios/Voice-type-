"""Dark theme for VoiceType: one QSS, identical on macOS/Windows/Linux.

Uses the Fusion style as a neutral base (native look per platform is kept via
Qt platform themes for dialogs/file pickers) plus a full dark palette and one
configurable accent color.
"""
from __future__ import annotations

DEFAULT_ACCENT = "#4da6ff"

DARK_QSS_TEMPLATE = """
QWidget {{ background-color: #1b1e24; color: #e6e9ef; font-size: 13px; }}
QMainWindow, QDialog {{ background-color: #1b1e24; }}
QGroupBox {{ border: 1px solid #333a45; border-radius: 8px; margin-top: 14px;
             padding-top: 10px; font-weight: 600; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 10px; padding: 0 6px;
                   color: {accent}; }}
QLabel {{ background: transparent; }}
QLabel#muted {{ color: #9aa3b2; }}
QLabel#accent {{ color: {accent}; font-weight: 600; }}
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QTextEdit, QPlainTextEdit,
QKeySequenceEdit {{
    background-color: #242932; border: 1px solid #3a4250; border-radius: 6px;
    padding: 5px 8px; selection-background-color: {accent};
}}
QLineEdit:focus, QComboBox:focus {{ border: 1px solid {accent}; }}
QPushButton {{ background-color: #2c333f; border: 1px solid #3f4756;
               border-radius: 6px; padding: 6px 14px; }}
QPushButton:hover {{ background-color: #353d4b; border-color: {accent}; }}
QPushButton:pressed {{ background-color: #232833; }}
QPushButton#primary {{ background-color: {accent}; color: #10141a; border: none;
                       font-weight: 700; }}
QPushButton#primary:hover {{ background-color: {accent_hover}; }}
QPushButton#danger {{ background-color: #5a2b30; border-color: #8a3b43; }}
QSlider::groove:horizontal {{ height: 6px; background: #2c333f;
                              border-radius: 3px; }}
QSlider::handle:horizontal {{ background: {accent}; width: 16px; height: 16px;
                              margin: -6px 0; border-radius: 8px; }}
QProgressBar {{ background: #242932; border: 1px solid #3a4250;
                border-radius: 6px; text-align: center; }}
QProgressBar::chunk {{ background-color: {accent}; border-radius: 5px; }}
QTabWidget::pane {{ border: 1px solid #333a45; border-radius: 8px; }}
QTabBar::tab {{ background: #242932; padding: 8px 16px; margin-right: 2px;
                border-top-left-radius: 6px; border-top-right-radius: 6px; }}
QTabBar::tab:selected {{ background: #2c333f; color: {accent}; }}
QListWidget, QTableWidget {{ background-color: #20252e; border: 1px solid #333a45;
                             border-radius: 6px; alternate-background-color: #242932; }}
QHeaderView::section {{ background: #2c333f; border: none; padding: 6px; }}
QScrollBar:vertical {{ background: transparent; width: 10px; }}
QScrollBar::handle:vertical {{ background: #3a4250; border-radius: 5px;
                               min-height: 30px; }}
QToolTip {{ background: #242932; color: #e6e9ef; border: 1px solid {accent}; }}
QMenu {{ background-color: #242932; border: 1px solid #3a4250; }}
QMenu::item:selected {{ background-color: {accent}; color: #10141a; }}
QCheckBox::indicator {{ width: 16px; height: 16px; border-radius: 4px;
                        border: 1px solid #3a4250; background: #242932; }}
QCheckBox::indicator:checked {{ background: {accent}; }}
"""


def _hover(accent: str) -> str:
    h = accent.lstrip("#")
    try:
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
        r = min(255, int(r * 1.12 + 12))
        g = min(255, int(g * 1.12 + 12))
        b = min(255, int(b * 1.12 + 12))
        return f"#{r:02x}{g:02x}{b:02x}"
    except Exception:
        return accent


def build_qss(accent: str = DEFAULT_ACCENT) -> str:
    accent = accent or DEFAULT_ACCENT
    return DARK_QSS_TEMPLATE.format(accent=accent, accent_hover=_hover(accent))


def apply_theme(app, accent: str = DEFAULT_ACCENT) -> str:
    """Apply Fusion base + dark palette + QSS. Returns the QSS string."""
    from PySide6.QtWidgets import QStyleFactory
    from PySide6.QtGui import QPalette, QColor
    from PySide6.QtCore import Qt
    if "Fusion" in QStyleFactory.keys():
        app.setStyle("Fusion")  # neutral cross-platform base; native dialogs untouched
    pal = QPalette()
    pal.setColor(QPalette.Window, QColor("#1b1e24"))
    pal.setColor(QPalette.WindowText, QColor("#e6e9ef"))
    pal.setColor(QPalette.Base, QColor("#242932"))
    pal.setColor(QPalette.AlternateBase, QColor("#20252e"))
    pal.setColor(QPalette.Text, QColor("#e6e9ef"))
    pal.setColor(QPalette.Button, QColor("#2c333f"))
    pal.setColor(QPalette.ButtonText, QColor("#e6e9ef"))
    pal.setColor(QPalette.Highlight, QColor(accent or DEFAULT_ACCENT))
    pal.setColor(QPalette.HighlightedText, QColor("#10141a"))
    pal.setColor(QPalette.ToolTipBase, QColor("#242932"))
    pal.setColor(QPalette.ToolTipText, QColor("#e6e9ef"))
    app.setPalette(pal)
    qss = build_qss(accent)
    app.setStyleSheet(qss)
    return qss
