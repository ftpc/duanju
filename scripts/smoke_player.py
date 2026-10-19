#!/usr/bin/env python3
"""播放器冒烟：生成 2 秒测试视频，用 QtMultimedia 打开并确认时长/状态。

用法：
    python scripts/smoke_player.py
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["APPDATA"] = tempfile.mkdtemp(prefix="duanju-smoke-player-")

from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from duanju.config import Config  # noqa: E402
from ui.player_window import MULTIMEDIA_AVAILABLE, PlayerWindow  # noqa: E402


def main() -> int:
    if not MULTIMEDIA_AVAILABLE:
        print("QtMultimedia 不可用，无法测试播放器")
        return 1
    config = Config.load()
    ffmpeg = config.resolved_ffmpeg()
    if not ffmpeg:
        print("未找到 ffmpeg")
        return 1
    work = Path(tempfile.mkdtemp(prefix="duanju-player-media-"))
    video = work / "test.mp4"
    result = subprocess.run(
        [
            ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "testsrc=size=320x240:rate=25",
            "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100",
            "-t", "2", "-c:v", "libx264", "-preset", "ultrafast",
            "-c:a", "aac", "-shortest", str(video),
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print("生成测试视频失败：", result.stderr[-500:])
        return 1

    app = QApplication.instance() or QApplication([sys.argv[0]])
    window = PlayerWindow([("测试视频", str(video))], volume=50)
    window.show()

    state = {"duration": 0.0, "error": ""}

    def on_duration(value: int) -> None:
        state["duration"] = value / 1000

    def on_error(_error, message: str) -> None:
        state["error"] = message

    window.player.durationChanged.connect(on_duration)
    window.player.errorOccurred.connect(on_error)

    deadline = time.monotonic() + 12
    while time.monotonic() < deadline:
        app.processEvents()
        if state["duration"] > 0 or state["error"]:
            break
        time.sleep(0.05)

    window.player.stop()
    window.close()
    if state["error"]:
        print(f"播放器报错：{state['error']}")
        return 1
    if state["duration"] <= 0:
        print("播放器没有识别到媒体时长（可能是解码器缺失）")
        return 1
    print(f"播放器就绪：时长 {state['duration']:.2f}s，后端 Windows Media Foundation [OK]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
