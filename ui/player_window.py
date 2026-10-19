"""应用内播放器：基于 QtMultimedia（Windows Media Foundation）。"""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

logger = logging.getLogger(__name__)

try:  # QtMultimedia 在 PySide6-Addons 中
    from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
    from PySide6.QtMultimediaWidgets import QVideoWidget

    MULTIMEDIA_AVAILABLE = True
except Exception as exc:  # noqa: BLE001
    MULTIMEDIA_AVAILABLE = False
    logger.warning("QtMultimedia 不可用：%s", exc)


def _format_ms(milliseconds: int) -> str:
    seconds = max(0, int(milliseconds)) // 1000
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


class PlayerWindow(QMainWindow):
    def __init__(
        self,
        playlist: list[tuple[str, str]],
        index: int = 0,
        volume: int = 80,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        if not MULTIMEDIA_AVAILABLE:
            raise RuntimeError("当前环境缺少 QtMultimedia，无法使用内置播放器")
        if not playlist:
            raise RuntimeError("播放列表为空")
        self._playlist = [(str(title), str(path)) for title, path in playlist]
        self._index = max(0, min(int(index), len(self._playlist) - 1))
        self._volume = max(0, min(100, int(volume)))
        self._seeking = False
        self._build()
        self._load(self._index)

    # ---------- UI ----------
    def _build(self) -> None:
        self.setWindowTitle("播放器")
        self.resize(1120, 700)
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.video = QVideoWidget()
        self.video.setStyleSheet("background:#000;")
        layout.addWidget(self.video, 1)

        controls = QWidget()
        controls_layout = QHBoxLayout(controls)
        controls_layout.setContentsMargins(14, 8, 14, 8)
        controls_layout.setSpacing(10)

        self.play_button = QPushButton("暂停")
        self.play_button.setFixedWidth(72)
        self.play_button.clicked.connect(self._toggle_play)
        self.prev_button = QPushButton("上一集")
        self.prev_button.clicked.connect(self._previous)
        self.next_button = QPushButton("下一集")
        self.next_button.clicked.connect(self._next)
        self.seek_slider = QSlider(Qt.Horizontal)
        self.seek_slider.setRange(0, 1)
        self.seek_slider.sliderPressed.connect(self._seek_start)
        self.seek_slider.sliderReleased.connect(self._seek_end)
        self.seek_slider.sliderMoved.connect(self._seek_moved)
        self.time_label = QLabel("00:00 / 00:00")
        self.time_label.setMinimumWidth(120)
        self.fullscreen_button = QPushButton("全屏")
        self.fullscreen_button.setFixedWidth(64)
        self.fullscreen_button.clicked.connect(self._toggle_fullscreen)

        controls_layout.addWidget(self.prev_button)
        controls_layout.addWidget(self.play_button)
        controls_layout.addWidget(self.next_button)
        controls_layout.addWidget(self.seek_slider, 1)
        controls_layout.addWidget(self.time_label)
        controls_layout.addWidget(self.fullscreen_button)
        layout.addWidget(controls)

        bottom = QWidget()
        bottom_layout = QHBoxLayout(bottom)
        bottom_layout.setContentsMargins(14, 0, 14, 10)
        self.title_label = QLabel("")
        self.title_label.setObjectName("PageSubtitle")
        self.status_label = QLabel("")
        self.status_label.setObjectName("StatusText")
        volume_label = QLabel("音量")
        self.volume_slider = QSlider(Qt.Horizontal)
        self.volume_slider.setRange(0, 100)
        self.volume_slider.setValue(self._volume)
        self.volume_slider.setFixedWidth(120)
        bottom_layout.addWidget(self.title_label, 1)
        bottom_layout.addWidget(self.status_label)
        bottom_layout.addWidget(volume_label)
        bottom_layout.addWidget(self.volume_slider)
        layout.addWidget(bottom)

        self.player = QMediaPlayer(self)
        self.audio = QAudioOutput(self)
        self.audio.setVolume(self._volume / 100)
        self.player.setAudioOutput(self.audio)
        self.player.setVideoOutput(self.video)
        self.player.positionChanged.connect(self._on_position)
        self.player.durationChanged.connect(self._on_duration)
        self.player.playbackStateChanged.connect(self._on_state)
        self.player.mediaStatusChanged.connect(self._on_status)
        self.player.errorOccurred.connect(self._on_error)
        self.volume_slider.valueChanged.connect(self._on_volume)

        self._shortcuts = []
        for key, handler in (
            ("Space", self._toggle_play),
            ("Left", lambda: self._seek_relative(-5000)),
            ("Right", lambda: self._seek_relative(5000)),
            ("F", self._toggle_fullscreen),
            ("F11", self._toggle_fullscreen),
            ("Escape", self._escape),
        ):
            shortcut = QShortcut(QKeySequence(key), self)
            shortcut.activated.connect(handler)
            self._shortcuts.append(shortcut)

    # ---------- 播放 ----------
    def _load(self, index: int) -> None:
        self._index = index
        title, path = self._playlist[index]
        self.setWindowTitle(f"{title} - 播放器")
        self.title_label.setText(f"{title}  ({index + 1}/{len(self._playlist)})")
        if not Path(path).is_file():
            self.status_label.setText("文件不存在，可能已被删除")
            return
        self.player.setSource(QUrl.fromLocalFile(path))
        self.player.play()
        self.play_button.setText("暂停")
        self.prev_button.setEnabled(index > 0)
        self.next_button.setEnabled(index < len(self._playlist) - 1)

    def _toggle_play(self) -> None:
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
        else:
            self.player.play()

    def _previous(self) -> None:
        if self._index > 0:
            self._load(self._index - 1)

    def _next(self) -> None:
        if self._index < len(self._playlist) - 1:
            self._load(self._index + 1)

    def _seek_start(self) -> None:
        self._seeking = True

    def _seek_end(self) -> None:
        self.player.setPosition(self.seek_slider.value())
        self._seeking = False

    def _seek_moved(self, position: int) -> None:
        self.time_label.setText(f"{_format_ms(position)} / {_format_ms(self.player.duration())}")

    def _seek_relative(self, delta: int) -> None:
        position = max(0, self.player.position() + delta)
        self.player.setPosition(position)

    def _toggle_fullscreen(self) -> None:
        if self.isFullScreen():
            self.showNormal()
        else:
            self.showFullScreen()

    def _escape(self) -> None:
        if self.isFullScreen():
            self.showNormal()
        else:
            self.close()

    # ---------- 信号 ----------
    def _on_position(self, position: int) -> None:
        if not self._seeking:
            self.seek_slider.setValue(position)
        self.time_label.setText(f"{_format_ms(position)} / {_format_ms(self.player.duration())}")

    def _on_duration(self, duration: int) -> None:
        self.seek_slider.setRange(0, max(1, duration))

    def _on_state(self, state) -> None:  # noqa: ANN001
        if state == QMediaPlayer.PlaybackState.PlayingState:
            self.play_button.setText("暂停")
        else:
            self.play_button.setText("播放")

    def _on_status(self, status) -> None:  # noqa: ANN001
        if status == QMediaPlayer.MediaStatus.EndOfMedia:
            if self._index < len(self._playlist) - 1:
                self._load(self._index + 1)
            else:
                self.play_button.setText("播放")
        elif status == QMediaPlayer.MediaStatus.InvalidMedia:
            self.status_label.setText("无法识别该媒体文件")

    def _on_error(self, _error, error_string: str) -> None:  # noqa: ANN001
        logger.warning("播放失败：%s", error_string)
        self.status_label.setText(f"播放失败：{error_string}")

    def _on_volume(self, value: int) -> None:
        self.audio.setVolume(value / 100)

    def closeEvent(self, event) -> None:  # noqa: N802, ANN001
        self.player.stop()
        super().closeEvent(event)
