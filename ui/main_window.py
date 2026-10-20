"""主窗口：侧边导航 + 页面栈 + 日志坞。"""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QApplication,
    QDockWidget,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QMainWindow,
    QMessageBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from duanju.util import open_in_explorer
from duanju.version import APP_TITLE, VERSION
from ui.downloads_page import DownloadsPage
from ui.library_page import LibraryPage
from ui.player_window import PlayerWindow
from ui.search_page import SearchPage
from ui.settings_page import SettingsPage
from ui.theme import stylesheet
from ui.widgets import LogPanel

logger = logging.getLogger(__name__)

NAV_ITEMS = ["发现", "下载", "剧库", "设置"]


class MainWindow(QMainWindow):
    def __init__(self, config, service, bridge, parent: QWidget | None = None):
        super().__init__(parent)
        self.config = config
        self.service = service
        self.bridge = bridge
        self._players: list[PlayerWindow] = []
        self._demo = False
        self._build()

    # ---------- UI ----------
    def _build(self) -> None:
        self.setWindowTitle(f"{APP_TITLE} v{VERSION}")
        self.resize(1280, 840)

        central = QWidget()
        self.setCentralWidget(central)
        layout = QHBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        sidebar = QWidget()
        sidebar.setObjectName("Sidebar")
        sidebar.setFixedWidth(188)
        sidebar_layout = QVBoxLayout(sidebar)
        sidebar_layout.setContentsMargins(10, 18, 10, 16)
        sidebar_layout.setSpacing(10)
        brand = QLabel("短剧\n下载器")
        brand.setStyleSheet("font-size: 17px; font-weight: 700; color: #ff5f57; padding: 4px 8px;")
        sidebar_layout.addWidget(brand)
        self.nav = QListWidget()
        self.nav.setObjectName("Nav")
        for text in NAV_ITEMS:
            self.nav.addItem(text)
        self.nav.setCurrentRow(0)
        sidebar_layout.addWidget(self.nav, 1)
        version_label = QLabel(f"v{VERSION} · 仅供个人学习")
        version_label.setObjectName("Muted")
        version_label.setWordWrap(True)
        sidebar_layout.addWidget(version_label)
        layout.addWidget(sidebar)

        self.stack = QStackedWidget()
        layout.addWidget(self.stack, 1)

        self.search_page = SearchPage(self.service, self.bridge)
        self.downloads_page = DownloadsPage(self.service, self.bridge)
        self.library_page = LibraryPage(self.service, self.bridge)
        self.settings_page = SettingsPage(self.config, self.service, self.bridge)
        for page in (self.search_page, self.downloads_page, self.library_page, self.settings_page):
            self.stack.addWidget(page)

        self._build_log_dock()
        self._build_menu()
        self._build_status_bar()

        self.nav.currentRowChanged.connect(self._switch_page)
        self.search_page.view_downloads.connect(lambda: self.nav.setCurrentRow(1))
        
        # 🎯 核心：连接本地播放和在线播放的信号
        self.downloads_page.play_file.connect(lambda p: self._play_playlist([(Path(p).name, p)], 0))
        self.library_page.play_playlist.connect(self._play_playlist)
        
        self.settings_page.settings_changed.connect(self._on_settings_changed)
        self.search_page.challenge_result_received.connect(self._on_challenge_result)
        self.bridge.log_message.connect(self.log_panel.append_line)
        self.bridge.jobs_changed.connect(self._update_stats)

        self.downloads_page.refresh()
        self.library_page.refresh()
        self._update_stats(self.service.jobs_snapshot())

    def _build_log_dock(self) -> None:
        self.log_panel = LogPanel()
        dock = QDockWidget("运行日志", self)
        dock.setObjectName("LogDock")
        dock.setWidget(self.log_panel)
        dock.setAllowedAreas(Qt.BottomDockWidgetArea | Qt.TopDockWidgetArea)
        self.addDockWidget(Qt.BottomDockWidgetArea, dock)
        self.log_dock = dock
        self.resizeDocks([dock], [200], Qt.Vertical)
        dock.hide() # 🎯 启动时隐藏日志

    def _build_menu(self) -> None:
        menu = self.menuBar()
        file_menu = menu.addMenu("文件")
        open_dir = QAction("打开下载目录", self)
        open_dir.triggered.connect(self._open_download_dir)
        file_menu.addAction(open_dir)
        file_menu.addSeparator()
        quit_action = QAction("退出", self)
        quit_action.setShortcut("Ctrl+Q")
        quit_action.triggered.connect(self.close)
        file_menu.addAction(quit_action)

        view_menu = menu.addMenu("视图")
        self.log_action = self.log_dock.toggleViewAction()
        self.log_action.setText("运行日志")
        self.log_action.setShortcut("Ctrl+L")
        view_menu.addAction(self.log_action)

        help_menu = menu.addMenu("帮助")
        about_action = QAction("关于", self)
        about_action.triggered.connect(self._about)
        help_menu.addAction(about_action)

    def _build_status_bar(self) -> None:
        self.statusBar().showMessage("就绪")
        self.stats_label = QLabel("")
        self.statusBar().addPermanentWidget(self.stats_label)

    # ---------- 行为 ----------
    def switch_page(self, index: int) -> None:
        self.nav.setCurrentRow(index)

    def _switch_page(self, index: int) -> None:
        index = max(0, min(index, self.stack.count() - 1))
        self.stack.setCurrentIndex(index)
        if self._demo:
            return
        if index == 1:
            self.downloads_page.refresh()
        elif index == 2:
            self.library_page.refresh()
        elif index == 3:
            self.settings_page.reload()

    def _play_playlist(self, playlist: list[tuple[str, str]], start_index: int = 0) -> None:
        """统一处理播放列表（包括本地连播和在线连播）。"""
        if not playlist:
            return
        
        # 验证起始文件是否存在
        source = Path(playlist[start_index][1])
        if not source.is_file() and not playlist[start_index][1].startswith("http"):
            QMessageBox.warning(self, "播放", f"文件不存在：{source}")
            return
        
        try:
            window = PlayerWindow(
                playlist=playlist,
                index=start_index,
                volume=self.config.volume,
            )
        except Exception as exc:
            QMessageBox.warning(self, "播放器", f"无法打开播放器：{exc}")
            return
        window.setAttribute(Qt.WA_DeleteOnClose, True)
        window.destroyed.connect(lambda *_args: self._forget_player(window))
        self._players.append(window)
        window.show()

    def _forget_player(self, window: PlayerWindow) -> None:
        if window in self._players:
            self._players.remove(window)

    def _update_stats(self, jobs: list[dict]) -> None:
        downloading = sum(1 for job in jobs if job.get("state") == "downloading")
        queued = sum(1 for job in jobs if job.get("state") == "queued")
        completed = sum(1 for job in jobs if job.get("state") == "completed")
        failed = sum(1 for job in jobs if job.get("state") == "failed")
        self.stats_label.setText(
            f"下载中 {downloading} · 等待 {queued} · 完成 {completed} · 失败 {failed}"
        )

    def _open_download_dir(self) -> None:
        self.service.open_download_dir()
        open_in_explorer(self.config.download_path())

    def _on_settings_changed(self) -> None:
        app = QApplication.instance()
        if app is not None:
            app.setStyleSheet(stylesheet(self.config.theme))
        self.search_page.refresh_sources()

    def _on_challenge_result(self, cookies: list[dict], user_agent: str) -> None:
        query = self.search_page.query_edit.text().strip()
        source_key = self.search_page.source_combo.currentData()
        if not query or not source_key:
            return

        def do_retry() -> None:
            try:
                result = self.service.retry_search_with_challenge(query, cookies, user_agent)
                self.search_page._challenge_pending = False
                self.search_page._on_search_ok(result)
            except Exception as exc:
                self.search_page._challenge_pending = False
                from PySide6.QtCore import QTimer
                error_msg = f"{exc}"
                if "人机验证" in error_msg or "Cloudflare" in error_msg:
                    error_msg += "\n\n可能原因：\n1. UA 与浏览器不一致\n2. Cookie 已过期\n3. IP 被限制"
                QTimer.singleShot(0, lambda: QMessageBox.critical(
                    self, "验证失败", f"重试失败：{error_msg}\n\n请确保 UA 与浏览器一致，或稍后重试。"
                ))

        self.bridge.run(do_retry)

    def _about(self) -> None:
        QMessageBox.about(
            self,
            "关于",
            f"<b>{APP_TITLE}</b> v{VERSION}<br><br>"
            "Windows 桌面短剧下载器：搜索、批量下载、断点恢复、整部合并与内置播放。<br><br>"
            "仅供个人学习分镜、离线研究使用；请勿传播、二创或商用。",
        )

    def closeEvent(self, event) -> None:
        try:
            active = self.service.active_jobs()
        except Exception:
            active = 0
        if active:
            reply = QMessageBox.question(
                self,
                "退出",
                f"还有 {active} 个任务正在下载，确定退出？\n未完成的任务会在下次启动后可继续。",
            )
            if reply != QMessageBox.Yes:
                event.ignore()
                return
        try:
            self.service.shutdown()
        except Exception:
            logger.exception("关闭服务失败")
        event.accept()

    def load_demo(self) -> None:
        self._demo = True
        self.search_page.load_demo()
        self.downloads_page.load_demo()
        self.library_page.load_demo()
        self._update_stats(
            [
                {"state": "completed"},
                {"state": "downloading"},
                {"state": "queued"},
                {"state": "failed"},
            ]
        )
        self.statusBar().showMessage("演示模式")