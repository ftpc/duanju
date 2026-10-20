"""搜索页：搜剧 → 查看剧集 → 选集加入下载。"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt, Signal, QThread
from PySide6.QtWidgets import (
    QDialog,
    QComboBox,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
    QProgressBar,
)

from duanju.models import Drama, Episode
from ui.widgets import EpisodeGrid, PageTitle, small_label

logger = logging.getLogger(__name__)

class StreamFetchWorker(QThread):
    first_ready = Signal(str, str)
    item_ready = Signal(str, str)
    all_finished = Signal(int)
    error = Signal(str)

    def __init__(self, service, current, target_eps):
        super().__init__()
        self.service = service
        self.current = current
        self.target_eps = target_eps
        self.is_running = True

    def run(self):
        from concurrent.futures import ThreadPoolExecutor, as_completed
        fail_count = 0

        def fetch_single(ep_num: int):
            if not self.is_running:
                return (ep_num, None)
            try:
                plan = self.service.resolve(self.current, ep_num)
                url = plan.url if hasattr(plan, 'url') else str(plan)
                if url:
                    return (ep_num, url)
            except Exception as e:
                logger.warning("解析第 %d 集失败：%s", ep_num, e)
            return (ep_num, None)

        try:
            first_ep = self.target_eps[0]
            _, first_url = fetch_single(first_ep)
            
            if not first_url:
                self.error.emit("第一集解析失败，无法立刻播放")
                return
            
            self.first_ready.emit(f"{self.current.title} - 第{first_ep}集 (在线)", first_url)

            rest_eps = self.target_eps[1:]
            if rest_eps:
                max_workers = getattr(self.service.config, "concurrency", 10)
                with ThreadPoolExecutor(max_workers=max_workers) as executor:
                    futures = {executor.submit(fetch_single, ep): ep for ep in rest_eps}
                    for future in as_completed(futures):
                        if not self.is_running:
                            break
                        ep_num, url = future.result()
                        if url:
                            title = f"{self.current.title} - 第{ep_num}集 (在线)"
                            self.item_ready.emit(title, url)
                        else:
                            fail_count += 1

            self.all_finished.emit(fail_count)
        except Exception as e:
            self.error.emit(str(e))

    def stop(self):
        self.is_running = False


class SearchPage(QWidget):
    view_downloads = Signal()
    challenge_result_received = Signal(list, str)

    def __init__(self, service, bridge, parent: QWidget | None = None):
        super().__init__(parent)
        self.service = service
        self.bridge = bridge
        self._dramas: list[Drama] = []
        self._episodes: list[Episode] = []
        self._current: Drama | None = None
        self._challenge_pending = False
        self._cards: dict[str, Any] = {}
        self.all_cards: list[Any] = []
        self.pixmap_cache: dict[str, Any] = {}
        self._preview_windows: list[Any] = []
        self._current_stream_window = None
        self._build()
        self.refresh_sources()

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(14)

        root.addWidget(PageTitle("发现", "输入剧名搜索；点击卡片查看剧集并加入下载队列"))

        search_bar = QHBoxLayout()
        self.source_combo = QComboBox()
        self.source_combo.setMinimumWidth(160)
        self.query_edit = QLineEdit()
        self.query_edit.setPlaceholderText("输入剧名")
        self.query_edit.returnPressed.connect(self._start_search)
        self.search_button = QPushButton("搜索")
        self.search_button.setObjectName("Primary")
        self.search_button.clicked.connect(self._start_search)
        search_bar.addWidget(self.source_combo)
        search_bar.addWidget(self.query_edit, 1)
        search_bar.addWidget(self.search_button)
        root.addLayout(search_bar)

        self.status_label = QLabel("")
        self.status_label.setObjectName("StatusText")
        root.addWidget(self.status_label)

        # ====== 新增：常驻进度条 ======
        self.progress_bar = QProgressBar()
        self.progress_bar.setTextVisible(True)
        self.progress_bar.hide() # 默认隐藏
        root.addWidget(self.progress_bar)
        # ==========================

        self._scroll_area = QScrollArea()
        self._scroll_area.setWidgetResizable(True)
        self._scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._scroll_area.setMinimumHeight(300)
        self._grid_container = QWidget()
        self._grid_layout = QGridLayout(self._grid_container)
        self._grid_layout.setSpacing(16)
        self._grid_layout.setContentsMargins(0, 0, 0, 0)
        self._scroll_area.setWidget(self._grid_container)
        root.addWidget(self._scroll_area, 2)

        detail_box = QGroupBox("剧集")
        detail_layout = QVBoxLayout(detail_box)
        detail_layout.setSpacing(10)
        header = QHBoxLayout()
        self.detail_title = QLabel("先搜索并点击一部剧")
        self.detail_title.setObjectName("PageSubtitle")
        header.addWidget(self.detail_title, 1)
        detail_layout.addLayout(header)

        self.episode_scroll = QScrollArea()
        self.episode_scroll.setWidgetResizable(True)
        self.episode_scroll.setMinimumHeight(150)
        self.episode_grid = EpisodeGrid()
        self.episode_scroll.setWidget(self.episode_grid)
        detail_layout.addWidget(self.episode_scroll, 1)

        tools = QHBoxLayout()
        self.select_all_btn = QPushButton("全选")
        self.select_none_btn = QPushButton("全不选")
        self.invert_btn = QPushButton("反选")
        self.select_all_btn.clicked.connect(lambda: self.episode_grid.set_all(True))
        self.select_none_btn.clicked.connect(lambda: self.episode_grid.set_all(False))
        self.invert_btn.clicked.connect(self.episode_grid.invert)
        tools.addWidget(self.select_all_btn)
        tools.addWidget(self.select_none_btn)
        tools.addWidget(self.invert_btn)
        tools.addWidget(small_label("范围"))
        self.range_from = QSpinBox()
        self.range_from.setRange(1, 99999)
        self.range_to = QSpinBox()
        self.range_to.setRange(1, 99999)
        self.range_apply = QPushButton("应用")
        self.range_apply.clicked.connect(self._apply_range)
        tools.addWidget(self.range_from)
        tools.addWidget(small_label("—"))
        tools.addWidget(self.range_to)
        tools.addWidget(self.range_apply)

        self.preview_button = QPushButton("▶ 在线播放")
        self.preview_button.setObjectName("Secondary")
        self.preview_button.clicked.connect(self._preview_online)
        self.preview_button.setEnabled(False)
        tools.addWidget(self.preview_button)

        tools.addStretch(1)
        self.count_label = QLabel("已选 0 集")
        self.count_label.setObjectName("StatusText")
        tools.addWidget(self.count_label)
        self.download_button = QPushButton("加入下载")
        self.download_button.setObjectName("Primary")
        self.download_button.clicked.connect(self._enqueue)
        self.download_button.setEnabled(False)
        tools.addWidget(self.download_button)
        self.view_button = QPushButton("查看下载")
        self.view_button.clicked.connect(self.view_downloads.emit)
        tools.addWidget(self.view_button)
        detail_layout.addLayout(tools)
        self.episode_grid.selection_changed.connect(self._on_selection_changed)

        root.addWidget(detail_box, 3)

        divider = QFrame()
        divider.setFrameShape(QFrame.HLine)
        divider.setStyleSheet("color: #262b38;")
        root.addWidget(divider)

    def refresh_sources(self) -> None:
        current = self.source_combo.currentData()
        self.source_combo.clear()
        for key, name in self.service.sources():
            self.source_combo.addItem(name, key)
        if current:
            index = self.source_combo.findData(current)
            if index >= 0:
                self.source_combo.setCurrentIndex(index)

    def _on_selection_changed(self, count: int) -> None:
        self.count_label.setText(f"已选 {count} 集")
        self.download_button.setEnabled(count > 0 and self._current is not None)

    def _start_search(self) -> None:
        query = self.query_edit.text().strip()
        source_key = self.source_combo.currentData()
        if not query:
            self.query_edit.setFocus()
            return
        if not source_key:
            QMessageBox.warning(self, "搜索", "没有可用的站源，请到设置里检查")
            return
        self.search_button.setEnabled(False)
        self.status_label.setText("正在搜索…")
        self._clear_cards()
        self.bridge.run(
            lambda: self.service.search(source_key, query),
            on_ok=self._on_search_ok,
            on_err=self._on_search_err,
        )

    def _clear_cards(self) -> None:
        while self._grid_layout.count():
            item = self._grid_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.hide()
                widget.setParent(None)
                widget.deleteLater()
        self._cards.clear()
        self.all_cards.clear()

    def _on_search_ok(self, dramas: list[Drama]) -> None:
        import time
        start = time.time()
        
        self.search_button.setEnabled(True)
        self._dramas = dramas
        self._clear_cards()
        
        from ui.video_card import VideoCard
        from ui.image_loader import ImageLoader
        
        if not hasattr(self, '_image_loader'):
            self._hashlib = hashlib
            self._image_loader = ImageLoader(self)
            self._image_loader.image_loaded.connect(self._on_image_loaded)
            self._image_loader.image_failed.connect(self._on_image_failed)
            logger.info("ImageLoader 已初始化")
        
        card_width = 180
        container_width = self._grid_container.width() or 800
        cols = max(3, min(6, container_width // card_width))
        
        for idx, drama in enumerate(dramas):
            row = idx // cols
            col = idx % cols
            card = VideoCard(
                title=drama.title,
                cover_url=drama.cover,
                status=f"全{drama.total}集" if drama.total else "",
                drama_id=drama.drama_id,
                parent=self._grid_container,
            )
            card.clicked.connect(lambda did=drama.drama_id: self._open_detail_by_id(did))
            self._grid_layout.addWidget(card, row, col)
            self._cards[drama.drama_id] = card
            self.all_cards.append(card)

            if drama.cover:
                cache_path = self._image_loader.get_cache_path(drama.cover)
                if cache_path and cache_path.exists():
                    from PySide6.QtGui import QPixmap
                    pixmap = QPixmap(str(cache_path))
                    if not pixmap.isNull():
                        card.set_cover_pixmap(pixmap)
                    else:
                        self._image_loader.load(drama.cover)
                else:
                    self._image_loader.load(drama.cover)
        
        elapsed = time.time() - start
        logger.info("显示 %d 个卡片，耗时 %.2f秒", len(dramas), elapsed)
        
        self.status_label.setText(
            f"找到 {len(dramas)} 部剧，点击卡片查看剧集" if dramas else "没有找到匹配的剧集"
        )

    def _on_image_loaded(self, url: str, local_path: str) -> None:
        from PySide6.QtCore import QTimer
        from PySide6.QtGui import QPixmap

        def update_card():
            pixmap = QPixmap(local_path)
            if pixmap.isNull():
                return
            self.pixmap_cache[url] = pixmap
            url_prefix = url[:50]
            for card in self.all_cards:
                if card.image_url and card.image_url[:50] == url_prefix:
                    card.set_cover_pixmap(pixmap)
                    card.update()
                    break

        QTimer.singleShot(50, update_card)

    def _on_image_failed(self, url: str, error: str) -> None:
        url_prefix = url[:50]
        for card in self.all_cards:
            if card.image_url and card.image_url[:50] == url_prefix:
                card.set_cover_error()
                return

    def _open_detail_by_id(self, drama_id: str) -> None:
        for drama in self._dramas:
            if drama.drama_id == drama_id:
                self._open_detail_by_drama(drama)
                return

    def _open_detail_by_drama(self, drama: Drama) -> None:
        self._current = drama
        self.detail_title.setText(f"正在读取《{drama.title}》的分集…")
        self.download_button.setEnabled(False)
        self.preview_button.setEnabled(False)
        self.episode_grid.clear()
        self.bridge.run(
            lambda: self.service.detail(drama),
            on_ok=lambda episodes: self._on_detail_ok(drama, episodes),
            on_err=self._on_detail_err,
        )

    def _on_detail_ok(self, drama: Drama, episodes: list[Episode]) -> None:
        self._episodes = episodes
        self.detail_title.setText(
            f"《{drama.title}》 · {len(episodes)} 集 · 来源：{drama.source_name}"
        )
        numbers = [episode.number for episode in episodes]
        completed: set[int] = set()
        try:
            completed = {
                int(job["episode"])
                for job in self.service.episodes(drama.key)
                if job.get("state") == "completed"
            }
        except Exception:
            completed = set()
        self.episode_grid.set_episodes(numbers, completed=completed)
        self.range_from.setRange(1, max(numbers) if numbers else 1)
        self.range_to.setRange(1, max(numbers) if numbers else 1)
        self.range_from.setValue(min(numbers) if numbers else 1)
        self.range_to.setValue(max(numbers) if numbers else 1)
        self.episode_grid.set_all(True)
        self.status_label.setText(f"已加载《{drama.title}》共 {len(episodes)} 集")
        
        if episodes:
            self.preview_button.setEnabled(True)
        else:
            self.preview_button.setEnabled(False)

    def _on_detail_err(self, message: str) -> None:
        self.detail_title.setText("读取分集失败")
        self.status_label.setText(f"读取分集失败：{message}")

    def _apply_range(self) -> None:
        self.episode_grid.select_range(self.range_from.value(), self.range_to.value())

    def _enqueue(self) -> None:
        if self._current is None:
            return
        selected = self.episode_grid.selected()
        if not selected:
            QMessageBox.information(self, "加入下载", "请先选择要下载的集数")
            return
        drama = self._current
        self.download_button.setEnabled(False)
        self.bridge.run(
            lambda: self.service.enqueue(drama, selected),
            on_ok=self._on_enqueue_ok,
            on_err=self._on_enqueue_err,
        )

    def _on_enqueue_ok(self, added: int) -> None:
        self.download_button.setEnabled(True)
        self.status_label.setText(
            f"已加入 {added} 个下载任务（已在队列中的会自动跳过）"
            if added
            else "所选集数都已在下载队列中"
        )

    def _on_enqueue_err(self, message: str) -> None:
        self.download_button.setEnabled(True)
        QMessageBox.warning(self, "加入下载失败", message)

    def _on_search_err(self, message: str) -> None:
        self.search_button.setEnabled(True)
        if ("人机验证" in message or "Cloudflare" in message) and not self._challenge_pending:
            self._show_challenge_dialog(message)
            return
        self.status_label.setText(f"搜索失败：{message}")
        logger.warning("搜索失败：%s", message)

    def _show_challenge_dialog(self, message: str) -> None:
        from ui.cf_challenge_dialog import CfChallengeDialog
        source_key = self.source_combo.currentData()
        url_for_verify = ""
        if source_key:
            cfg = self.service.config.source_by_key(source_key)
            if cfg and cfg.bases:
                url_for_verify = cfg.bases[0]

        dialog = CfChallengeDialog(url_for_verify or "https://", self)
        result = dialog.exec()

        if result != QDialog.Accepted:
            self.status_label.setText(f"搜索失败：{message}")
            return

        cookies = dialog.get_cookies()
        user_agent = dialog.get_user_agent()
        if not cookies:
            QMessageBox.warning(self, "验证失败", "未检测到有效 Cookie，请重新粘贴。")
            return

        self._challenge_pending = True
        self.status_label.setText("已收到验证结果，正在重试搜索…")
        self.challenge_result_received.emit(cookies, user_agent)

    # ================= 核心：秒播 + 后台动态追加 =================
    def _preview_online(self) -> None:
        if not self._episodes:
            QMessageBox.warning(self, "提示", "没有可用的剧集数据")
            return

        selected = self.episode_grid.selected()
        if not selected:
            target_eps = [self._episodes[0].number]
        else:
            target_eps = selected

        self.preview_button.setEnabled(False)
        
        # 在主窗体下方显示进度条
        self.progress_bar.setMaximum(len(target_eps))
        self.progress_bar.setValue(0)
        self.progress_bar.show()
        self.status_label.setText("正在解析第一集，请稍候...")

        self.stream_worker = StreamFetchWorker(self.service, self._current, target_eps)
        self.stream_worker.first_ready.connect(self._on_first_ep_ready)
        self.stream_worker.item_ready.connect(self._on_item_ready)
        self.stream_worker.all_finished.connect(self._on_all_finished)
        self.stream_worker.error.connect(self._on_fetch_error)
        
        # 记录已经加载了多少集，用于进度条
        self._loaded_count = 0
        self.stream_worker.start()

    def _on_first_ep_ready(self, title: str, url: str) -> None:
        self.status_label.setText("第一集已解析，正在播放，后台继续解析...")
        self._loaded_count += 1
        self.progress_bar.setValue(self._loaded_count)
        
        from ui.player_window import PlayerWindow
        
        try:
            self._current_stream_window = PlayerWindow(
                playlist=[(title, url)],
                volume=self.service.config.volume
            )
            self._current_stream_window.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
            
            # 核心：当窗口销毁时，立刻停止后台线程解析！
            self._current_stream_window.destroyed.connect(self._stop_background_fetch)
            
            self._preview_windows.append(self._current_stream_window)
            self._current_stream_window.destroyed.connect(lambda: self._preview_windows.remove(self._current_stream_window) if self._current_stream_window in self._preview_windows else None)
            
            self._current_stream_window.show()
        except Exception as e:
            logger.error("在线播放失败: %s", e)
            QMessageBox.warning(self, "播放失败", f"无法打开在线播放器：{e}")

    def _stop_background_fetch(self) -> None:
        """当用户关闭播放器时，立刻叫停后台解析。"""
        logger.info("播放器窗口被关闭，停止后台线程解析。")
        if hasattr(self, 'stream_worker') and self.stream_worker and self.stream_worker.isRunning():
            self.stream_worker.stop() # 安全停止
            self.progress_bar.hide()
            self.preview_button.setEnabled(True)
            self.status_label.setText("已停止后台解析")
        self._current_stream_window = None

    def _on_item_ready(self, title: str, url: str) -> None:
        self._loaded_count += 1
        self.progress_bar.setValue(self._loaded_count)
        
        # 修复：检查窗口是否还存在，并且 is_alive 标记是否有效
        if getattr(self, '_current_stream_window', None) and self._current_stream_window.is_alive:
            self._current_stream_window.append_to_playlist(title, url)

    def _on_all_finished(self, fail_count: int) -> None:
        self.preview_button.setEnabled(True)
        self.progress_bar.hide()
        if fail_count > 0:
            self.status_label.setText(f"解析完毕，有 {fail_count} 集解析失败。")
        else:
            self.status_label.setText("全部剧集解析完成！")
        self._current_stream_window = None

    def _on_fetch_error(self, message: str) -> None:
        self.preview_button.setEnabled(True)
        self.progress_bar.hide()
        self.status_label.setText(f"在线解析失败：{message}")
        QMessageBox.warning(self, "在线播放失败", f"解析过程出错：\n{message}")

    def load_demo(self) -> None:
        dramas = [
            Drama("duanju", "短剧聚合", "15297", "示例短剧 A", total=76),
            Drama("duanju", "短剧聚合", "11792", "示例短剧 B", total=62),
        ]
        self._on_search_ok(dramas)
        self._open_detail_by_id("15297")