"""剧库页：按剧查看已下载分集、合并整部、播放 / 打开 / 删除。"""

from __future__ import annotations

import threading
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QProgressDialog,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
    QSpinBox,
)

from duanju.models import STATE_LABELS
from duanju.util import human_size, open_in_explorer
from ui.widgets import PageTitle, small_label


class LibraryPage(QWidget):
    play_playlist = Signal(list, int)

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
        bridge.jobs_changed.connect(self._on_jobs_changed)
        bridge.progress.connect(self._on_progress)

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(12)
        
        root.addWidget(PageTitle("剧库", "已下载的剧集"))

        body_layout = QHBoxLayout()
        body_layout.setSpacing(20)

        # ====== 左侧列表 ======
        left = QWidget()
        left.setFixedWidth(450)
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        
        self.drama_table = QTableWidget(0, 4)
        self.drama_table.setHorizontalHeaderLabels(["剧名", "已完成", "总数", "合并"])
        self.drama_table.verticalHeader().setVisible(False)
        self.drama_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.drama_table.setSelectionMode(QTableWidget.SingleSelection)
        self.drama_table.setEditTriggers(QTableWidget.NoEditTriggers)
        
        header = self.drama_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Stretch)      
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents) 
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        
        self.drama_table.itemSelectionChanged.connect(self._on_drama_selected)
        left_layout.addWidget(self.drama_table, 1)
        
        refresh_button = QPushButton("刷新")
        refresh_button.clicked.connect(self.refresh)
        left_layout.addWidget(refresh_button)
        body_layout.addWidget(left)

        # ====== 右侧列表 ======
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        
        right_top_bar = QHBoxLayout()
        right_top_bar.setContentsMargins(0, 0, 0, 0)
        
        self.selected_label = QLabel("在左侧选择一部剧")
        self.selected_label.setObjectName("PageSubtitle")
        right_top_bar.addWidget(self.selected_label, 0, Qt.AlignBottom | Qt.AlignLeft)
        
        right_top_bar.addStretch(1)
        right_layout.addLayout(right_top_bar)
        
        self.tabs = QTabWidget()
        self.tabs.setContentsMargins(0, -1, 0, 0) 
        
        self.episode_list = QListWidget()
        self.episode_list.setObjectName("Plain")
        # 🎯 恢复正常的单击选中（移除上一版被禁用的逻辑）
        self.episode_list.itemDoubleClicked.connect(lambda _item: self._play_episode())
        self.tabs.addTab(self.episode_list, "分集")
        
        self.merged_list = QListWidget()
        self.merged_list.setObjectName("Plain")
        self.merged_list.setSelectionMode(QListWidget.ExtendedSelection)
        self.merged_list.itemDoubleClicked.connect(lambda _item: self._play_merged())
        self.tabs.addTab(self.merged_list, "合并成品")
        right_layout.addWidget(self.tabs, 1)

        # 底部按钮栏
        actions = QHBoxLayout()
        self.play_btn = QPushButton("播放")
        self.play_btn.clicked.connect(self._play_current)
        
        merge_label = QLabel("合并为")
        merge_label.setStyleSheet("color: #aaa; margin-left: 10px;")
        self.merge_parts_spin = QSpinBox()
        self.merge_parts_spin.setRange(1, 50)
        self.merge_parts_spin.setValue(1)
        self.merge_parts_spin.setSuffix(" 段")
        self.merge_parts_spin.setToolTip("1 代表合并成一个大文件；输入 3 代表分成 3 个文件。")
        
        self.merge_btn = QPushButton("合并整部")
        self.merge_btn.setObjectName("Primary")
        self.merge_btn.clicked.connect(self._merge)
        
        self.open_btn = QPushButton("打开目录")
        self.open_btn.clicked.connect(self._open_dir)
        self.delete_btn = QPushButton("删除选中成品")
        self.delete_btn.setObjectName("Danger")
        self.delete_btn.clicked.connect(self._delete_merged)
        
        for button in (self.play_btn, merge_label, self.merge_parts_spin, self.merge_btn, self.open_btn, self.delete_btn):
            actions.addWidget(button)
            
        actions.addStretch(1)
        self.status = small_label("")
        actions.addWidget(self.status)
        right_layout.addLayout(actions)
        
        body_layout.addWidget(right, 1)
        root.addLayout(body_layout, 1)

    # ---------- 数据 ----------
    def refresh(self) -> None:
        rows = self.service.library()
        self._rows = rows
        self.drama_table.blockSignals(True)
        self.drama_table.setRowCount(0)
        for row in rows:
            index = self.drama_table.rowCount()
            self.drama_table.insertRow(index)
            self.drama_table.setItem(index, 0, QTableWidgetItem(str(row.get("title") or "")))
            self.drama_table.setItem(index, 1, QTableWidgetItem(str(row.get("completed") or 0)))
            self.drama_table.setItem(index, 2, QTableWidgetItem(str(row.get("total") or 0)))
            merged_text = str(row.get("merged") or 0)
            self.drama_table.setItem(index, 3, QTableWidgetItem(merged_text))
        self.drama_table.blockSignals(False)
        
        if rows:
            if not self._current_key:
                self.drama_table.selectRow(0)
            else:
                for i, row in enumerate(self._rows):
                    if row.get("drama_key") == self._current_key:
                        self.drama_table.selectRow(i)
                        break
                else:
                    self.drama_table.selectRow(0)

    def _on_jobs_changed(self, _jobs) -> None:
        self.refresh()
        if self._current_key:
            self._episodes = self.service.episodes(self._current_key)
            self._merged = self.service.merged_items(self._current_key)
            self._fill_episodes()
            self._fill_merged()

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
            
        # 🎯 核心修改：自动选中第一集并获取焦点
        if self.episode_list.count() > 0:
            self.episode_list.setCurrentRow(0)
            self.episode_list.setFocus()

    def _fill_merged(self) -> None:
        self.merged_list.clear()
        for item_data in self._merged:
            size = human_size(item_data.get("bytes") or 0)
            file_path = Path(str(item_data.get("file") or ""))
            display_name = file_path.stem
            text = f"{display_name} · {size}"
            
            item = QListWidgetItem(text)
            item.setData(Qt.UserRole, item_data)
            item.setToolTip(str(item_data.get("file") or ""))
            self.merged_list.addItem(item)

    # ---------- 操作 ----------
    def _play_episode(self) -> None:
        item = self.episode_list.currentItem()
        if item is None:
            return
        
        playlist = []
        start_index = 0
        current_ep = item.data(Qt.UserRole) or {}
        
        sorted_episodes = sorted(self._episodes, key=lambda x: x.get("episode", 0))
        
        for job in sorted_episodes:
            if job.get("state") == "completed" and job.get("file"):
                if str(job.get("episode")) == str(current_ep.get("episode")):
                    start_index = len(playlist)
                playlist.append((f"第{job.get('episode')}集", str(job["file"])))
                
        if playlist:
            self.play_playlist.emit(playlist, start_index)

    def _play_merged(self) -> None:
        item = self.merged_list.currentItem()
        if item is None:
            return
        data = item.data(Qt.UserRole) or {}
        path = str(data.get("file") or "")
        if path:
            self.play_playlist.emit([(Path(path).stem, path)], 0)

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
        selected_items = self.merged_list.selectedItems()
        if not selected_items:
            return
            
        count = len(selected_items)
        msg = f"确定删除选中的 {count} 个合并文件？" if count > 1 else "确定删除这个合并文件？"
        if QMessageBox.question(self, "删除成品", msg) != QMessageBox.Yes:
            return
            
        for item in selected_items:
            data = item.data(Qt.UserRole) or {}
            merged_id = str(data.get("id"))
            if merged_id:
                self.service.delete_merged(merged_id)
                
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
        parts = self.merge_parts_spin.value() 

        def task(report):
            return self.service.merge_drama(key, report, self._merge_cancel, parts)

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
        
        self.refresh()
        if self._current_key:
            self._merged = self.service.merged_items(self._current_key)
            self._fill_merged()
            self.tabs.setCurrentIndex(1)

        QMessageBox.information(
            self,
            "合并完成",
            f"已生成：{item.get('file')}\n大小：{human_size(item.get('bytes') or 0)}",
        )

    def _on_merge_err(self, message: str) -> None:
        self._close_dialog()
        self.status.setText(f"合并失败：{message}")
        QMessageBox.warning(self, "合并失败", message)

    def load_demo(self) -> None:
        pass