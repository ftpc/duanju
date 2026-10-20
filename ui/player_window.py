"""应用内播放器：基于 libmpv 强兼容引擎。"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

try:
    if getattr(sys, 'frozen', False):
        _base_dir = sys._MEIPASS
    else:
        _base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    os.environ['PATH'] = _base_dir + os.pathsep + os.environ.get('PATH', '')
    if hasattr(os, 'add_dll_directory'):
        os.add_dll_directory(_base_dir)
    import ctypes
    try:
        ctypes.CDLL(os.path.join(_base_dir, 'mpv-2.dll'))
    except OSError:
        ctypes.CDLL(os.path.join(_base_dir, 'libmpv-2.dll'))
except Exception as _e:
    print(f"DLL 预加载失败: {_e}", file=sys.stderr)

import mpv
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
    QMessageBox,
    QComboBox, # 新增：用于倍速选择
)

logger = logging.getLogger(__name__)
MPV_AVAILABLE = True


def _format_ms(milliseconds: float) -> str:
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
        if not MPV_AVAILABLE:
            raise RuntimeError("当前环境缺少 libmpv 库，请确保项目根目录存在 libmpv-2.dll")
        if not playlist:
            raise RuntimeError("播放列表为空")
            
        self._playlist = [(str(title), str(path)) for title, path in playlist]
        self._index = max(0, min(int(index), len(self._playlist) - 1))
        self._volume = max(0, min(100, int(volume)))
        self._seeking = False
        self._is_eof_handled = False
        self.is_alive = True
        
        self._build()
        
        try:
            self.player = mpv.MPV(
                wid=str(int(self.video.winId())),
                osc=False,
                input_default_bindings=False,
                input_vo_keyboard=False,
            )
            self.player.volume = self._volume
            self._update_title_loop()
        except Exception as e:
            logger.error("MPV 初始化失败: %s", e)
            raise RuntimeError(f"MPV 初始化失败，请检查 dll 是否正确: {e}")

        self.timer = QTimer(self)
        self.timer.setInterval(500)
        self.timer.timeout.connect(self._on_timer)
        self.timer.start()

        self._load(self._index)

    def append_to_playlist(self, title: str, path: str) -> None:
        if not self.is_alive:
            return
        self._playlist.append((title, path))
        try:
            self.player.command('loadfile', path, 'append')
        except Exception as e:
            logger.warning("动态追加播放失败: %s", e)

    def _build(self) -> None:
        self.setWindowTitle("播放器")
        self.resize(1120, 700)
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.video = QWidget()
        self.video.setStyleSheet("background:#000;")
        self.video.setAttribute(Qt.WidgetAttribute.WA_NativeWindow)
        self.video.setAttribute(Qt.WidgetAttribute.WA_DontCreateNativeAncestors)
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
        self.seek_slider = QSlider(Qt.Orientation.Horizontal)
        self.seek_slider.setRange(0, 1)
        self.seek_slider.sliderPressed.connect(self._seek_start)
        self.seek_slider.sliderReleased.connect(self._seek_end)
        self.seek_slider.sliderMoved.connect(self._seek_moved)
        self.time_label = QLabel("00:00 / 00:00")
        self.time_label.setMinimumWidth(120)
        
        # ================= 新增：倍速选择框 =================
        self.speed_combo = QComboBox()
        self.speed_combo.addItems(["0.5x", "0.8x", "1.0x", "1.25x", "1.5x", "2.0x"])
        self.speed_combo.setCurrentText("1.0x")
        self.speed_combo.setFixedWidth(64)
        self.speed_combo.currentTextChanged.connect(self._on_speed_changed)
        # ====================================================

        self.fullscreen_button = QPushButton("全屏")
        self.fullscreen_button.setFixedWidth(64)
        self.fullscreen_button.clicked.connect(self._toggle_fullscreen)

        controls_layout.addWidget(self.prev_button)
        controls_layout.addWidget(self.play_button)
        controls_layout.addWidget(self.next_button)
        controls_layout.addWidget(self.seek_slider, 1)
        controls_layout.addWidget(self.time_label)
        controls_layout.addWidget(self.speed_combo) # 添加倍速控件
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
        self.volume_slider = QSlider(Qt.Orientation.Horizontal)
        self.volume_slider.setRange(0, 100)
        self.volume_slider.setValue(self._volume)
        self.volume_slider.setFixedWidth(120)
        self.volume_slider.valueChanged.connect(self._on_volume)
        
        bottom_layout.addWidget(self.title_label, 1)
        bottom_layout.addWidget(self.status_label)
        bottom_layout.addWidget(volume_label)
        bottom_layout.addWidget(self.volume_slider)
        layout.addWidget(bottom)

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

    def _load(self, index: int) -> None:
        self._index = index
        self._is_eof_handled = False 
        title, path = self._playlist[index]
        self.setWindowTitle(f"{title} - 播放器")
        self.title_label.setText(f"{title}  ({index + 1}/{len(self._playlist)})")
        
        if not path.startswith(("http://", "https://")) and not Path(path).is_file():
            self.status_label.setText("文件不存在，可能已被删除")
            return
            
        self.status_label.setText("")
        logger.info("🎬 准备播放的视频地址: %s", path)
        
        # 切集时重置倍速为 1.0x
        self.speed_combo.setCurrentText("1.0x")
        self.player.speed = 1.0
        
        self.player.play(path)
        self.play_button.setText("暂停")
        self.prev_button.setEnabled(index > 0)
        self.next_button.setEnabled(index < len(self._playlist) - 1)

    def _toggle_play(self) -> None:
        if self.player.pause:
            self.player.pause = False
            self.play_button.setText("暂停")
        else:
            self.player.pause = True
            self.play_button.setText("播放")

    def _previous(self) -> None:
        if self._index > 0:
            self._load(self._index - 1)

    def _next(self) -> None:
        if self._index < len(self._playlist) - 1:
            self._load(self._index + 1)
        else:
            self.player.pause = True
            self.play_button.setText("播放")
            QMessageBox.information(self, "播放结束", "已经播放完全部剧集！")
            self._index = 0
            self._load(0)

    def _seek_start(self) -> None:
        self._seeking = True

    def _seek_end(self) -> None:
        target_sec = self.seek_slider.value() / 1000.0
        self.player.seek(target_sec, reference="absolute")
        self._seeking = False

    def _seek_moved(self, position: int) -> None:
        self.time_label.setText(f"{_format_ms(position)} / {_format_ms(self.player.duration * 1000 if self.player.duration else 0)}")

    def _seek_relative(self, delta_ms: int) -> None:
        self.player.seek(delta_ms / 1000.0, reference="relative")

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

    def _on_timer(self) -> None:
        if self.player.duration is None:
            return
            
        pos_ms = (self.player.time_pos or 0) * 1000
        dur_ms = self.player.duration * 1000
        
        if not self._seeking:
            self.seek_slider.setRange(0, max(1, int(dur_ms)))
            self.seek_slider.setValue(int(pos_ms))
            
        self.time_label.setText(f"{_format_ms(pos_ms)} / {_format_ms(dur_ms)}")
        
        if dur_ms > 0 and pos_ms >= dur_ms - 500:
            if not self._is_eof_handled:
                self._is_eof_handled = True
                QTimer.singleShot(100, self._next)

    def _update_title_loop(self) -> None:
        @self.player.event_callback('end-file')
        def on_end_file(event):
            reason = event.data.get('reason')
            if reason == 'error':
                logger.error("MPV 播放发生错误")
            elif reason == 'eof' and not self._is_eof_handled:
                self._is_eof_handled = True
                QTimer.singleShot(100, self._next)

    def _on_volume(self, value: int) -> None:
        if hasattr(self, 'player'):
            self.player.volume = value

    # ================= 新增：处理倍速切换 =================
    def _on_speed_changed(self, text: str) -> None:
        try:
            speed_val = float(text.replace("x", ""))
            if hasattr(self, 'player'):
                self.player.speed = speed_val
        except ValueError:
            pass
    # ====================================================

    def closeEvent(self, event) -> None:  # noqa: N802, ANN001
        self.is_alive = False
        self.timer.stop()
        try:
            self.player.terminate()
        except Exception:
            pass
        super().closeEvent(event)