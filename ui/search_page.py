"""搜索页：搜剧 → 查看剧集 → 选集加入下载。"""

from __future__ import annotations

import logging

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from duanju.models import Drama, Episode
from ui.widgets import EpisodeGrid, PageTitle, small_label

logger = logging.getLogger(__name__)


class SearchPage(QWidget):
    view_downloads = Signal()

    def __init__(self, service, bridge, parent: QWidget | None = None):
        super().__init__(parent)
        self.service = service
        self.bridge = bridge
        self._dramas: list[Drama] = []
        self._episodes: list[Episode] = []
        self._current: Drama | None = None
        self._build()
        self.refresh_sources()

    # ---------- UI ----------
    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(14)

        root.addWidget(
            PageTitle("发现", "输入剧名搜索；双击结果查看剧集并加入下载队列")
        )

        search_bar = QHBoxLayout()
        self.source_combo = QComboBox()
        self.source_combo.setMinimumWidth(160)
        self.query_edit = QLineEdit()
        self.query_edit.setPlaceholderText("输入剧名，例如：示例短剧")
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

        self.results = QTableWidget(0, 3)
        self.results.setHorizontalHeaderLabels(["剧名", "集数", "ID"])
        self.results.horizontalHeader().setStretchLastSection(False)
        self.results.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.results.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.results.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.results.verticalHeader().setVisible(False)
        self.results.setSelectionBehavior(QTableWidget.SelectRows)
        self.results.setEditTriggers(QTableWidget.NoEditTriggers)
        self.results.setAlternatingRowColors(False)
        self.results.cellDoubleClicked.connect(lambda row, _col: self._open_detail(row))
        self.results.setMinimumHeight(180)
        root.addWidget(self.results, 2)

        detail_box = QGroupBox("剧集")
        detail_layout = QVBoxLayout(detail_box)
        detail_layout.setSpacing(10)
        header = QHBoxLayout()
        self.detail_title = QLabel("先搜索并双击一部剧")
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

    # ---------- 数据 ----------
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

    # ---------- 动作 ----------
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
        self.bridge.run(
            lambda: self.service.search(source_key, query),
            on_ok=self._on_search_ok,
            on_err=self._on_search_err,
        )

    def _on_search_ok(self, dramas: list[Drama]) -> None:
        self.search_button.setEnabled(True)
        self._dramas = dramas
        self.results.setRowCount(0)
        for drama in dramas:
            row = self.results.rowCount()
            self.results.insertRow(row)
            title_item = QTableWidgetItem(drama.title)
            title_item.setData(Qt.UserRole, row)
            self.results.setItem(row, 0, title_item)
            total_text = f"{drama.total} 集" if drama.total else "—"
            self.results.setItem(row, 1, QTableWidgetItem(total_text))
            self.results.setItem(row, 2, QTableWidgetItem(drama.drama_id))
        self.status_label.setText(
            f"找到 {len(dramas)} 部剧，双击查看剧集" if dramas else "没有找到匹配的剧集"
        )

    def _on_search_err(self, message: str) -> None:
        self.search_button.setEnabled(True)
        self.status_label.setText(f"搜索失败：{message}")
        logger.warning("搜索失败：%s", message)

    def _open_detail(self, row: int) -> None:
        if not (0 <= row < len(self._dramas)):
            return
        drama = self._dramas[row]
        self._current = drama
        self.detail_title.setText(f"正在读取《{drama.title}》的分集…")
        self.download_button.setEnabled(False)
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
        except Exception:  # noqa: BLE001 - 已下载标记只是锦上添花
            completed = set()
        self.episode_grid.set_episodes(numbers, completed=completed)
        self.range_from.setRange(1, max(numbers) if numbers else 1)
        self.range_to.setRange(1, max(numbers) if numbers else 1)
        self.range_from.setValue(min(numbers) if numbers else 1)
        self.range_to.setValue(max(numbers) if numbers else 1)
        self.episode_grid.set_all(True)
        self.status_label.setText(f"已加载《{drama.title}》共 {len(episodes)} 集")

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

    # ---------- 演示 / 截图 ----------
    def load_demo(self) -> None:
        dramas = [
            Drama("duanju", "短剧聚合", "15297", "示例短剧 A", total=76),
            Drama("duanju", "短剧聚合", "11792", "示例短剧 B", total=62),
        ]
        self._on_search_ok(dramas)
        self.results.selectRow(0)
        self._current = dramas[0]
        episodes = [Episode(number=n, title=f"第{n}集") for n in range(1, 77)]
        self._on_detail_ok(dramas[0], episodes)
        # 演示：13/14/15/20 已下载（绿色 ✓），1~12 选中（红色），其余未选中（深色）
        self.episode_grid.set_episodes(
            [episode.number for episode in episodes],
            completed={13, 14, 15, 20},
        )
        self.episode_grid.select_range(1, 12)
        self.status_label.setText("演示数据：找到 2 部剧（用于界面截图）")
