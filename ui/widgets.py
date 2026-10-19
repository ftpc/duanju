"""复用控件：日志面板、任务表模型 / 进度委托、选集网格。"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (
    QApplication,
    QGridLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QStyle,
    QStyleOptionProgressBar,
    QStyledItemDelegate,
    QWidget,
)

from duanju.models import STATE_LABELS
from duanju.util import human_size, human_speed

JOB_COLUMNS = ["剧名", "集", "状态", "进度", "速度", "大小", "错误"]


class PageTitle(QWidget):
    def __init__(self, title: str, subtitle: str = "", parent: QWidget | None = None):
        super().__init__(parent)
        from PySide6.QtWidgets import QVBoxLayout

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        title_label = QLabel(title)
        title_label.setObjectName("PageTitle")
        layout.addWidget(title_label)
        if subtitle:
            subtitle_label = QLabel(subtitle)
            subtitle_label.setObjectName("PageSubtitle")
            subtitle_label.setWordWrap(True)
            layout.addWidget(subtitle_label)


class LogPanel(QPlainTextEdit):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setReadOnly(True)
        self.setMaximumBlockCount(5000)
        self.setPlaceholderText("运行日志会显示在这里")

    def append_line(self, text: str) -> None:
        self.appendPlainText(text)
        self.verticalScrollBar().setValue(self.verticalScrollBar().maximum())


class JobTableModel(QAbstractTableModel):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._jobs: list[dict[str, Any]] = []
        self._index: dict[str, int] = {}

    # ---------- Qt 接口 ----------
    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self._jobs)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(JOB_COLUMNS)

    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.DisplayRole):  # noqa: N802
        if role != Qt.DisplayRole:
            return None
        if orientation == Qt.Horizontal:
            return JOB_COLUMNS[section] if 0 <= section < len(JOB_COLUMNS) else None
        return section + 1

    def data(self, index: QModelIndex, role: int = Qt.DisplayRole):
        if not index.isValid():
            return None
        job = self._jobs[index.row()]
        column = index.column()
        state = job.get("state", "queued")
        total = int(job.get("total") or 0)
        downloaded = int(job.get("bytes") or 0)
        ratio = downloaded / total if total > 0 else (1.0 if state == "completed" else None)
        if role == Qt.DisplayRole:
            if column == 0:
                return job.get("drama_title", "")
            if column == 1:
                return f"第{job.get('episode', 0)}集"
            if column == 2:
                return STATE_LABELS.get(state, state)
            if column == 3:
                if state == "completed":
                    return "100%"
                if ratio is not None:
                    return f"{ratio * 100:.0f}%"
                return human_size(downloaded) if downloaded else "—"
            if column == 4:
                return human_speed(float(job.get("speed") or 0)) if state == "downloading" else ""
            if column == 5:
                return human_size(downloaded) if downloaded else ""
            if column == 6:
                return job.get("error", "")
        if role == Qt.UserRole and column == 3:
            return ratio if ratio is not None else -1.0
        if role == Qt.TextAlignmentRole and column in (1, 2, 3, 4, 5):
            return int(Qt.AlignCenter)
        if role == Qt.ToolTipRole and column == 6:
            return job.get("error", "")
        return None

    # ---------- 数据操作 ----------
    def set_jobs(self, jobs: list[dict[str, Any]]) -> None:
        self.beginResetModel()
        self._jobs = [dict(job) for job in jobs]
        self._index = {str(job["id"]): row for row, job in enumerate(self._jobs)}
        self.endResetModel()

    def update_job(self, job: dict[str, Any]) -> None:
        job_id = str(job.get("id"))
        row = self._index.get(job_id)
        if row is None:
            row = len(self._jobs)
            self.beginInsertRows(QModelIndex(), row, row)
            self._jobs.append(dict(job))
            self._index[job_id] = row
            self.endInsertRows()
            return
        self._jobs[row] = dict(job)
        left = self.index(row, 0)
        right = self.index(row, len(JOB_COLUMNS) - 1)
        self.dataChanged.emit(left, right)

    def remove_job(self, job_id: str) -> None:
        row = self._index.get(job_id)
        if row is None:
            return
        self.beginRemoveRows(QModelIndex(), row, row)
        self._jobs.pop(row)
        self.endRemoveRows()
        self._index = {str(job["id"]): index for index, job in enumerate(self._jobs)}

    def job_at(self, row: int) -> dict[str, Any] | None:
        if 0 <= row < len(self._jobs):
            return self._jobs[row]
        return None


class ProgressDelegate(QStyledItemDelegate):
    """自绘进度条（不依赖平台样式，深/浅色主题都一致）。"""

    def paint(self, painter, option, index):  # noqa: N802, ANN001
        ratio = index.data(Qt.UserRole)
        if ratio is None or (isinstance(ratio, (int, float)) and ratio < 0):
            super().paint(painter, option, index)
            return
        value = max(0.0, min(1.0, float(ratio)))
        selected = bool(option.state & QStyle.State_Selected)
        if selected:
            painter.fillRect(option.rect, option.palette.highlight())
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing, True)
        rect = QRectF(option.rect.adjusted(6, 6, -6, -6))
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor("#3a4256" if selected else "#2a3040"))
        painter.drawRoundedRect(rect, 5, 5)
        if value > 0:
            filled = QRectF(rect)
            filled.setWidth(rect.width() * value)
            painter.setBrush(QColor("#ff5f57"))
            painter.drawRoundedRect(filled, 5, 5)
        painter.setPen(QColor("#ffffff"))
        painter.setFont(option.font)
        painter.drawText(option.rect, Qt.AlignCenter, f"{value * 100:.0f}%")
        painter.restore()


class EpisodeGrid(QWidget):
    selection_changed = Signal(int)

    COLUMNS = 10

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._layout = QGridLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(6)
        self._buttons: dict[int, QPushButton] = {}
        self.setObjectName("EpisodeGrid")
        self.setAttribute(Qt.WA_StyledBackground, True)

    def clear(self) -> None:
        while self._layout.count():
            item = self._layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                # 只 deleteLater 会留下残影（父控件还在、仍在绘制），必须先摘除父级
                widget.hide()
                widget.setParent(None)
                widget.deleteLater()
        self._buttons.clear()
        self.selection_changed.emit(0)

    def set_episodes(self, numbers: list[int], completed: set[int] | None = None) -> None:
        self.clear()
        completed = completed or set()
        for position, number in enumerate(numbers):
            button = QPushButton(str(number))
            button.setObjectName("EpisodeButton")
            button.setCheckable(True)
            button.setMinimumHeight(34)
            button.setCursor(Qt.PointingHandCursor)
            button.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            if number in completed:
                button.setProperty("completed", True)
                button.setToolTip("已下载")
                button.setText(f"√{number}")
            else:
                button.setProperty("completed", False)
                button.setToolTip(f"第{number}集")
            button.toggled.connect(lambda _checked: self._notify())
            self._layout.addWidget(button, position // self.COLUMNS, position % self.COLUMNS)
            self._buttons[number] = button

    def numbers(self) -> list[int]:
        return sorted(self._buttons)

    def selected(self) -> list[int]:
        return [number for number, button in self._buttons.items() if button.isChecked()]

    def count(self) -> int:
        return len(self.selected())

    def set_all(self, checked: bool) -> None:
        for button in self._buttons.values():
            button.setChecked(checked)
        self._notify()

    def invert(self) -> None:
        for button in self._buttons.values():
            button.setChecked(not button.isChecked())
        self._notify()

    def select_range(self, start: int, end: int) -> None:
        low, high = min(start, end), max(start, end)
        for number, button in self._buttons.items():
            button.setChecked(low <= number <= high)
        self._notify()

    def select_numbers(self, numbers: set[int]) -> None:
        for number, button in self._buttons.items():
            button.setChecked(number in numbers)
        self._notify()

    def _notify(self) -> None:
        self.selection_changed.emit(self.count())


def small_label(text: str, object_name: str = "Muted") -> QLabel:
    label = QLabel(text)
    label.setObjectName(object_name)
    return label
