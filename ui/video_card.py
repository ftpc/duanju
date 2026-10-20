"""视频卡片控件。

显示封面图、剧名和状态信息，点击时发出信号。
"""

from __future__ import annotations

import logging

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QMovie, QPainter, QPixmap
from PySide6.QtWidgets import QLabel, QSizePolicy, QVBoxLayout, QWidget

logger = logging.getLogger(__name__)


class VideoCard(QWidget):
    """视频卡片控件，显示封面图和基本信息。

    使用示例：
        card = VideoCard(title="剧名", cover_url="https://...", status="全60集")
        card.clicked.connect(lambda drama_id: print(drama_id))
        card.set_cover_pixmap(pixmap)
    """

    # 点击信号，携带 drama_id
    clicked = Signal(str)

    def __init__(
        self,
        title: str = "",
        cover_url: str = "",
        status: str = "",
        drama_id: str = "",
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self._title = title
        self._cover_url = cover_url
        self.image_url = cover_url  # 公开属性，用于信号匹配
        self._status = status
        self._drama_id = drama_id
        self._pixmap: QPixmap | None = None

        # 构建 UI
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        # 封面图标签
        self._cover_label = QLabel()
        self._cover_label.setFixedSize(160, 220)
        self._cover_label.setAlignment(Qt.AlignCenter)
        self._cover_label.setStyleSheet(
            "QLabel { border-radius: 8px; background-color: #2a3040; }"
        )
        # 设置占位图
        self._cover_label.setText("加载中...")
        self._cover_label.setObjectName("CoverLabel")
        layout.addWidget(self._cover_label)

        # 剧名标签
        self._title_label = QLabel(title)
        self._title_label.setWordWrap(True)
        self._title_label.setMaximumHeight(40)
        self._title_label.setStyleSheet(
            "QLabel { font-weight: bold; color: #ffffff; }"
        )
        self._title_label.setTextInteractionFlags(
            Qt.TextSelectableByMouse | Qt.TextSelectableByKeyboard
        )
        layout.addWidget(self._title_label)

        # 状态标签
        self._status_label = QLabel(status)
        self._status_label.setObjectName("Muted")
        self._status_label.setStyleSheet(
            "QLabel { color: #8899aa; font-size: 12px; }"
        )
        layout.addWidget(self._status_label)

        # 设置最小尺寸
        self.setMinimumWidth(170)
        self.setMaximumWidth(180)
        from PySide6.QtWidgets import QSizePolicy
        self.setSizePolicy(
            QSizePolicy.Preferred,
            QSizePolicy.Preferred,
        )

    def set_title(self, title: str) -> None:
        """更新剧名。"""
        self._title = title
        self._title_label.setText(title)

    def set_status(self, status: str) -> None:
        """更新状态文本。"""
        self._status = status
        self._status_label.setText(status)

    def set_drama_id(self, drama_id: str) -> None:
        """更新 drama_id。"""
        self._drama_id = drama_id

    def set_cover_url(self, url: str) -> None:
        """更新封面图 URL（不立即加载）。"""
        self._cover_url = url

    def set_cover_pixmap(self, pixmap: QPixmap) -> None:
        """设置封面图（已加载完成）。"""
        if pixmap.isNull():
            self._cover_label.setText("加载失败")
            self._cover_label.setStyleSheet(
                "QLabel { border-radius: 8px; background-color: #2a3040; color: #888; }"
            )
            return
        # 缩放并保持宽高比
        scaled = pixmap.scaled(
            160, 220,
            Qt.KeepAspectRatio,
            Qt.SmoothTransformation,
        )
        self._pixmap = scaled
        self._cover_label.setPixmap(scaled)
        # 恢复样式
        self._cover_label.setStyleSheet(
            "QLabel { border-radius: 8px; background-color: #2a3040; }"
        )
        # 强制重绘，确保立即显示
        self.update()

    def set_cover_error(self) -> None:
        """显示封面图加载失败状态。"""
        self._cover_label.setText("加载失败")
        self._cover_label.setStyleSheet(
            "QLabel { border-radius: 8px; background-color: #2a3040; color: #888; }"
        )
        self.update()

    def mousePressEvent(self, event) -> None:  # noqa: N802
        """处理点击事件。"""
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self._drama_id)
        super().mousePressEvent(event)

    def get_drama_info(self) -> dict:
        """返回卡片信息。"""
        return {
            "title": self._title,
            "cover_url": self._cover_url,
            "status": self._status,
            "drama_id": self._drama_id,
        }
