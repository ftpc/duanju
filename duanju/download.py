"""ffmpeg / ffprobe 封装：拉流、进度、取消、完整性校验。"""

from __future__ import annotations

import json
import logging
import os
import queue
import re
import subprocess
import threading
import time
import yt_dlp
from collections import deque
from collections.abc import Callable
from pathlib import Path
from urllib.parse import urljoin

from .config import Config
from .errors import CancelledError, DownloadError
from .models import MediaPlan
from .net import HttpClient

logger = logging.getLogger(__name__)

_EXTINF_RE = re.compile(r"#EXTINF:\s*([\d.]+)", re.I)
_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _popen_kwargs() -> dict:
    return {"creationflags": _CREATE_NO_WINDOW} if os.name == "nt" else {}


class FFmpeg:
    def __init__(self, config: Config):
        self._config = config
        self.binary = config.resolved_ffmpeg()
        self.probe_binary = config.resolved_ffprobe()

    # ---------- 环境 ----------
    def refresh(self) -> None:
        self.binary = self._config.resolved_ffmpeg()
        self.probe_binary = self._config.resolved_ffprobe()

    @property
    def available(self) -> bool:
        return bool(self.binary and Path(self.binary).is_file())

    def version(self) -> str:
        if not self.available:
            return ""
        try:
            result = subprocess.run(
                [self.binary, "-version"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=15,
                **_popen_kwargs(),
            )
            return (result.stdout or "").splitlines()[0] if result.stdout else ""
        except (OSError, subprocess.SubprocessError):
            return ""

    # ---------- 探测 ----------
    def probe(self, path: str | Path) -> dict:
        if not self.probe_binary:
            return {}
        try:
            result = subprocess.run(
                [
                    self.probe_binary,
                    "-v",
                    "error",
                    "-print_format",
                    "json",
                    "-show_format",
                    "-show_streams",
                    str(path),
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=30,
                **_popen_kwargs(),
            )
        except (OSError, subprocess.SubprocessError) as exc:
            logger.warning("ffprobe 执行失败：%s", exc)
            return {}
        if result.returncode != 0:
            return {}
        try:
            data = json.loads(result.stdout or "{}")
        except json.JSONDecodeError:
            return {}
        streams = data.get("streams") or []
        video = next((s.get("codec_name") for s in streams if s.get("codec_type") == "video"), "")
        audio = next((s.get("codec_name") for s in streams if s.get("codec_type") == "audio"), "")
        try:
            duration = float((data.get("format") or {}).get("duration") or 0.0)
        except (TypeError, ValueError):
            duration = 0.0
        if duration <= 0:
            for stream in streams:
                try:
                    duration = max(duration, float(stream.get("duration") or 0.0))
                except (TypeError, ValueError):
                    continue
        return {"duration": duration, "video": video or "", "audio": audio or "", "raw": data}

    # ---------- HLS 时长（用于进度百分比） ----------
    def hls_duration(self, plan: MediaPlan, client: HttpClient) -> float:
        if plan.kind != "hls":
            return 0.0
        text = client.get_text(
            plan.url,
            referer=plan.referer or None,
            headers=plan.headers,
            cookie=self._config.cookie or None,
            timeout=20,
        )
        if "#EXT-X-STREAM-INF" in text:
            variant = self._first_variant(text, plan.url)
            if variant:
                text = client.get_text(
                    variant,
                    referer=plan.referer or None,
                    headers=plan.headers,
                    cookie=self._config.cookie or None,
                    timeout=20,
                )
        total = 0.0
        for value in _EXTINF_RE.findall(text):
            try:
                total += float(value)
            except ValueError:
                continue
        return total

    @staticmethod
    def _first_variant(playlist: str, base_url: str) -> str:
        lines = [line.strip() for line in playlist.splitlines()]
        for index, line in enumerate(lines):
            if line.startswith("#EXT-X-STREAM-INF"):
                for candidate in lines[index + 1 :]:
                    if candidate and not candidate.startswith("#"):
                        return urljoin(base_url, candidate)
        return ""

    # ---------- 运行 ----------
    @staticmethod
    def _terminate(proc: subprocess.Popen) -> None:
        try:
            proc.terminate()
        except OSError:
            return
        try:
            proc.wait(timeout=6)
        except subprocess.TimeoutExpired:
            try:
                proc.kill()
            except OSError:
                pass
            try:
                proc.wait(timeout=6)
            except subprocess.TimeoutExpired:
                pass

    def run_with_progress(
        self,
        args: list[str],
        duration: float,
        on_progress: Callable[[float | None, int, float], None],
        cancel_event: threading.Event | None,
        *,
        label: str = "处理",
    ) -> None:
        if not self.available:
            raise DownloadError("未找到 ffmpeg，请在「设置」里指定 ffmpeg.exe 路径")
        try:
            proc = subprocess.Popen(
                args,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                **_popen_kwargs(),
            )
        except OSError as exc:
            raise DownloadError(f"无法启动 ffmpeg：{exc}") from exc

        lines: queue.Queue[str | None] = queue.Queue()

        def pump() -> None:
            assert proc.stdout is not None
            try:
                for line in proc.stdout:
                    lines.put(line)
            finally:
                lines.put(None)

        threading.Thread(target=pump, daemon=True, name=f"ffmpeg-{label}").start()

        downloaded = 0
        ratio: float | None = None
        speed = 0.0
        last_emit = 0.0
        last_bytes = 0
        last_speed_at = time.monotonic()
        errors: deque[str] = deque(maxlen=8)
        cancelled = False

        def emit(force: bool = False) -> None:
            nonlocal last_emit
            now = time.monotonic()
            if not force and now - last_emit < 0.25:
                return
            last_emit = now
            try:
                on_progress(ratio, downloaded, speed)
            except Exception:  # noqa: BLE001 - 回调异常不能影响下载
                logger.exception("进度回调失败")

        def handle(raw: str) -> None:
            nonlocal downloaded, ratio, speed, last_bytes, last_speed_at
            if "=" in raw and not raw.startswith(" "):
                key, _, value = raw.strip().partition("=")
                if key == "out_time_us":
                    try:
                        seconds = int(value) / 1_000_000
                    except ValueError:
                        seconds = 0.0
                    ratio = max(0.0, min(0.999, seconds / duration)) if duration > 0 else None
                    now = time.monotonic()
                    if now - last_speed_at >= 0.5:
                        delta = downloaded - last_bytes
                        speed = max(0.0, delta / max(1e-6, now - last_speed_at))
                        last_bytes, last_speed_at = downloaded, now
                    emit()
                elif key == "total_size":
                    try:
                        downloaded = max(downloaded, int(value))
                    except ValueError:
                        pass
                    emit()
                elif key == "progress" and value.strip() == "end":
                    ratio = 1.0 if duration > 0 else ratio
                    emit(force=True)
            else:
                text = raw.strip()
                if text and not text.startswith("#"):
                    errors.append(text)

        while True:
            if cancel_event is not None and cancel_event.is_set():
                cancelled = True
                self._terminate(proc)
                break
            try:
                raw = lines.get(timeout=0.2)
            except queue.Empty:
                if proc.poll() is not None:
                    try:
                        raw = lines.get(timeout=1.0)
                    except queue.Empty:
                        break
                    if raw is None:
                        break
                    handle(raw)
                    continue
                continue
            if raw is None:
                break
            handle(raw)

        try:
            returncode = proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            self._terminate(proc)
            returncode = proc.returncode if proc.returncode is not None else -1

        if cancelled:
            raise CancelledError("任务已暂停/取消")
        if returncode != 0:
            message = " | ".join(errors) if errors else f"ffmpeg 退出码 {returncode}"
            raise DownloadError(f"{label}失败：{message}")
        try:
            on_progress(1.0 if duration > 0 else ratio, downloaded, 0.0)
        except Exception:  # noqa: BLE001
            pass

    # ---------- 下载一集 ----------
    def download(
        self,
        plan: MediaPlan,
        target: str | Path,
        on_progress: Callable[[float | None, int, float], None],
        cancel_event: threading.Event,
        client: HttpClient,
    ) -> None:
        target = Path(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        part = target.with_name(target.stem + ".part" + target.suffix)
        if part.exists():
            try:
                part.unlink()
            except OSError:
                pass

        duration = float(plan.duration or 0.0)
        if duration <= 0 and plan.kind == "hls":
            try:
                duration = self.hls_duration(plan, client)
            except Exception as exc:  # noqa: BLE001 - 时长只影响进度显示
                logger.debug("获取播放列表时长失败：%s", exc)
                duration = 0.0

        # =========================================================
        # 🚀 新增：优先使用 yt-dlp 进行多线程分片下载
        # =========================================================
        try:
            # 组装 yt-dlp 需要的请求头
            headers = plan.headers.copy()
            if plan.referer:
                headers["Referer"] = plan.referer
                
            def ytdlp_progress_hook(d):
                if cancel_event.is_set():
                    raise Exception("用户取消下载")
                if d["status"] == "downloading":
                    downloaded = d.get("downloaded_bytes", 0)
                    total = d.get("total_bytes") or d.get("total_bytes_estimate", 0)
                    ratio = (downloaded / total) if total > 0 else None
                    speed = d.get("speed") or 0.0
                    on_progress(ratio, downloaded, speed)
                elif d["status"] == "finished":
                    on_progress(1.0, d.get("total_bytes", 0), 0.0)

            ydl_opts = {
                "format": "bestvideo+bestaudio/best",
                "outtmpl": str(part),
                "concurrent_fragment_downloads": 8, # 👈 开启 8 线程分片并发
                "quiet": True,
                "no_warnings": True,
                "http_headers": headers,
                "progress_hooks": [ytdlp_progress_hook],
            }

            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([plan.url])

            if cancel_event.is_set():
                raise CancelledError("任务已暂停/取消")

            if not part.exists() or part.stat().st_size <= 1024:
                raise DownloadError("yt-dlp 没有产出有效文件")

            # 校验并重命名
            if self.probe_binary:
                info = self.probe(part)
                if info and info.get("duration", 0) <= 0 and not info.get("video"):
                    raise DownloadError("yt-dlp 文件校验失败：无法识别音视频流")
            
            os.replace(part, target)
            logger.info("yt-dlp 下载成功: %s", target)
            return  # 成功则直接返回，不走下面的 ffmpeg 逻辑

        except CancelledError:
            self._safe_unlink(part)
            raise
        except Exception as exc:
            logger.warning("yt-dlp 下载失败或不受支持，自动回退到 ffmpeg 顺序下载。原因: %s", exc)
            # 清理可能残留的半截文件
            self._safe_unlink(part)
        # =========================================================
        # 如果 yt-dlp 失败，继续往下走原本的 ffmpeg 逻辑
        # =========================================================

        header_text = "".join(
            f"{key}: {value}\r\n"
            for key, value in plan.request_headers().items()
            if key.lower() != "user-agent"
        )
        user_agent = plan.headers.get("User-Agent") or (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        args = [
            self.binary,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-user_agent",
            user_agent,
            "-headers",
            header_text,
            "-i",
            plan.url,
            "-c",
            "copy",
        ]
        if plan.kind == "hls":
            args += ["-bsf:a", "aac_adtstoasc"]
        args += ["-movflags", "+faststart", "-progress", "pipe:1", "-nostats", str(part)]

        try:
            self.run_with_progress(args, duration, on_progress, cancel_event, label="下载")
            if not part.exists() or part.stat().st_size <= 1024:
                raise DownloadError("ffmpeg 没有产出有效文件（可能地址已失效）")
            if self.probe_binary:
                info = self.probe(part)
                if info and info.get("duration", 0) <= 0 and not info.get("video"):
                    raise DownloadError("文件校验失败：无法识别音视频流")
            os.replace(part, target)
        except CancelledError:
            self._safe_unlink(part)
            raise
        except DownloadError:
            self._safe_unlink(part)
            raise
        except OSError as exc:
            self._safe_unlink(part)
            raise DownloadError(f"保存文件失败：{exc}") from exc

    @staticmethod
    def _safe_unlink(path: Path) -> None:
        try:
            if path.exists():
                path.unlink()
        except OSError:
            pass