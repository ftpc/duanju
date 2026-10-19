"""深色 / 浅色主题样式表。"""

from __future__ import annotations

DARK = """
* { font-family: "Microsoft YaHei UI", "Segoe UI", sans-serif; font-size: 13px; }
QMainWindow, QDialog { background: #14161d; }
QWidget#Sidebar { background: #101218; border-right: 1px solid #262a36; }
QWidget#PageHeader { background: transparent; }
QLabel { color: #e6e9f2; }
QLabel#PageTitle { font-size: 20px; font-weight: 600; color: #ffffff; }
QLabel#PageSubtitle, QLabel#Muted { color: #8b93a7; }
QLabel#ErrorText { color: #ff6b6b; }
QLabel#StatusText { color: #9aa3b8; }
QListWidget#Nav { background: transparent; border: none; outline: 0; padding: 8px; }
QListWidget#Nav::item { color: #aeb6c9; padding: 12px 14px; border-radius: 10px; margin: 2px 0; }
QListWidget#Nav::item:selected { background: #262b3a; color: #ffffff; }
QListWidget#Nav::item:hover { background: #1d2130; }
QPushButton { background: #2a3040; color: #e6e9f2; border: 1px solid #353c4e; border-radius: 8px; padding: 7px 14px; }
QPushButton:hover { background: #333b4f; }
QPushButton:pressed { background: #232936; }
QPushButton:disabled { color: #6a7184; background: #1c202b; border-color: #262b38; }
QPushButton#Primary { background: #ff5f57; border-color: #ff5f57; color: #ffffff; font-weight: 600; }
QPushButton#Primary:hover { background: #ff756e; }
QPushButton#Primary:pressed { background: #e54d46; }
QPushButton#Danger { background: #3a2330; border-color: #5a3040; color: #ff9a9a; }
QPushButton#Danger:hover { background: #4a2a3a; }
/* 选集按钮：未选中=深色底，选中=品牌红，已下载=绿色底/描边 + √ */
QWidget#EpisodeGrid { background: transparent; }
QScrollArea, QScrollArea > QWidget > QWidget { background: transparent; }
QPushButton#EpisodeButton {
    background: #1d2230;
    color: #cfd6e6;
    border: 1px solid #39415a;
    border-radius: 8px;
    padding: 4px 6px;
    font-weight: 600;
}
QPushButton#EpisodeButton:hover { background: #2b3346; border-color: #ff8a7f; color: #ffffff; }
QPushButton#EpisodeButton:checked { background: #ff5f57; border: 1px solid #ff5f57; color: #ffffff; }
QPushButton#EpisodeButton:checked:hover { background: #ff756e; border-color: #ffb4ad; }
QPushButton#EpisodeButton[completed="true"] { background: #17302a; border: 1px solid #3f8f63; color: #a7e8bd; }
QPushButton#EpisodeButton[completed="true"]:checked { background: #ff5f57; border-color: #ff5f57; color: #ffffff; }
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox { background: #1b1f29; color: #e6e9f2; border: 1px solid #2f3546; border-radius: 8px; padding: 6px 10px; selection-background-color: #ff5f57; }
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus { border-color: #ff5f57; }
QComboBox QAbstractItemView { background: #1b1f29; color: #e6e9f2; selection-background-color: #2f3546; border: 1px solid #2f3546; }
QTableView, QTableWidget, QListWidget#Plain, QPlainTextEdit, QTextEdit { background: #191d26; color: #e6e9f2; border: 1px solid #262b38; border-radius: 10px; gridline-color: #232838; selection-background-color: #2b3346; selection-color: #ffffff; }
QHeaderView::section { background: #1e2430; color: #9aa3b8; border: none; border-bottom: 1px solid #2a3040; padding: 8px; font-weight: 600; }
QTableView::item { padding: 4px; }
QTabWidget::pane { border: 1px solid #262b38; border-radius: 10px; }
QTabBar::tab { background: #1b1f29; color: #9aa3b8; padding: 8px 16px; border-top-left-radius: 8px; border-top-right-radius: 8px; margin-right: 2px; }
QTabBar::tab:selected { background: #262b3a; color: #ffffff; }
QProgressBar { background: #1b1f29; border: 1px solid #2a3040; border-radius: 6px; text-align: center; color: #e6e9f2; height: 18px; }
QProgressBar::chunk { background: #ff5f57; border-radius: 5px; }
QSlider::groove:horizontal { height: 4px; background: #2a3040; border-radius: 2px; }
QSlider::handle:horizontal { width: 14px; margin: -5px 0; border-radius: 7px; background: #ff5f57; }
QScrollArea { border: none; background: transparent; }
QScrollBar:vertical { background: transparent; width: 10px; }
QScrollBar::handle:vertical { background: #333a4c; border-radius: 5px; min-height: 30px; }
QScrollBar:horizontal { background: transparent; height: 10px; }
QScrollBar::handle:horizontal { background: #333a4c; border-radius: 5px; min-width: 30px; }
QStatusBar { background: #101218; color: #8b93a7; }
QDockWidget { color: #e6e9f2; }
QDockWidget::title { background: #1b1f29; padding: 6px; }
QMenuBar { background: #101218; color: #cfd5e4; }
QMenuBar::item:selected { background: #262b3a; }
QMenu { background: #1b1f29; color: #e6e9f2; border: 1px solid #2f3546; }
QMenu::item:selected { background: #2f3546; }
QCheckBox { color: #d5dae6; }
QGroupBox { color: #cfd5e4; border: 1px solid #262b38; border-radius: 10px; margin-top: 12px; padding-top: 8px; }
QGroupBox::title { subcontrol-origin: margin; left: 12px; padding: 0 4px; color: #9aa3b8; }
QToolTip { background: #262b3a; color: #e6e9f2; border: 1px solid #3a4155; }
"""

