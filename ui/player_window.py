"""应用内播放器：基于 libmpv 强兼容引擎。"""

from __future__ import annotations

import logging
import os
import sys
import time
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
from PySide6.QtCore import Qt, QTimer, Signal
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
    QComboBox,
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
    # mpv 事件回调运行在 mpv 自己的线程，直接操作 Qt 控件不安全，
    # 统一通过信号派发回 GUI 线程处理。
    _end_file_signal = Signal(str, int)

    def __init__(
        self,
        playlist: list[tuple[str, str]],
        index: int = 0,
        volume: int = 80,
        parent: QWidget | None = None,
        headers: dict[str, str] | None = None,
        tls_ca_file: str | None = None,
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
        # 下载/取流时用的请求头（UA / Referer 等）在这里复用给 mpv，
        # 避免站点一旦校验 Referer 就出现"下载正常、播放黑屏"。
        self._headers = {str(k): str(v) for k, v in (headers or {}).items()}
        self._tls_ca_file = tls_ca_file or os.environ.get("DUANJU_MPV_CA", "")
        self._tls_fallback_done = False
        self._buffer_wait_start = 0.0
        self._buffer_hint_shown = False
        self._playback_failed = False
        # 网络类瞬时故障（DNS 抖动、连接被重置、CF 边缘节点抽风）自动重试
        self._net_retry_count = 0
        self._net_retry_limit = 2
        self._end_file_signal.connect(self._on_end_file_ui)

        self._build()

        try:
            mpv_opts: dict = {
                "wid": str(int(self.video.winId())),
                "osc": False,
                "input_default_bindings": False,
                "input_vo_keyboard": False,
                # 网络容错：TUN 代理环境下首帧可能明显变慢
                "network_timeout": 30,
                "cache": "yes",
                "log_handler": self._on_mpv_log,
                "loglevel": "error",
            }
            ua = self._headers.get("User-Agent")
            if ua:
                mpv_opts["user_agent"] = ua
            if self._tls_ca_file:
                mpv_opts["tls_ca_file"] = self._tls_ca_file
            self.player = mpv.MPV(**mpv_opts)
            self._apply_headers(self._headers)
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

    # ---------- 请求头 / 日志 ----------
    @staticmethod
    def _header_fields(headers: dict[str, str] | None) -> list[str]:
        fields = []
        for key, value in (headers or {}).items():
            key = str(key).strip()
            value = str(value).strip()
            if not key or not value:
                continue
            fields.append(f"{key}: {value}")
        return fields

    def _apply_headers(self, headers: dict[str, str] | None) -> None:
        fields = self._header_fields(headers)
        if not fields:
            return
        try:
            self.player.http_header_fields = fields
        except Exception as exc:  # noqa: BLE001 - 老版本 mpv 可能不支持
            logger.warning("设置 http-header-fields 失败：%s", exc)
            return
        referer = next(
            (f.split(":", 1)[1].strip() for f in fields if f.lower().startswith("referer:")), ""
        )
        if referer:
            try:
                self.player.referrer = referer
            except Exception:  # noqa: BLE001
                pass
        logger.info("播放器已应用请求头：%s", ", ".join(f.split(":", 1)[0] for f in fields))

    def _on_mpv_log(self, level, prefix, text) -> None:
        """mpv 错误日志落盘（原实现完全没有日志，出错只有一块黑屏）。"""
        try:
            logger.error("MPV[%s] %s", prefix, str(text).strip())
        except Exception:  # noqa: BLE001
            pass

    def append_to_playlist(self, title: str, path: str, headers: dict[str, str] | None = None) -> None:
        if not self.is_alive:
            return
        self._playlist.append((title, path))
        if headers:
            self._headers = {str(k): str(v) for k, v in headers.items()}
            self._apply_headers(self._headers)
        try:
            self.player.command('loadfile', path, 'append')
        except Exception as e:
            logger.warning("动态追加播放失败: %s", e)
        # 追加只改数据，不动当前播放；但标题计数与"下一集"按钮必须同步刷新，
        # 否则界面会一直显示 (1/1) 且下一集置灰，表现为"只解析到 1 集"。
        self._refresh_playlist_ui()

    def _refresh_playlist_ui(self) -> None:
        """刷新播放列表相关界面状态：标题计数、上一集/下一集可用性。"""
        if not self._playlist or not (0 <= self._index < len(self._playlist)):
            return
        title = self._playlist[self._index][0]
        self.setWindowTitle(f"{title} - 播放器")
        self.title_label.setText(f"{title}  ({self._index + 1}/{len(self._playlist)})")
        self.prev_button.setEnabled(self._index > 0)
        self.next_button.setEnabled(self._index < len(self._playlist) - 1)
        self._sync_playlist_combo()

    def _sync_playlist_combo(self) -> None:
        """把内部播放列表同步到界面的选集下拉框。"""
        combo = getattr(self, "playlist_combo", None)
        if combo is None:
            return
        combo.blockSignals(True)
        try:
            if combo.count() != len(self._playlist):
                combo.clear()
                for idx, (item_title, _path) in enumerate(self._playlist):
                    combo.addItem(f"{idx + 1}. {item_title}")
            if 0 <= self._index < combo.count():
                combo.setCurrentIndex(self._index)
        finally:
            combo.blockSignals(False)

    def _on_playlist_pick(self, index: int) -> None:
        if 0 <= index < len(self._playlist) and index != self._index:
            self._load(index)

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
        
        self.speed_combo = QComboBox()
        self.speed_combo.addItems(["0.5x", "0.8x", "1.0x", "1.25x", "1.5x", "2.0x"])
        self.speed_combo.setCurrentText("1.0x")
        self.speed_combo.setFixedWidth(64)
        self.speed_combo.currentTextChanged.connect(self._on_speed_changed)

        self.fullscreen_button = QPushButton("全屏")
        self.fullscreen_button.setFixedWidth(64)
        self.fullscreen_button.clicked.connect(self._toggle_fullscreen)

        controls_layout.addWidget(self.prev_button)
        controls_layout.addWidget(self.play_button)
        controls_layout.addWidget(self.next_button)
        controls_layout.addWidget(self.seek_slider, 1)
        controls_layout.addWidget(self.time_label)
        controls_layout.addWidget(self.speed_combo)
        controls_layout.addWidget(self.fullscreen_button)
        layout.addWidget(controls)

        bottom = QWidget()
        bottom_layout = QHBoxLayout(bottom)
        bottom_layout.setContentsMargins(14, 0, 14, 10)
        self.title_label = QLabel("")
        self.title_label.setObjectName("PageSubtitle")
        # 可见的选集列表：后台每追加一集就同步增长，用户能直观看到"75 集都进来了"，
        # 也能直接点选跳集（此前只有 (1/1) 计数，追加再多也看不出来）。
        self.playlist_combo = QComboBox()
        self.playlist_combo.setMinimumWidth(220)
        self.playlist_combo.setMaxVisibleItems(20)
        self.playlist_combo.setToolTip("已解析的剧集列表（后台仍在追加）")
        self.playlist_combo.activated.connect(self._on_playlist_pick)
        self.status_label = QLabel("")
        self.status_label.setObjectName("StatusText")
        volume_label = QLabel("音量")
        self.volume_slider = QSlider(Qt.Orientation.Horizontal)
        self.volume_slider.setRange(0, 100)
        self.volume_slider.setValue(self._volume)
        self.volume_slider.setFixedWidth(120)
        self.volume_slider.valueChanged.connect(self._on_volume)
        
        bottom_layout.addWidget(self.title_label, 1)
        bottom_layout.addWidget(self.playlist_combo)
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
        self._buffer_wait_start = time.monotonic()
        self._buffer_hint_shown = False
        self._playback_failed = False
        self._net_retry_count = 0
        title, path = self._playlist[index]
        self.setWindowTitle(f"{title} - 播放器")
        self.title_label.setText(f"{title}  ({index + 1}/{len(self._playlist)})")
        self.prev_button.setEnabled(index > 0)
        self.next_button.setEnabled(index < len(self._playlist) - 1)
        self._sync_playlist_combo()

        if not path.startswith(("http://", "https://")) and not Path(path).is_file():
            self.status_label.setText("文件不存在，可能已被删除")
            return

        self.status_label.setText("正在连接…" if path.startswith(("http://", "https://")) else "")
        logger.info("🎬 准备播放的视频地址: %s", path)

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
        if self._playback_failed:
            # 已明确失败：保留错误文案，不要再被缓冲提示覆盖
            return
        if self.player.duration is None:
            # 首帧等待期：给出"正在缓冲"进度，避免界面静止被判定为卡死
            self._update_buffering_status()
            return

        # 首帧已到，清掉缓冲提示
        if self.status_label.text().startswith("正在缓冲") or self.status_label.text() == "正在连接…":
            self.status_label.setText("")
        self._net_retry_count = 0

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

    def _update_buffering_status(self) -> None:
        waited = time.monotonic() - (self._buffer_wait_start or time.monotonic())
        seconds = int(waited)
        if seconds < 1:
            self.status_label.setText("正在缓冲…")
            return
        if seconds < 8:
            self.status_label.setText(f"正在缓冲…已等待 {seconds} 秒")
            return
        if not self._buffer_hint_shown:
            self._buffer_hint_shown = True
            logger.warning("首帧等待超过 8 秒，可能是网络或站点限速")
        self.status_label.setText(
            f"正在缓冲…已等待 {seconds} 秒（若长时间无画面，可改用“加入下载”）"
        )

    def _end_file_reason_name(self, reason) -> str:
        """把 mpv 的 end-file reason 整型转成名字（python-mpv 1.0.8 用 MpvEventEndFile 常量）。"""
        try:
            code = int(reason)
        except (TypeError, ValueError):
            return "unknown"
        mapping = {}
        for name in ("EOF", "QUIT", "ERROR", "REDIRECT", "RESTARTED", "ABORTED"):
            value = getattr(mpv.MpvEventEndFile, name, None)
            if isinstance(value, int):
                mapping[value] = name.lower()
        return mapping.get(code, str(code))

    def _on_end_file_ui(self, reason: str, error_code: int) -> None:
        """在 GUI 线程处理 end-file（由 _end_file_signal 派发）。"""
        if reason == "error":
            self._handle_playback_error(error_code)
        elif reason == "eof" and not self._is_eof_handled:
            self._is_eof_handled = True
            QTimer.singleShot(100, self._next)

    @staticmethod
    def _error_code_name(code: int) -> str:
        """mpv.ErrorCode 是普通类（非 Enum），只能反查属性名。"""
        for name in dir(mpv.ErrorCode):
            if name.startswith("_") or name == "EXCEPTION_DICT":
                continue
            if getattr(mpv.ErrorCode, name, None) == code:
                return name
        return f"ERROR_{code}"

    def _handle_playback_error(self, error_code) -> None:
        try:
            code = int(error_code)
        except (TypeError, ValueError):
            code = 0
        detail = self._error_code_name(code)

        current_path = self._playlist[self._index][1]
        is_network = current_path.startswith(("http://", "https://"))
        cn_reason = {
            -13: "无法打开该视频地址（可能已失效，或被站点拒绝访问）",
            -15: "视频输出初始化失败",
            -14: "音频输出初始化失败",
            -16: "没有可播放的内容",
        }.get(code, "")
        hint = ""
        low = detail.lower()
        if cn_reason:
            hint = ""
        elif "certificate" in low or "tls" in low or "ssl" in low:
            hint = "（证书校验失败）"
        elif "loading" in low or "failed" in low:
            hint = "（地址无法打开，可能已失效或站点拒绝访问）"

        message = f"{cn_reason or detail}{hint}"
        logger.error("MPV 播放失败：%s [%s/%s]，地址：%s", message, detail, code, current_path)

        # 网络类瞬时故障（DNS 抖动 / 连接被重置 / 边缘节点抽风）：自动重试若干次
        if is_network and code == -13 and self._net_retry_count < self._net_retry_limit:
            self._net_retry_count += 1
            attempt = self._net_retry_count
            self._playback_failed = False
            self._is_eof_handled = False
            self._buffer_wait_start = time.monotonic()
            self._buffer_hint_shown = False
            self.status_label.setText(
                f"连接失败，正在自动重试（{attempt}/{self._net_retry_limit}）…"
            )
            logger.warning("网络类播放失败，第 %s 次自动重试：%s", attempt, current_path)
            QTimer.singleShot(2000, lambda p=current_path: self._retry_play(p))
            return

        # 证书类错误：自动降级为不校验证书重试一次（P2 容错）
        if (
            not self._tls_fallback_done
            and is_network
            and ("certificate" in low or "tls" in low or "ssl" in low)
        ):
            self._tls_fallback_done = True
            try:
                self.player.command("set", "tls-verify", "no")
                self.status_label.setText("证书校验失败，已切换为忽略证书重试…")
                logger.warning("证书校验失败，自动降级 tls-verify=no 重试一次")
                self._playback_failed = False
                self._buffer_wait_start = time.monotonic()
                self.player.play(current_path)
                return
            except Exception as exc:  # noqa: BLE001
                logger.error("证书降级重试失败：%s", exc)

        self._playback_failed = True
        self.status_label.setText(f"播放失败：{message}")
        self.play_button.setText("播放")

    def _retry_play(self, path: str) -> None:
        """网络类错误后的自动重试（延迟执行，避免 mpv 状态未就绪）。"""
        if not self.is_alive:
            return
        if self._index >= len(self._playlist) or self._playlist[self._index][1] != path:
            return  # 用户已切集/切片，放弃本次重试
        try:
            logger.info("自动重试播放：%s", path)
            self.player.play(path)
            self.play_button.setText("暂停")
        except Exception as exc:  # noqa: BLE001
            logger.error("自动重试播放失败：%s", exc)
            self._playback_failed = True
            self.status_label.setText("播放失败：无法重新打开该视频地址")

    def _update_title_loop(self) -> None:
        @self.player.event_callback('end-file')
        def on_end_file(event):
            # python-mpv 1.0.8：end-file 的字段在 event.data（MpvEventEndFile）里，
            # 原实现 event.data.get('reason') 会抛 AttributeError 被事件循环吞掉，
            # 导致任何播放错误既不入日志也不提示用户。
            data = getattr(event, "data", None)
            reason_raw = getattr(data, "reason", None) if data is not None else None
            error_raw = getattr(data, "error", 0) if data is not None else 0
            reason = self._end_file_reason_name(reason_raw)
            try:
                error_code = int(error_raw or 0)
            except (TypeError, ValueError):
                error_code = 0
            logger.info("mpv end-file: reason=%s(%r) error=%s", reason, reason_raw, error_code)
            # 回调在 mpv 线程，交给 GUI 线程处理
            self._end_file_signal.emit(reason, error_code)

    def _on_volume(self, value: int) -> None:
        if hasattr(self, 'player'):
            self.player.volume = value

    def _on_speed_changed(self, text: str) -> None:
        try:
            speed_val = float(text.replace("x", ""))
            if hasattr(self, 'player'):
                self.player.speed = speed_val
        except ValueError:
            pass

    def closeEvent(self, event) -> None:  # noqa: N802, ANN001
        self.is_alive = False
        self.timer.stop()
        try:
            self.player.terminate()
        except Exception:
            pass
        super().closeEvent(event)