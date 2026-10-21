"""搜索页：搜剧 → 查看剧集 → 选集加入下载。"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt, Signal, QThread, QTimer
from PySide6.QtWidgets import (
    QComboBox, QFrame, QGridLayout, QGroupBox, QHBoxLayout, 
    QLabel, QLineEdit, QMessageBox, QPushButton, QScrollArea, QSpinBox, 
    QVBoxLayout, QWidget, QProgressBar,
)

from duanju.models import Drama, Episode
from duanju.search_state import (
    BLOCKED_HINT,
    STATE_BLOCKED,
    STATE_EMPTY,
    STATE_ERROR,
    STATE_OK,
    format_state,
    is_blocked_message,
    normalize_state,
)
from ui.widgets import EpisodeGrid, PageTitle, small_label

logger = logging.getLogger(__name__)


class StreamFetchWorker(QThread):
    # 第三参数为请求头（dict），供播放器复用，避免出现“下载能播、在线播放黑屏”
    first_ready = Signal(str, str, object)
    item_ready = Signal(str, str, object)
    all_finished = Signal(int)
    error = Signal(str)
    blocked = Signal(str)

    def __init__(self, service, current, target_eps, titles=None):
        super().__init__()
        self.service = service
        self.current = current
        self.target_eps = target_eps
        # 分段剧（第1-20集 / 第21-40集 …）的真实标题，用于播放列表展示
        self.titles = dict(titles or {})
        self.is_running = True
        self._blocked_error: Exception | None = None

    def _ep_label(self, ep_num: int) -> str:
        title = (self.titles.get(int(ep_num)) or "").strip()
        if title and title != f"第{ep_num}集":
            return title
        return f"第{ep_num}集"

    def run(self):
        from concurrent.futures import ThreadPoolExecutor, as_completed
        from duanju.errors import BlockedError
        fail_count = 0

        def fetch_single(ep_num: int):
            if not self.is_running:
                return (ep_num, None, {})
            try:
                plan = self.service.resolve(self.current, ep_num)
                url = plan.url if hasattr(plan, 'url') else str(plan)
                headers = {}
                if hasattr(plan, 'request_headers'):
                    try:
                        headers = dict(plan.request_headers())
                    except Exception:  # noqa: BLE001
                        headers = {}
                if url:
                    return (ep_num, url, headers)
            except BlockedError as exc:
                # 人机验证：不降级成“解析失败”，带真实原因交给 UI 弹验证框
                self._blocked_error = exc
                raise
            except Exception as e:
                logger.warning("解析第 %d 集失败：%s", ep_num, e)
            return (ep_num, None, {})

        try:
            first_ep = self.target_eps[0]
            _, first_url, first_headers = fetch_single(first_ep)

            if not first_url:
                self.error.emit("第一集解析失败，无法立刻播放")
                return

            first_title = f"{self.current.title} - {self._ep_label(first_ep)} (在线)"
            self.first_ready.emit(first_title, first_url, first_headers)

            rest_eps = self.target_eps[1:]
            if rest_eps:
                max_workers = getattr(self.service.config, "concurrency", 10)
                with ThreadPoolExecutor(max_workers=max_workers) as executor:
                    futures = {executor.submit(fetch_single, ep): ep for ep in rest_eps}
                    for future in as_completed(futures):
                        if not self.is_running:
                            break
                        try:
                            ep_num, url, headers = future.result()
                        except BlockedError:
                            continue
                        if url:
                            title = f"{self.current.title} - {self._ep_label(ep_num)} (在线)"
                            self.item_ready.emit(title, url, headers)
                        else:
                            fail_count += 1

            self.all_finished.emit(fail_count)
        except BlockedError as exc:
            self.blocked.emit(str(exc))
        except Exception as e:
            self.error.emit(str(e))

    def stop(self):
        self.is_running = False


class SearchPage(QWidget):
    view_downloads = Signal()

    def __init__(self, service, bridge, parent: QWidget | None = None):
        super().__init__(parent)
        self.service = service
        self.bridge = bridge
        self._dramas: list[Drama] = []
        self._episodes: list[Episode] = []
        self._current: Drama | None = None
        # 本次在线解析是否遇到过站源受限（用于收尾文案，不再弹验证框）
        self._blocked_seen = False
        self._pending_target_eps: list[int] = []
        # 首集播放器还没创建时先到达的后续集（极端情况下信号早于窗口），
        # 暂存于此，窗口创建后立即补进播放列表，避免"列表只剩 1 集"。
        self._pending_playlist: list[tuple[str, str, dict]] = []
        self._cards: dict[str, Any] = {}
        self.all_cards: list[Any] = []
        self.pixmap_cache: dict[str, Any] = {}
        # 发现页分页状态：当前已加载到第几页 / 是否还有下一页 / 是否正在追加 / 过期结果作废令牌
        self._discover_page = 0
        self._home_has_more = False
        self._loading_more = False
        self._page_token = 0
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
        # 🎯 下拉框切换时自动加载推荐
        self.source_combo.currentIndexChanged.connect(self._auto_load_home)
        
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

        self.progress_bar = QProgressBar()
        self.progress_bar.setTextVisible(True)
        self.progress_bar.hide()
        root.addWidget(self.progress_bar)

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

        # 发现页底部「加载更多」：点击追加下一页卡片（搜索模式下隐藏）
        load_more_row = QHBoxLayout()
        load_more_row.addStretch(1)
        self.load_more_btn = QPushButton("加载更多")
        self.load_more_btn.setObjectName("Secondary")
        self.load_more_btn.setMinimumWidth(200)
        self.load_more_btn.clicked.connect(self._load_more)
        self.load_more_btn.hide()
        load_more_row.addWidget(self.load_more_btn)
        load_more_row.addStretch(1)
        root.addLayout(load_more_row)

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

        # 🎯 启动后延迟 100ms 自动加载推荐
        QTimer.singleShot(100, self._auto_load_home)

    def refresh_sources(self) -> None:
        current = self.source_combo.currentData()
        self.source_combo.blockSignals(True)
        self.source_combo.clear()
        for key, name in self.service.sources():
            self.source_combo.addItem(name, key)
        if current:
            index = self.source_combo.findData(current)
            if index >= 0:
                self.source_combo.setCurrentIndex(index)
        self.source_combo.blockSignals(False)

    # ==================== 首页推荐加载逻辑 ====================
    def _auto_load_home(self) -> None:
        """当搜索框为空时，自动加载当前站源的首页内容。"""
        if hasattr(self, '_home_loading') and self._home_loading:
            return
        if self.query_edit.text().strip():
            return
            
        source_key = self.source_combo.currentData()
        if not source_key:
            return
            
        self._home_loading = True
        self._page_token += 1
        self._discover_page = 0
        self._home_has_more = False
        self._loading_more = False
        self._update_load_more("hidden")
        self.status_label.setText("正在加载最新推荐…")
        self._clear_cards()
        
        self.bridge.run(
            lambda: self.service.get_catalog_page(source_key, 1),
            on_ok=self._on_home_ok,
            on_err=self._on_home_err,
        )

    def _on_home_ok(self, result: Any) -> None:
        self._home_loading = False
        dramas, has_more = self._split_page_result(result)
        if not dramas:
            self._update_load_more("hidden")
            self.status_label.setText("该站源暂未获取到推荐内容，请使用搜索功能")
            return

        self._discover_page = 1
        self._home_has_more = bool(has_more)
        self._render_cards(dramas, append=False)
        self.status_label.setText(
            f"为你推荐（来自 {self.source_combo.currentText()}）· 第 1 页 · 共 {len(self._dramas)} 部"
        )
        # 只有一页的站源（如未适配分类流的站）不显示「加载更多」
        self._update_load_more("more" if self._home_has_more else "hidden", page=2)

    def _on_home_err(self, message: str) -> None:
        self._home_loading = False
        self._loading_more = False
        self._update_load_more("hidden")
        self.status_label.setText(f"加载推荐失败：{message}")
    # ==========================================================

    def _on_selection_changed(self, count: int) -> None:
        self.count_label.setText(f"已选 {count} 集")
        self.download_button.setEnabled(count > 0 and self._current is not None)

    def _start_search(self) -> None:
        query = self.query_edit.text().strip()
        source_key = self.source_combo.currentData()
        if not query:
            self._auto_load_home()  # 清空搜索框后，恢复推荐
            return
        if not source_key:
            QMessageBox.warning(self, "搜索", "没有可用的站源，请到设置里检查")
            return
        self.search_button.setEnabled(False)
        self.status_label.setText("正在搜索…")
        self._page_token += 1
        self._discover_page = 0
        self._home_has_more = False
        self._loading_more = False
        self._update_load_more("hidden")
        self._clear_cards()
        self.bridge.run(
            lambda: self.service.search(source_key, query),
            on_ok=self._on_search_response,
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

    @staticmethod
    def _split_page_result(result: Any) -> tuple[list[Drama], bool]:
        """统一 (dramas, has_more) / 纯列表 两种返回形态。"""
        if isinstance(result, tuple) and len(result) == 2:
            dramas, has_more = result
            return list(dramas or []), bool(has_more)
        return list(result or []), False

    def _update_load_more(self, state: str, page: int = 1) -> None:
        """state: hidden=隐藏 / more=可点击 / loading=加载中 / end=已到底。"""
        if state == "hidden":
            self.load_more_btn.hide()
            self.load_more_btn.setEnabled(False)
            return
        self.load_more_btn.show()
        if state == "more":
            self.load_more_btn.setEnabled(True)
            self.load_more_btn.setText(f"加载更多（第 {page} 页）")
        elif state == "loading":
            self.load_more_btn.setEnabled(False)
            self.load_more_btn.setText(f"正在加载第 {page} 页…")
        else:  # end
            self.load_more_btn.setEnabled(False)
            self.load_more_btn.setText("没有更多了")

    def _ensure_image_loader(self) -> None:
        from ui.image_loader import ImageLoader

        if not hasattr(self, "_image_loader"):
            self._hashlib = hashlib
            self._image_loader = ImageLoader(self)
            self._image_loader.image_loaded.connect(self._on_image_loaded)
            self._image_loader.image_failed.connect(self._on_image_failed)
            logger.info("ImageLoader 已初始化")

    def _render_cards(self, dramas: list[Drama], append: bool = False) -> int:
        """把卡片渲染进网格，返回本次真正新增的卡片数。

        append=False 走"整体替换"（清空后重绘）；append=True 追加到末尾，
        并按 drama_id 跳过已在界面上的卡片，避免跨页重复造成孤儿卡片。
        """
        import time

        start_time = time.time()
        self._ensure_image_loader()

        if append:
            known = set(self._cards)
            fresh: list[Drama] = []
            for drama in dramas:
                if not drama.drama_id or drama.drama_id in known:
                    continue
                known.add(drama.drama_id)
                fresh.append(drama)
            dramas = fresh
            self._dramas.extend(dramas)
        else:
            self._dramas = list(dramas)

        from ui.video_card import VideoCard

        card_width = 180
        container_width = self._grid_container.width() or 800
        cols = max(3, min(6, container_width // card_width))
        start = len(self.all_cards)

        for idx, drama in enumerate(dramas):
            row = (start + idx) // cols
            col = (start + idx) % cols
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

        # 追加时按当前列数重排（窗口宽度变化后避免行错位）
        if append:
            for pos, card in enumerate(self.all_cards):
                self._grid_layout.removeWidget(card)
                self._grid_layout.addWidget(card, pos // cols, pos % cols)

        elapsed = time.time() - start_time
        logger.info(
            "渲染 %d 张卡片（%s），耗时 %.2f 秒",
            len(dramas),
            "追加" if append else "替换",
            elapsed,
        )
        return len(dramas)

    def _load_more(self) -> None:
        """点击底部「加载更多」：抓取下一页并追加到网格末尾。"""
        if self._loading_more or getattr(self, "_home_loading", False):
            return
        source_key = self.source_combo.currentData()
        if not source_key or not self._home_has_more:
            return

        next_page = self._discover_page + 1
        self._loading_more = True
        self._update_load_more("loading", page=next_page)
        token = self._page_token
        self.bridge.run(
            lambda: self.service.get_catalog_page(source_key, next_page),
            on_ok=lambda result: self._on_load_more_ok(token, next_page, result),
            on_err=lambda message: self._on_load_more_err(token, message),
        )

    def _on_load_more_ok(self, token: int, page: int, result: Any) -> None:
        if token != self._page_token:
            return  # 期间已切站源/重新搜索，丢弃过期结果
        self._loading_more = False
        dramas, has_more = self._split_page_result(result)
        added = self._render_cards(dramas, append=True)
        self._discover_page = page
        self._home_has_more = bool(has_more)
        total = len(self._dramas)
        source_name = self.source_combo.currentText()
        if self._home_has_more:
            self._update_load_more("more", page=page + 1)
            self.status_label.setText(
                f"为你推荐（来自 {source_name}）· 已加载 {page} 页 · 共 {total} 部"
            )
        else:
            self._update_load_more("end")
            self.status_label.setText(
                f"为你推荐（来自 {source_name}）· 已加载 {page} 页 · 共 {total} 部 · 已到底"
            )
        logger.info("追加第 %d 页：返回 %d 条，新增 %d 张卡片", page, len(dramas), added)

    def _on_load_more_err(self, token: int, message: str) -> None:
        if token != self._page_token:
            return
        self._loading_more = False
        self._update_load_more("more", page=self._discover_page + 1)
        self.status_label.setText(f"加载第 {self._discover_page + 1} 页失败：{message}")

    @staticmethod
    def _split_search_result(result: Any) -> tuple[list[Drama], str, str]:
        """兼容 (dramas, state, reason) 三态返回值与旧的 list 返回。"""
        if isinstance(result, tuple) and len(result) == 3:
            dramas, state, reason = result
            dramas = list(dramas or [])
            state = STATE_OK if not state and dramas else normalize_state(state or STATE_EMPTY)
            if state == STATE_OK and not dramas:
                state = STATE_EMPTY
            return dramas, state, str(reason or "")
        dramas = list(result or [])
        return dramas, STATE_OK if dramas else STATE_EMPTY, ""

    def _on_search_response(self, result: Any) -> None:
        """搜索结果的统一状态渲染入口（ok / empty / blocked / error 共用）。

        非 ok 状态只更新状态栏文案，不弹窗、不弹验证框。
        """
        self.search_button.setEnabled(True)
        # 搜索结果模式：重置分页状态并隐藏「加载更多」
        self._page_token += 1
        self._discover_page = 0
        self._home_has_more = False
        self._loading_more = False
        self._update_load_more("hidden")

        dramas, state, reason = self._split_search_result(result)
        query = self.query_edit.text().strip()
        source_name = self.source_combo.currentText()

        if state == STATE_OK and dramas:
            self._render_cards(dramas, append=False)
            self.status_label.setText(f"找到 {len(dramas)} 部剧，点击卡片查看剧集")
            return

        # 非 ok：清空结果区，按三态模板提示
        self._render_cards([], append=False)
        state = normalize_state(state)
        self.status_label.setText(format_state(state, query=query, source=source_name))
        if state == STATE_EMPTY:
            logger.info("搜索无结果（source=%s, query=%s）", source_name, query)
        elif state == STATE_BLOCKED:
            logger.warning("搜索站源受限（source=%s, query=%s）：%s", source_name, query, reason)
        else:
            logger.warning("搜索异常（source=%s, query=%s）：%s", source_name, query, reason)

    def _on_search_err(self, message: str) -> None:
        """后台线程级异常（极少触发）：同样落到统一文案，不弹窗。"""
        self.search_button.setEnabled(True)
        self._update_load_more("hidden")
        self.status_label.setText(
            format_state(
                STATE_ERROR,
                query=self.query_edit.text().strip(),
                source=self.source_combo.currentText(),
            )
        )
        logger.warning("搜索任务异常：%s", message)

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
        self.detail_title.setText(f"正在读取《{drama.title}》的分集列表…")
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
        total = int(drama.total or 0)
        if total > len(episodes):
            # 站点把多集打包成若干段直链：N 段 = N 条播放源，不是 N 集
            count_text = f"{len(episodes)} 段 · 共 {total} 集"
        else:
            count_text = f"{len(episodes)} 集"
        self.detail_title.setText(
            f"《{drama.title}》 · {count_text} · 来源：{drama.source_name}"
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
        self.episode_grid.set_episodes(
            numbers,
            completed=completed,
            labels={int(ep.number): ep.title for ep in episodes if ep.title},
        )
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
        """详情读取失败：站源受限时给固定切换提示，不弹任何验证框。"""
        self.detail_title.setText("读取分集失败")
        if is_blocked_message(message):
            self.status_label.setText(f"读取分集受限：{BLOCKED_HINT}")
            logger.warning("读取分集被站源拦截：%s", message)
            return
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

    # ==================== 在线播放 ====================
    # 注意：此处原有按站源名（djw123 / 短剧网123）硬编码的黑名单已删除。
    # 站源是否允许在线播放改由配置项 SourceConfig.allow_online 控制（默认 True）。
    def _preview_online(self) -> None:
        if not self._episodes:
            QMessageBox.warning(self, "提示", "没有可用的剧集数据")
            return

        if self._current is not None:
            cfg = self.service.config.source_by_key(self._current.source_key)
            if cfg is not None and not getattr(cfg, "allow_online", True):
                QMessageBox.information(
                    self,
                    "在线播放已关闭",
                    f"站源「{cfg.name}」在配置中关闭了在线播放（allow_online = false）。\n\n"
                    "如需在线播放，请在配置文件中把该站源的 allow_online 改为 true，"
                    "当前仍可使用“加入下载”。",
                )
                return

        selected = self.episode_grid.selected()
        if not selected:
            target_eps = [self._episodes[0].number]
        else:
            target_eps = selected

        self._start_stream_fetch(target_eps)

    def _start_stream_fetch(self, target_eps: list[int]) -> None:
        """解析并后台追加播放。"""
        if not target_eps or self._current is None:
            return
        self._pending_target_eps = list(target_eps)
        self._blocked_seen = False
        self.preview_button.setEnabled(False)

        self.progress_bar.setMaximum(len(target_eps))
        self.progress_bar.setValue(0)
        self.progress_bar.show()
        self.status_label.setText("正在解析第一集，请稍候…")

        titles = {
            int(episode.number): episode.title
            for episode in (self._episodes or [])
            if episode.title
        }
        self.stream_worker = StreamFetchWorker(
            self.service, self._current, list(target_eps), titles=titles
        )
        self.stream_worker.first_ready.connect(self._on_first_ep_ready)
        self.stream_worker.item_ready.connect(self._on_item_ready)
        self.stream_worker.all_finished.connect(self._on_all_finished)
        self.stream_worker.error.connect(self._on_fetch_error)
        self.stream_worker.blocked.connect(self._on_blocked)

        self._loaded_count = 0
        self._pending_playlist = []
        self.stream_worker.start()

    def _on_blocked(self, message: str) -> None:
        """解析遇到站源拦截（403/人机验证）：提示切换站源，不再弹 Cookie 验证框。"""
        self._blocked_seen = True
        self.preview_button.setEnabled(True)
        self.progress_bar.hide()
        self.status_label.setText(f"在线播放受限：{BLOCKED_HINT}")
        logger.warning("在线播放被站源拦截：%s", message)

    def _on_first_ep_ready(self, title: str, url: str, headers=None) -> None:
        self.status_label.setText("第一集已解析，正在播放，后台继续解析...")
        self._loaded_count += 1
        self.progress_bar.setValue(self._loaded_count)
        self._open_player(title, url, headers)

    def _open_player(self, title: str, url: str, headers=None) -> None:
        from ui.player_window import PlayerWindow

        try:
            window = PlayerWindow(
                playlist=[(title, url)],
                volume=self.service.config.volume,
                headers=headers or {},
            )
            self._current_stream_window = window
            window.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)

            # 关闭回调必须绑定"这个"窗口：否则用户先开 A 再开 B 时，
            # 关闭 A 会把 _current_stream_window 误清成 None，B 就再也追加不进后续集。
            def _on_player_closed(*_args, _win=window) -> None:
                if _win in self._preview_windows:
                    self._preview_windows.remove(_win)
                if self._current_stream_window is _win:
                    self._stop_background_fetch()

            window.destroyed.connect(_on_player_closed)
            self._preview_windows.append(window)

            # 首集窗口建好前就解析完成的后续集，在这里补进播放列表
            if getattr(self, "_pending_playlist", None):
                for pending_title, pending_url, pending_headers in self._pending_playlist:
                    window.append_to_playlist(pending_title, pending_url, headers=pending_headers)
                self._pending_playlist.clear()

            window.show()
        except Exception as e:
            logger.error("在线播放失败: %s", e)
            QMessageBox.warning(self, "播放失败", f"无法打开在线播放器：{e}")

    def _on_item_ready(self, title: str, url: str, headers=None) -> None:
        self._loaded_count += 1
        self.progress_bar.setValue(self._loaded_count)

        if getattr(self, '_current_stream_window', None) and self._current_stream_window.is_alive:
            self._current_stream_window.append_to_playlist(title, url, headers=headers or {})
        else:
            # 播放器还没建好（首集仍在解析/窗口初始化中），先暂存，建好后补进去
            self._pending_playlist.append((title, url, dict(headers or {})))

    def _stop_background_fetch(self) -> None:
        if hasattr(self, 'stream_worker') and self.stream_worker and self.stream_worker.isRunning():
            self.stream_worker.stop()
            self.progress_bar.hide()
            self.preview_button.setEnabled(True)
            self.status_label.setText("已停止后台解析")
        self._current_stream_window = None

    def _on_all_finished(self, fail_count: int) -> None:
        self.preview_button.setEnabled(True)
        self.progress_bar.hide()
        if fail_count > 0:
            if self._blocked_seen:
                self.status_label.setText(
                    f"解析完毕，有 {fail_count} 集未能解析 · {BLOCKED_HINT}"
                )
            else:
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
        pass
