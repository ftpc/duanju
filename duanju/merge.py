"""整部合并：ffmpeg concat demuxer（优先无损 copy，失败回退转码）。"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from pathlib import Path

from .download import FFmpeg
from .errors import CancelledError, DownloadError, StorageError

logger = logging.getLogger(__name__)


def _concat_line(path: Path) -> str:
    text = str(path.resolve()).replace("\\", "/").replace("'", "'\\''")
    return f"file '{text}'"


def merge_episodes(
    ffmpeg: FFmpeg,
    files: list[str | Path],
    target: str | Path,
    on_progress: Callable[[float | None, int, float], None],
    cancel_event: threading.Event,
) -> Path:
    """把多集 mp4 顺序拼接为一个文件，返回最终路径。"""
    if not ffmpeg.available:
        raise DownloadError("未找到 ffmpeg，无法合并")
    sources = [Path(item) for item in files]
    if not sources:
        raise DownloadError("没有可合并的分集")
    missing = [str(item) for item in sources if not item.is_file() or item.stat().st_size <= 0]
    if missing:
        raise DownloadError(f"以下文件缺失或为空：{missing[0]}")

    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    part = target.with_name(target.stem + ".part" + target.suffix)
    list_file = target.with_name(target.stem + ".concat.txt")

    durations: list[float] = []
    for source in sources:
        info = ffmpeg.probe(source)
        durations.append(float(info.get("duration") or 0.0))
    total_duration = sum(durations)

    try:
        list_file.write_text(
            "ffconcat version 1.0\n" + "\n".join(_concat_line(item) for item in sources) + "\n",
            encoding="utf-8",
        )
    except OSError as exc:
        raise StorageError(f"无法创建合并清单：{exc}") from exc

    base_args = [
        ffmpeg.binary,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(list_file),
    ]
    tail = [
        "-movflags",
        "+faststart",
        "-progress",
        "pipe:1",
        "-nostats",
        str(part),
    ]
    copy_args = base_args + ["-c", "copy"] + tail
    encode_args = base_args + [
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "20",
        "-c:a",
        "aac",
        "-b:a",
        "160k",
    ] + tail

    try:
        try:
            ffmpeg.run_with_progress(copy_args, total_duration, on_progress, cancel_event, label="合并")
        except DownloadError as exc:
            if cancel_event.is_set():
                raise CancelledError("合并已取消") from exc
            logger.warning("无损合并失败，回退转码合并：%s", exc)
            if part.exists():
                part.unlink(missing_ok=True)
            ffmpeg.run_with_progress(
                encode_args, total_duration, on_progress, cancel_event, label="合并(转码)"
            )
        if not part.exists() or part.stat().st_size <= 1024:
            raise DownloadError("合并失败：没有产出有效文件")
        info = ffmpeg.probe(part)
        if info and info.get("duration", 0) <= 0 and not info.get("video"):
            raise DownloadError("合并结果校验失败：无法识别音视频流")
        import os

        os.replace(part, target)
        return target
    except CancelledError:
        part.unlink(missing_ok=True)
        raise
    except DownloadError:
        part.unlink(missing_ok=True)
        raise
    finally:
        list_file.unlink(missing_ok=True)
