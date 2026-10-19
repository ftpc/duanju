"""下载页：队列状态、进度、暂停 / 继续 / 重试 / 删除。"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QMessageBox,
    QPushButton,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from duanju.models import STATE_LABELS
from ui.widgets import JobTableModel, PageTitle, ProgressDelegate


class DownloadsPage(QWidget):
    play_file = Signal(str)

    def __init__(self, service, bridge, parent: QWidget | None = None):
        super().__init__(parent)
        self.service = service
        self.bridge = bridge
        self._build()
        self._model.set_jobs(self.service.jobs_snapshot())
        bridge.job_updated.connect(self._on_job)
        bridge.jobs_changed.connect(self._on_jobs)

    # ---------- UI ----------
    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(12)
        root.addWidget(PageTitle("下载", "任务按加入顺序执行；双击已完成任务可直接播放"))

        toolbar = QHBoxLayout()
        for text, handler in (
            ("全部暂停", self._pause_all),
            ("全部继续", self._resume_all),
            ("清理失败", self._clear_failed),
            ("打开下载目录", self._open_dir),
        ):
            button = QPushButton(text)
            button.clicked.connect(handler)
            toolbar.addWidget(button)
        toolbar.addStretch(1)
        self.summary = QPushButton("刷新")
        self.summary.setObjectName("Primary")
        self.summary.clicked.connect(self.refresh)
        toolbar.addWidget(self.summary)
        root.addLayout(toolbar)

        self.table = QTableView()
        self._model = JobTableModel(self)
        self.table.setModel(self._model)
        self.table.setItemDelegateForColumn(3, ProgressDelegate(self.table))
        self.table.setSelectionBehavior(QTableView.SelectRows)
        self.table.setSelectionMode(QTableView.ExtendedSelection)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setColumnWidth(0, 240)
        self.table.setColumnWidth(1, 70)
        self.table.setColumnWidth(2, 90)
        self.table.setColumnWidth(3, 130)
        self.table.setColumnWidth(4, 100)
        self.table.setColumnWidth(5, 100)
        self.table.doubleClicked.connect(self._open_selected)
        root.addWidget(self.table, 1)

        actions = QHBoxLayout()
        self.pause_btn = QPushButton("暂停")
        self.resume_btn = QPushButton("继续")
        self.retry_btn = QPushButton("重试")
        self.remove_btn = QPushButton("删除任务")
        self.remove_btn.setObjectName("Danger")
        self.play_btn = QPushButton("播放")
        self.pause_btn.clicked.connect(lambda: self._for_selected(self.service.pause_job))
        self.resume_btn.clicked.connect(lambda: self._for_selected(self.service.resume_job))
        self.retry_btn.clicked.connect(lambda: self._for_selected(self.service.resume_job))
        self.remove_btn.clicked.connect(self._remove_selected)
        self.play_btn.clicked.connect(self._open_selected)
        for button in (self.pause_btn, self.resume_btn, self.retry_btn, self.remove_btn, self.play_btn):
            actions.addWidget(button)
        actions.addStretch(1)
        self.status = QPushButton("")
        self.status.setFlat(True)
        self.status.setEnabled(False)
        actions.addWidget(self.status)
        root.addLayout(actions)

    # ---------- 事件 ----------
    def _on_job(self, job: dict) -> None:
        self._model.update_job(job)
        self._update_summary()

    def _on_jobs(self, jobs: list[dict]) -> None:
        self._model.set_jobs(jobs)
        self._update_summary()

    def refresh(self) -> None:
        self._model.set_jobs(self.service.jobs_snapshot())
        self._update_summary()

    def _update_summary(self) -> None:
        jobs = [self._model.job_at(row) for row in range(self._model.rowCount())]
        jobs = [job for job in jobs if job]
        downloading = sum(1 for job in jobs if job.get("state") == "downloading")
        queued = sum(1 for job in jobs if job.get("state") == "queued")
        completed = sum(1 for job in jobs if job.get("state") == "completed")
        failed = sum(1 for job in jobs if job.get("state") == "failed")
        self.status.setText(
            f"共 {len(jobs)} 项 · 下载中 {downloading} · 等待 {queued} · 完成 {completed} · 失败 {failed}"
        )

    # ---------- 操作 ----------
    def _selected_jobs(self) -> list[dict]:
        rows = sorted({index.row() for index in self.table.selectionModel().selectedRows()})
        return [job for row in rows if (job := self._model.job_at(row))]

    def _for_selected(self, handler) -> None:
        for job in self._selected_jobs():
            handler(job["id"])

    def _remove_selected(self) -> None:
        jobs = self._selected_jobs()
        if not jobs:
            return
        completed = sum(1 for job in jobs if job.get("state") == "completed")
        text = f"确定删除 {len(jobs)} 个任务？"
        if completed:
            text += f"\n其中 {completed} 个已完成任务的文件也会被删除。"
        if QMessageBox.question(self, "删除任务", text) != QMessageBox.Yes:
            return
        for job in jobs:
            self.service.remove_job(job["id"])

    def _pause_all(self) -> None:
        self.service.pause_all()

    def _resume_all(self) -> None:
        self.service.resume_all()

    def _clear_failed(self) -> None:
        count = self.service.clear_failed()
        self._update_summary()

    def _open_dir(self) -> None:
        from duanju.util import open_in_explorer

        self.service.open_download_dir()
        open_in_explorer(self.service.config.download_path())

    def _open_selected(self, *_args) -> None:
        jobs = self._selected_jobs()
        if not jobs:
            return
        job = jobs[0]
        path = job.get("file") or ""
        if job.get("state") != "completed" or not path:
            QMessageBox.information(self, "播放", "该任务还没有完成下载")
            return
        self.play_file.emit(path)

    # ---------- 演示 ----------
    def load_demo(self) -> None:
        now = 1_760_000_000
        jobs = [
            {
                "id": "demo1",
                "drama_key": "duanju:15297",
                "drama_title": "示例短剧 A",
                "episode": 1,
                "state": "completed",
                "bytes": 38_000_000,
                "total": 38_000_000,
                "speed": 0.0,
                "error": "",
                "file": "",
                "created": now,
                "updated": now,
                "revision": 1,
            },
            {
                "id": "demo2",
                "drama_key": "duanju:15297",
                "drama_title": "示例短剧 A",
                "episode": 2,
                "state": "downloading",
                "bytes": 21_000_000,
                "total": 38_000_000,
                "speed": 4_600_000.0,
                "error": "",
                "file": "",
                "created": now + 1,
                "updated": now + 1,
                "revision": 0,
            },
            {
                "id": "demo3",
                "drama_key": "duanju:15297",
                "drama_title": "示例短剧 A",
                "episode": 3,
                "state": "queued",
                "bytes": 0,
                "total": 0,
                "speed": 0.0,
                "error": "",
                "file": "",
                "created": now + 2,
                "updated": now + 2,
                "revision": 0,
            },
            {
                "id": "demo4",
                "drama_key": "duanju:11792",
                "drama_title": "示例短剧 B",
                "episode": 6,
                "state": "failed",
                "bytes": 0,
                "total": 0,
                "speed": 0.0,
                "error": "站点返回 HTTP 403（人机验证）",
                "file": "",
                "created": now + 3,
                "updated": now + 3,
                "revision": 0,
            },
        ]
        self._model.set_jobs(jobs)
        self._update_summary()
