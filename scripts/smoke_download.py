#!/usr/bin/env python3
"""下载 / 合并冒烟：本机生成测试 HLS 与测试 mp4，不访问任何真实站源。

验证：
  1. ffmpeg 通过 HTTP 拉取本地 HLS（含进度回调）
  2. 取消下载能中断并清理临时文件
  3. 两段 mp4 无损合并、时长校验

用法：
    python scripts/smoke_download.py
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
os.environ["APPDATA"] = tempfile.mkdtemp(prefix="duanju-smoke-dl-")

from duanju.config import Config  # noqa: E402
from duanju.download import FFmpeg  # noqa: E402
from duanju.errors import CancelledError  # noqa: E402
from duanju.merge import merge_episodes  # noqa: E402
from duanju.models import MediaPlan  # noqa: E402
from duanju.net import HttpClient  # noqa: E402
from duanju.util import human_size  # noqa: E402


def _ffmpeg(args: list[str]) -> None:
    result = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg 失败：{result.stderr[-800:]}")


def _serve(directory: Path) -> tuple[ThreadingHTTPServer, threading.Thread]:
    handler = partial(SimpleHTTPRequestHandler, directory=str(directory))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def main() -> int:
    config = Config.load()
    ffmpeg = FFmpeg(config)
    if not ffmpeg.available:
        print("未找到 ffmpeg，无法执行下载冒烟")
        return 1
    print("ffmpeg：", ffmpeg.version())
    client = HttpClient(config)
    work = Path(tempfile.mkdtemp(prefix="duanju-smoke-media-"))
    try:
        print("\n[准备] 生成测试 HLS 与两段测试 mp4…")
        _ffmpeg(
            [
                ffmpeg.binary, "-hide_banner", "-loglevel", "error", "-y",
                "-f", "lavfi", "-i", "testsrc=size=320x240:rate=25",
                "-t", "6", "-c:v", "libx264", "-preset", "ultrafast", "-g", "25",
                "-f", "hls", "-hls_time", "2", "-hls_list_size", "0",
                "-hls_segment_filename", str(work / "seg%d.ts"),
                str(work / "index.m3u8"),
            ]
        )
        for name in ("a.mp4", "b.mp4"):
            _ffmpeg(
                [
                    ffmpeg.binary, "-hide_banner", "-loglevel", "error", "-y",
                    "-f", "lavfi", "-i", "testsrc=size=320x240:rate=25",
                    "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100",
                    "-t", "2", "-c:v", "libx264", "-preset", "ultrafast",
                    "-c:a", "aac", "-shortest", str(work / name),
                ]
            )

        server, thread = _serve(work)
        base = f"http://127.0.0.1:{server.server_address[1]}/"
        print("本地测试服务：", base)

        print("\n[1/3] 下载本地 HLS…")
        plan = MediaPlan(url=base + "index.m3u8", referer=base, kind="hls")
        progress: list[tuple] = []
        target = work / "hls.mp4"
        ffmpeg.download(
            plan,
            target,
            lambda ratio, downloaded, speed: progress.append((ratio, downloaded, speed)),
            threading.Event(),
            client,
        )
        info = ffmpeg.probe(target)
        print(f"  文件 {human_size(target.stat().st_size)}，时长 {info.get('duration'):.2f}s，进度回调 {len(progress)} 次")
        assert target.stat().st_size > 10_000
        assert (info.get("duration") or 0) > 4

        print("\n[2/3] 取消下载…")
        cancel = threading.Event()
        cancelled_target = work / "cancelled.mp4"
        started = time.monotonic()

        def cancel_later() -> None:
            time.sleep(0.6)
            cancel.set()

        threading.Thread(target=cancel_later, daemon=True).start()
        try:
            ffmpeg.download(plan, cancelled_target, lambda *_: None, cancel, client)
            print("  [FAIL] 预期被取消，但下载成功")
            return 1
        except CancelledError:
            part = cancelled_target.with_name(cancelled_target.stem + ".part" + cancelled_target.suffix)
            print(f"  [OK] 已取消（{time.monotonic() - started:.1f}s），临时文件清理：{not part.exists()}")
            assert not part.exists()

        print("\n[3/3] 合并两段测试视频…")
        merged = work / "merged.mp4"
        merge_episodes(ffmpeg, [work / "a.mp4", work / "b.mp4"], merged, lambda *_: None, threading.Event())
        merged_info = ffmpeg.probe(merged)
        print(f"  合并结果 {human_size(merged.stat().st_size)}，时长 {merged_info.get('duration'):.2f}s")
        assert 3.5 <= (merged_info.get("duration") or 0) <= 4.6

        server.shutdown()
        thread.join(timeout=2)
        print("\n下载 / 合并冒烟通过 [OK]")
        return 0
    finally:
        client.close()
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
