"""剧库页：按剧查看已下载分集、合并整部、播放 / 打开 / 删除。"""

from __future__ import annotations

import threading

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QProgressDialog,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from duanju.models import STATE_LABELS
from duanju.util import human_size, open_in_explorer
from ui.widgets import PageTitle, small_label


class LibraryPage(QWidget):
    play_file = Signal(str)

    def __init__(self, service, bridge, parent: QWidget | None = None):
        super().__init__(parent)
        self.service = service
        self.bridge = bridge
        self._rows: list[dict] = []
        self._episodes: list[dict] = []
        self._merged: list[dict] = []
        self._current_key = ""
        self._merge_cancel = threading.Event()
        self._progress_dialog: QProgressDialog | None = None
        self._build()
        bridge.jobs_changed.connect(lambda _jobs: self.refresh())
        bridge.progress.connect(self._on_progress)

    # ---------- UI ----------
    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(12)
        root.addWidget(PageTitle("剧库", "已下载的剧集与合并成品；可播放、打开目录或合并整部"))

        splitter = QSplitter(Qt.Horizontal)

        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        self.drama_table = QTableWidget(0, 4)
        self.drama_table.setHorizontalHeaderLabels(["剧名", "已完成", "总数", "合并"])
        self.drama_table.verticalHeader().setVisible(False)
        self.drama_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.drama_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.drama_table.horizontalHeader().setStretchLastSection(False)
        self.drama_table.setColumnWidth(0, 260)
        self.drama_table.setColumnWidth(1, 70)
        self.drama_table.setColumnWidth(2, 70)
        self.drama_table.setColumnWidth(3, 60)
        self.drama_table.itemSelectionChanged.connect(self._on_drama_selected)
        left_layout.addWidget(self.drama_table, 1)
        refresh_button = QPushButton("刷新")
        refresh_button.clicked.connect(self.refresh)
        left_layout.addWidget(refresh_button)
        splitter.addWidget(left)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        self.selected_label = QLabel("在左侧选择一部剧")
        self.selected_label.setObjectName("PageSubtitle")
        right_layout.addWidget(self.selected_label)

        self.tabs = QTabWidget()
        self.episode_list = QListWidget()
        self.episode_list.setObjectName("Plain")
        self.episode_list.itemDoubleClicked.connect(lambda _item: self._play_episode())
        self.tabs.addTab(self.episode_list, "分集")
        self.merged_list = QListWidget()
        self.merged_list.setObjectName("Plain")
        self.merged_list.itemDoubleClicked.connect(lambda _item: self._play_merged())
        self.tabs.addTab(self.merged_list, "合并成品")
        right_layout.addWidget(self.tabs, 1)

        actions = QHBoxLayout()
        self.play_btn = QPushButton("播放")
        self.play_btn.clicked.connect(self._play_current)
        self.merge_btn = QPushButton("合并整部")
        self.merge_btn.setObjectName("Primary")
        self.merge_btn.clicked.connect(self._merge)
        self.open_btn = QPushButton("打开目录")
        self.open_btn.clicked.connect(self._open_dir)
        self.delete_btn = QPushButton("删除选中成品")
        self.delete_btn.setObjectName("Danger")
        self.delete_btn.clicked.connect(self._delete_merged)
        for button in (self.play_btn, self.merge_btn, self.open_btn, self.delete_btn):
            actions.addWidget(button)
        actions.addStretch(1)
        self.status = small_label("")
        actions.addWidget(self.status)
        right_layout.addLayout(actions)
        splitter.addWidget(right)
        splitter.setSizes([420, 620])
        root.addWidget(splitter, 1)

    # ---------- 数据 ----------
    def refresh(self) -> None:
        rows = self.service.library()
        self._rows = rows
        self.drama_table.setRowCount(0)
        for row in rows:
            index = self.drama_table.rowCount()
            self.drama_table.insertRow(index)
            self.drama_table.setItem(index, 0, QTableWidgetItem(str(row.get("title") or "")))
            self.drama_table.setItem(index, 1, QTableWidgetItem(str(row.get("completed") or 0)))
            self.drama_table.setItem(index, 2, QTableWidgetItem(str(row.get("total") or 0)))
            merged_text = str(row.get("merged") or 0)
            self.drama_table.setItem(index, 3, QTableWidgetItem(merged_text))
        if rows and not self._current_key:
            self.drama_table.selectRow(0)

    def _on_drama_selected(self) -> None:
        rows = {index.row() for index in self.drama_table.selectedItems()}
        if not rows:
            return
        index = sorted(rows)[0]
        if not (0 <= index < len(self._rows)):
            return
        row = self._rows[index]
        self._current_key = str(row.get("drama_key") or "")
        self.selected_label.setText(
            f"《{row.get('title')}》 · 已完成 {row.get('completed')}/{row.get('total')} 集"
        )
        self._episodes = self.service.episodes(self._current_key)
        self._merged = self.service.merged_items(self._current_key)
        self._fill_episodes()
        self._fill_merged()

    def _fill_episodes(self) -> None:
        self.episode_list.clear()
        for job in self._episodes:
            state = STATE_LABELS.get(str(job.get("state")), str(job.get("state")))
            size = human_size(job.get("bytes") or 0) if job.get("bytes") else ""
            text = f"第{job.get('episode')}集 · {state}"
            if size:
                text += f" · {size}"
            item = QListWidgetItem(text)
            item.setData(Qt.UserRole, job)
            self.episode_list.addItem(item)

    def _fill_merged(self) -> None:
        self.merged_list.clear()
        for item_data in self._merged:
            size = human_size(item_data.get("bytes") or 0)
            item = QListWidgetItem(f"{item_data.get('episodes')} 集 · {size}")
            item.setData(Qt.UserRole, item_data)
            item.setToolTip(str(item_data.get("file") or ""))
            self.merged_list.addItem(item)

    # ---------- 操作 ----------
    def _play_episode(self) -> None:
        item = self.episode_list.currentItem()
        if item is None:
            return
        job = item.data(Qt.UserRole) or {}
        if job.get("state") != "completed" or not job.get("file"):
            QMessageBox.information(self, "播放", "这一集还没有下载完成")
            return
        self.play_file.emit(str(job["file"]))

    def _play_merged(self) -> None:
        item = self.merged_list.currentItem()
        if item is None:
            return
        data = item.data(Qt.UserRole) or {}
        path = str(data.get("file") or "")
        if path:
            self.play_file.emit(path)

    def _play_current(self) -> None:
        if self.tabs.currentIndex() == 0:
            self._play_episode()
        else:
            self._play_merged()

    def _open_dir(self) -> None:
        if self._current_key:
            files = self.service.completed_files(self._current_key)
            if files:
                open_in_explorer(files[0]["file"], select=True)
                return
        open_in_explorer(self.service.config.download_path())

    def _delete_merged(self) -> None:
        item = self.merged_list.currentItem()
        if item is None:
            return
        data = item.data(Qt.UserRole) or {}
        if QMessageBox.question(self, "删除成品", "确定删除这个合并文件？") != QMessageBox.Yes:
            return
        self.service.delete_merged(str(data.get("id")))
        self._merged = self.service.merged_items(self._current_key)
        self._fill_merged()

    # ---------- 合并 ----------
    def _merge(self) -> None:
        if not self._current_key:
            return
        episodes = self.service.episodes(self._current_key)
        completed = [job for job in episodes if job.get("state") == "completed"]
        if not completed:
            QMessageBox.information(self, "合并", "这部剧还没有已完成的分集")
            return
        if len(completed) != len(episodes):
            reply = QMessageBox.question(
                self,
                "合并整部",
                f"还有 {len(episodes) - len(completed)} 集未完成，只合并已完成的 {len(completed)} 集？",
            )
            if reply != QMessageBox.Yes:
                return
        self._merge_cancel = threading.Event()
        dialog = QProgressDialog("正在合并…", "取消", 0, 1000, self)
        dialog.setWindowTitle("合并整部")
        dialog.setWindowModality(Qt.WindowModal)
        dialog.setMinimumDuration(0)
        dialog.canceled.connect(self._merge_cancel.set)
        self._progress_dialog = dialog
        dialog.show()
        key = self._current_key

        def task(report):
            return self.service.merge_drama(key, report, self._merge_cancel)

        self.bridge.run_with_progress(
            "merge",
            task,
            on_ok=self._on_merge_ok,
            on_err=self._on_merge_err,
        )

    def _on_progress(self, task_id: str, ratio: float, downloaded: int, speed: float) -> None:
        if task_id != "merge" or self._progress_dialog is None:
            return
        dialog = self._progress_dialog
        if ratio < 0:
            dialog.setRange(0, 0)
        else:
            dialog.setRange(0, 1000)
            dialog.setValue(int(ratio * 1000))

    def _close_dialog(self) -> None:
        if self._progress_dialog is not None:
            self._progress_dialog.close()
            self._progress_dialog = None

    def _on_merge_ok(self, item: dict) -> None:
        self._close_dialog()
        warning = item.get("warning") or ""
        self.status.setText("合并完成" + (f"（{warning}）" if warning else ""))
        QMessageBox.information(
            self,
            "合并完成",
            f"已生成：{item.get('file')}\n大小：{human_size(item.get('bytes') or 0)}",
        )
        self.refresh()

    def _on_merge_err(self, message: str) -> None:
        self._close_dialog()
        self.status.setText(f"合并失败：{message}")
        QMessageBox.warning(self, "合并失败", message)

    # ---------- 演示 ----------
    def load_demo(self) -> None:
        rows = [
            {
                "drama_key": "duanju:15297",
                "title": "示例短剧 A",
                "completed": 12,
                "total": 76,
                "merged": 1,
                "merged_bytes": 420_000_000,
            },
            {
                "drama_key": "duanju:11792",
                "title": "示例短剧 B",
                "completed": 3,
                "total": 62,
                "merged": 0,
                "merged_bytes": 0,
            },
        ]
        self._rows = rows
        self.drama_table.setRowCount(0)
        for row in rows:
            index = self.drama_table.rowCount()
            self.drama_table.insertRow(index)
            self.drama_table.setItem(index, 0, QTableWidgetItem(row["title"]))
            self.drama_table.setItem(index, 1, QTableWidgetItem(str(row["completed"])))
            self.drama_table.setItem(index, 2, QTableWidgetItem(str(row["total"])))
            self.drama_table.setItem(index, 3, QTableWidgetItem(str(row["merged"])))
        self.drama_table.selectRow(0)
        self._current_key = "duanju:15297"
        self.selected_label.setText("《示例短剧 A》 · 已完成 12/76 集")
        self._episodes = [
            {
                "episode": number,
                "state": "completed" if number <= 12 else ("failed" if number == 13 else "paused"),
                "bytes": 38_000_000 if number <= 12 else 0,
                "file": f"D:/demo/第{number:02d}集.mp4" if number <= 12 else "",
            }
            for number in range(1, 20)
        ]
        self._fill_episodes()
        self._merged = [
            {
                "id": "demo-merged",
                "episodes": 12,
                "bytes": 420_000_000,
                "file": "D:/demo/示例短剧 A_全集.mp4",
            }
        ]
        self._fill_merged()
        self.status.setText("演示数据")
