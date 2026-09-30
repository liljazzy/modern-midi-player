"""Dark, modern theme for the application."""
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

BG = "#14161c"
BG2 = "#1b1e26"
PANEL = "#222632"
PANEL2 = "#2a2f3d"
BORDER = "#343a4a"
TEXT = "#e6e9f2"
TEXT_DIM = "#8b93a7"
ACCENT = "#7c6cff"
ACCENT2 = "#39d0c8"
MUTE = "#ff7a45"
SOLO = "#ffd24a"
DANGER = "#ff5c7a"

STYLESHEET = f"""
* {{ font-size: 10pt; }}
QMainWindow, QWidget {{ background: {BG}; color: {TEXT}; }}
QToolTip {{ background: {PANEL2}; color: {TEXT}; border: 1px solid {BORDER}; padding: 4px; }}
QMenuBar {{ background: {BG2}; }}
QMenuBar::item:selected {{ background: {PANEL2}; border-radius: 4px; }}
QMenu {{ background: {PANEL}; border: 1px solid {BORDER}; padding: 4px; }}
QMenu::item {{ padding: 5px 22px 5px 22px; border-radius: 4px; }}
QMenu::item:selected {{ background: {ACCENT}; color: white; }}
QMenu::separator {{ height: 1px; background: {BORDER}; margin: 4px 8px; }}
QTabWidget::pane {{ border: none; background: {BG}; }}
QTabBar::tab {{ background: transparent; color: {TEXT_DIM}; padding: 8px 18px; margin-right: 2px;
               border-bottom: 2px solid transparent; font-weight: 600; }}
QTabBar::tab:selected {{ color: {TEXT}; border-bottom: 2px solid {ACCENT}; }}
QTabBar::tab:hover {{ color: {TEXT}; }}
QPushButton, QToolButton {{ background: {PANEL2}; border: 1px solid {BORDER}; border-radius: 6px;
               padding: 5px 10px; color: {TEXT}; }}
QPushButton:hover, QToolButton:hover {{ border-color: {ACCENT}; }}
QPushButton:pressed, QToolButton:pressed {{ background: {BORDER}; }}
QPushButton:checked, QToolButton:checked {{ background: {ACCENT}; border-color: {ACCENT}; color: white; }}
QPushButton:disabled, QToolButton:disabled {{ color: {TEXT_DIM}; }}
QPushButton#mute:checked, QToolButton#mute:checked {{ background: {MUTE}; border-color: {MUTE}; color: #1a1a1a; }}
QPushButton#solo:checked, QToolButton#solo:checked {{ background: {SOLO}; border-color: {SOLO}; color: #1a1a1a; }}
QPushButton#play {{ background: {ACCENT}; border: none; border-radius: 20px; color: white;
               font-size: 16pt; min-width: 40px; min-height: 40px; max-width: 40px; max-height: 40px; }}
QPushButton#play:hover {{ background: #8f82ff; }}
QPushButton#transport {{ background: transparent; border: none; font-size: 13pt; min-width: 32px; min-height: 32px; }}
QPushButton#transport:hover {{ color: {ACCENT}; }}
QPushButton#transport:checked {{ color: {ACCENT2}; background: transparent; }}
QPushButton#loop {{ background: transparent; border: 1px solid {BORDER}; border-radius: 14px; color: {TEXT_DIM};
               padding: 4px 10px; min-height: 20px; }}
QPushButton#loop:hover {{ border-color: {ACCENT}; }}
QPushButton#loop:checked {{ background: {ACCENT}; border-color: {ACCENT}; color: white; font-weight: 600; }}
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox {{ background: {BG2}; border: 1px solid {BORDER};
               border-radius: 5px; padding: 3px 6px; selection-background-color: {ACCENT}; }}
QComboBox::drop-down {{ border: none; width: 18px; }}
QComboBox QAbstractItemView {{ background: {PANEL}; border: 1px solid {BORDER};
               selection-background-color: {ACCENT}; }}
QSlider::groove:horizontal {{ height: 4px; background: {BORDER}; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: {ACCENT}; border-radius: 2px; }}
QSlider::handle:horizontal {{ background: {TEXT}; width: 12px; height: 12px; margin: -5px 0; border-radius: 6px; }}
QSlider::handle:horizontal:hover {{ background: white; }}
QSlider::groove:vertical {{ width: 4px; background: {BORDER}; border-radius: 2px; }}
QSlider::add-page:vertical {{ background: {ACCENT}; border-radius: 2px; }}
QSlider::handle:vertical {{ background: {TEXT}; height: 22px; width: 18px; margin: 0 -8px;
               border-radius: 4px; border: 1px solid {BORDER}; }}
QSlider#seek::groove:horizontal {{ height: 6px; border-radius: 3px; }}
QSlider#seek::sub-page:horizontal {{ background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 {ACCENT}, stop:1 {ACCENT2});
               border-radius: 3px; }}
QScrollBar:vertical {{ background: {BG}; width: 10px; margin: 0; }}
QScrollBar::handle:vertical {{ background: {PANEL2}; min-height: 30px; border-radius: 5px; }}
QScrollBar:horizontal {{ background: {BG}; height: 10px; margin: 0; }}
QScrollBar::handle:horizontal {{ background: {PANEL2}; min-width: 30px; border-radius: 5px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: none; }}
QTreeWidget, QListWidget, QTableWidget {{ background: {BG2}; border: 1px solid {BORDER}; border-radius: 6px;
               alternate-background-color: #1e222b; outline: none; }}
QTreeWidget::item, QListWidget::item {{ padding: 4px 2px; }}
QTreeWidget::item:selected, QListWidget::item:selected {{ background: #3a3470; color: white; }}
QHeaderView::section {{ background: {BG2}; color: {TEXT_DIM}; border: none; border-bottom: 1px solid {BORDER};
               padding: 4px 6px; font-weight: 600; }}
QDockWidget {{ color: {TEXT_DIM}; font-weight: 600; }}
QDockWidget::title {{ background: {BG2}; padding: 6px; }}
QStatusBar {{ background: {BG2}; color: {TEXT_DIM}; }}
QFrame#card {{ background: {PANEL}; border: 1px solid {BORDER}; border-radius: 10px; }}
QFrame#strip {{ background: {PANEL}; border: 1px solid {BORDER}; border-radius: 8px; }}
QFrame#transportBar {{ background: {BG2}; border-top: 1px solid {BORDER}; }}
QLabel#title {{ font-size: 15pt; font-weight: 700; }}
QLabel#dim {{ color: {TEXT_DIM}; }}
QLabel#time {{ font-family: "Consolas", "Menlo", "DejaVu Sans Mono", monospace; color: {TEXT_DIM}; }}
QToolBar {{ background: {BG2}; border: none; spacing: 4px; padding: 4px; }}
QSplitter::handle {{ background: {BORDER}; }}
"""


def apply_theme(app: QApplication):
    app.setStyle("Fusion")
    pal = QPalette()
    pal.setColor(QPalette.Window, QColor(BG))
    pal.setColor(QPalette.WindowText, QColor(TEXT))
    pal.setColor(QPalette.Base, QColor(BG2))
    pal.setColor(QPalette.AlternateBase, QColor(PANEL))
    pal.setColor(QPalette.Text, QColor(TEXT))
    pal.setColor(QPalette.Button, QColor(PANEL2))
    pal.setColor(QPalette.ButtonText, QColor(TEXT))
    pal.setColor(QPalette.Highlight, QColor(ACCENT))
    pal.setColor(QPalette.HighlightedText, QColor("white"))
    pal.setColor(QPalette.ToolTipBase, QColor(PANEL2))
    pal.setColor(QPalette.ToolTipText, QColor(TEXT))
    pal.setColor(QPalette.PlaceholderText, QColor(TEXT_DIM))
    app.setPalette(pal)
    app.setStyleSheet(STYLESHEET)
