#!/usr/bin/env python3
"""播放器冒烟（mpv 版）：生成 2 秒测试视频，用 PlayerWindow 打开并确认时长/首帧。

同时校验请求头透传（mpv http-header-fields）是否真正生效。

用法：
    python scripts/smoke_player.py [--local <本地视频路径>]
"""

from __future__ import annotations

import argparse
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
os.environ["APPDATA"] = tempfile.mkdtemp(prefix="duanju-smoke-player-")

from PySide6.QtWidgets import QApplication  # noqa: E402

from duanju.config import Config  # noqa: E402
from ui.player_window import MPV_AVAILABLE, PlayerWindow  # noqa: E402

TEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
    ),
    "Referer": "https://www.hongguoapp.cn/",
}


def build_test_video(ffmpeg: str) -> Path:
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
        raise RuntimeError(f"生成测试视频失败：{result.stderr[-500:]}")
    return video


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--local", help="直接播放该本地文件，跳过生成测试视频")
    args = parser.parse_args()

    if not MPV_AVAILABLE:
        print("libmpv 不可用（缺少 mpv-2.dll），无法测试播放器")
        return 1

    config = Config.load()
    if args.local:
        video = Path(args.local)
        if not video.is_file():
            print(f"文件不存在：{video}")
            return 1
    else:
        ffmpeg = config.resolved_ffmpeg()
        if not ffmpeg:
            print("未找到 ffmpeg")
            return 1
        video = build_test_video(ffmpeg)

    app = QApplication.instance() or QApplication([sys.argv[0]])
    window = PlayerWindow(
        [("测试视频", str(video))],
        volume=50,
        headers=TEST_HEADERS,
    )
    window.show()

    # 1) 请求头是否真的写进了 mpv
    applied = list(getattr(window.player, "http_header_fields", None) or [])
    headers_ok = any(h.lower().startswith("referer:") for h in applied) and any(
        h.lower().startswith("user-agent:") for h in applied
    )

    state = {"duration": 0.0, "pos": 0.0, "reason": "", "error": ""}
    end_file_reasons: list[str] = []

    def on_end_file(reason: str, _code: int) -> None:
        end_file_reasons.append(reason)

    window._end_file_signal.connect(on_end_file)

    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        app.processEvents()
        try:
            duration = window.player.duration or 0.0
            pos = window.player.time_pos or 0.0
        except Exception:  # noqa: BLE001
            duration, pos = 0.0, 0.0
        state["duration"] = max(state["duration"], float(duration))
        state["pos"] = max(state["pos"], float(pos))
        if state["pos"] > 0:
            break
        if end_file_reasons:
            break
        time.sleep(0.05)

    try:
        window.player.stop()
    except Exception:  # noqa: BLE001
        pass
    window.close()
    app.processEvents()

    print(f"请求头透传：{'PASS' if headers_ok else 'FAIL'} -> {applied}")
    print(f"时长识别：{state['duration']:.2f}s，播放推进：{state['pos']:.2f}s")
    if end_file_reasons:
        print(f"提前收到 end-file：{end_file_reasons}")

    if not headers_ok:
        return 1
    if state["duration"] <= 0 and state["pos"] <= 0:
        print("播放器没有识别到媒体时长（可能是解码器缺失或首帧未出）")
        return 1
    print("播放器就绪：mpv 已出帧，请求头生效 [OK]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