LIGHT = """
* { font-family: "Microsoft YaHei UI", "Segoe UI", sans-serif; font-size: 13px; }
QMainWindow, QDialog { background: #f4f5f8; }
QWidget#Sidebar { background: #ffffff; border-right: 1px solid #e2e5ee; }
QLabel { color: #242936; }
QLabel#PageTitle { font-size: 20px; font-weight: 600; color: #10131a; }
QLabel#PageSubtitle, QLabel#Muted { color: #7a8194; }
QLabel#ErrorText { color: #d93025; }
QLabel#StatusText { color: #666e82; }
QListWidget#Nav { background: transparent; border: none; outline: 0; padding: 8px; }
QListWidget#Nav::item { color: #5a6173; padding: 12px 14px; border-radius: 10px; margin: 2px 0; }
QListWidget#Nav::item:selected { background: #ffe8e6; color: #d93025; }
QListWidget#Nav::item:hover { background: #f0f1f5; }
QPushButton { background: #ffffff; color: #242936; border: 1px solid #d8dce6; border-radius: 8px; padding: 7px 14px; }
QPushButton:hover { background: #f2f4f9; }
QPushButton:disabled { color: #a8aec0; background: #f5f6f9; }
QPushButton#Primary { background: #ff5f57; border-color: #ff5f57; color: #ffffff; font-weight: 600; }
QPushButton#Primary:hover { background: #ff756e; }
QPushButton#Danger { background: #fff0f0; border-color: #f2c2c2; color: #c0392b; }
/* 选集按钮：未选中=白底，选中=品牌红，已下载=绿色底/描边 + √ */
QWidget#EpisodeGrid { background: transparent; }
QScrollArea, QScrollArea > QWidget > QWidget { background: transparent; }
QPushButton#EpisodeButton {
    background: #ffffff;
    color: #3a4152;
    border: 1px solid #d8dce6;
    border-radius: 8px;
    padding: 4px 6px;
    font-weight: 600;
}
QPushButton#EpisodeButton:hover { background: #fff1ef; border-color: #ff8a7f; color: #c0392b; }
QPushButton#EpisodeButton:checked { background: #ff5f57; border: 1px solid #ff5f57; color: #ffffff; }
QPushButton#EpisodeButton:checked:hover { background: #ff756e; border-color: #ffb4ad; }
QPushButton#EpisodeButton[completed="true"] { background: #eefaf2; border: 1px solid #59a87a; color: #1f6b45; }
QPushButton#EpisodeButton[completed="true"]:checked { background: #ff5f57; border-color: #ff5f57; color: #ffffff; }
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox { background: #ffffff; color: #242936; border: 1px solid #d8dce6; border-radius: 8px; padding: 6px 10px; }
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus { border-color: #ff5f57; }
QTableView, QTableWidget, QListWidget#Plain, QPlainTextEdit, QTextEdit { background: #ffffff; color: #242936; border: 1px solid #e2e5ee; border-radius: 10px; gridline-color: #eef0f5; selection-background-color: #ffe3e1; selection-color: #242936; }
QHeaderView::section { background: #f7f8fb; color: #666e82; border: none; border-bottom: 1px solid #e2e5ee; padding: 8px; font-weight: 600; }
QTabWidget::pane { border: 1px solid #e2e5ee; border-radius: 10px; }
QTabBar::tab { background: #f0f1f5; color: #666e82; padding: 8px 16px; border-top-left-radius: 8px; border-top-right-radius: 8px; margin-right: 2px; }
QTabBar::tab:selected { background: #ffffff; color: #d93025; }
QProgressBar { background: #f0f1f5; border: 1px solid #e2e5ee; border-radius: 6px; text-align: center; color: #242936; height: 18px; }
QProgressBar::chunk { background: #ff5f57; border-radius: 5px; }
QSlider::groove:horizontal { height: 4px; background: #e2e5ee; border-radius: 2px; }
QSlider::handle:horizontal { width: 14px; margin: -5px 0; border-radius: 7px; background: #ff5f57; }
QStatusBar { background: #ffffff; color: #7a8194; }
QMenuBar { background: #ffffff; color: #242936; }
QMenuBar::item:selected { background: #f0f1f5; }
QMenu { background: #ffffff; color: #242936; border: 1px solid #d8dce6; }
QMenu::item:selected { background: #ffe8e6; }
QCheckBox { color: #242936; }
QGroupBox { color: #242936; border: 1px solid #e2e5ee; border-radius: 10px; margin-top: 12px; padding-top: 8px; }
QGroupBox::title { subcontrol-origin: margin; left: 12px; padding: 0 4px; color: #7a8194; }
"""


def stylesheet(theme: str) -> str:
    return LIGHT if theme == "light" else DARK
