"""下载任务队列：状态机 + 并发调度 + 暂停/继续/删除 + 进度与持久化。"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable, Iterable
from pathlib import Path

from .config import Config
from .db import Database
from .download import FFmpeg
from .errors import AppError, CancelledError, ParseError
from .models import Drama, Job
from .net import HttpClient
from .sources import SourceRegistry
from .util import ensure_dir, episode_filename, sanitize_filename

logger = logging.getLogger(__name__)


class JobManager:
    def __init__(
        self,
        config: Config,
        db: Database,
        client: HttpClient,
        ffmpeg: FFmpeg,
        registry: SourceRegistry,
        *,
        job_callback: Callable[[dict], None],
        jobs_callback: Callable[[list[dict]], None],
        log_callback: Callable[[str], None] | None = None,
    ):
        self.config = config
        self.db = db
        self.client = client
        self.ffmpeg = ffmpeg
        self.registry = registry
        self._job_callback = job_callback
        self._jobs_callback = jobs_callback
        self._log_callback = log_callback
        self._lock = threading.RLock()
        self._active: dict[str, threading.Event] = {}
        self._removing: set[str] = set()
        self._progress_at: dict[str, float] = {}
        self._stop = threading.Event()
        self._scheduler: threading.Thread | None = None

    # ---------- 生命周期 ----------
    def start(self) -> None:
        paused = self.db.reset_interrupted()
        if paused:
            self._log(f"发现 {paused} 个上次未完成的任务，已置为暂停")
        self._stop.clear()
        self._scheduler = threading.Thread(target=self._loop, name="job-scheduler", daemon=True)
        self._scheduler.start()
        self._emit_jobs()

    def stop(self) -> None:
        self._stop.set()
        with self._lock:
            for event in self._active.values():
                event.set()
        if self._scheduler is not None:
            self._scheduler.join(timeout=2)
        deadline = time.monotonic() + 6
        while time.monotonic() < deadline:
            with self._lock:
                if not self._active:
                    break
            time.sleep(0.1)

    # ---------- 查询 ----------
    def snapshot(self) -> list[dict]:
        return [job.to_dict() for job in self.db.jobs()]

    def active_count(self) -> int:
        with self._lock:
            return len(self._active)

    # ---------- 入队与控制 ----------
    def enqueue(self, drama: Drama, episodes: Iterable[int]) -> int:
        added = self.db.add_jobs(drama, episodes)
        if added:
            self._log(f"已加入 {added} 个下载任务：{drama.title}")
            self._emit_jobs()
            self._dispatch()
        return added

    def pause(self, job_id: str) -> None:
        job = self.db.job(job_id)
        if job is None:
            return
        if job.state == "queued":
            self.db.update_job(job_id, state="paused", speed=0, error="")
            self._log(f"第{job.episode}集 已暂停")
            self._emit_job(job_id)
        elif job.state == "downloading":
            with self._lock:
                event = self._active.get(job_id)
            if event is not None:
                event.set()

    def resume(self, job_id: str) -> None:
        job = self.db.job(job_id)
        if job is None or job.state not in ("paused", "failed"):
            return
        self.db.update_job(job_id, state="queued", error="", speed=0)
        self._log(f"第{job.episode}集 继续下载")
        self._emit_job(job_id)
        self._dispatch()

    def remove(self, job_id: str) -> None:
        with self._lock:
            self._removing.add(job_id)
            event = self._active.get(job_id)
        if event is not None:
            event.set()
            return
        self._delete_job(job_id)

    def pause_all(self) -> None:
        with self._lock:
            events = list(self._active.values())
        for event in events:
            event.set()
        for job in self.db.jobs():
            if job.state == "queued":
                self.db.update_job(job.id, state="paused", speed=0, error="")
        self._emit_jobs()
        self._log("已暂停全部任务")

    def resume_all(self) -> None:
        for job in self.db.jobs():
            if job.state in ("paused", "failed"):
                self.db.update_job(job.id, state="queued", error="", speed=0)
        self._emit_jobs()
        self._dispatch()
        self._log("已继续全部任务")

    def clear_failed(self) -> int:
        failed = [job.id for job in self.db.jobs() if job.state == "failed"]
        for job_id in failed:
            self._delete_job(job_id, emit=False)
        self._emit_jobs()
        if failed:
            self._log(f"已清理 {len(failed)} 个失败任务")
        return len(failed)

    # ---------- 调度 ----------
    def _loop(self) -> None:
        while not self._stop.wait(0.3):
            try:
                self._dispatch()
            except Exception:  # noqa: BLE001
                logger.exception("调度循环异常")

    def _dispatch(self) -> None:
        with self._lock:
            slots = max(0, int(self.config.concurrency) - len(self._active))
            if slots <= 0:
                return
            busy_ids = set(self._active)
            for _ in range(slots):
                job = self.db.next_queued(exclude=busy_ids)
                if job is None:
                    break
                self._begin(job)
                busy_ids.add(job.id)

    def _begin(self, job: Job) -> None:
        event = threading.Event()
        with self._lock:
            self._active[job.id] = event
        self.db.update_job(job.id, state="downloading", bytes=0, total=0, speed=0, error="")
        self._emit_job(job.id)
        threading.Thread(
            target=self._run,
            args=(job.id, event),
            name=f"job-{job.id}",
            daemon=True,
        ).start()

    def _run(self, job_id: str, cancel: threading.Event) -> None:
        job = self.db.job(job_id)
        if job is None:
            with self._lock:
                self._active.pop(job_id, None)
            return
        drama = self._drama_for(job)
        attempts = max(1, int(self.config.retries) + 1)
        last_error = ""
        for attempt in range(1, attempts + 1):
            if cancel.is_set():
                break
            try:
                plan = self._resolve(drama, job.episode)
                if cancel.is_set():
                    raise CancelledError("任务已暂停")
                out_dir = ensure_dir(self.config.download_path() / sanitize_filename(drama.title))
                target = out_dir / episode_filename(job.episode)
                self.db.update_job(job_id, file=str(target))
                self.ffmpeg.download(
                    plan,
                    target,
                    lambda ratio, downloaded, speed: self._progress(job_id, ratio, downloaded, speed),
                    cancel,
                    self.client,
                )
                size = target.stat().st_size if target.exists() else 0
                self.db.update_job(
                    job_id,
                    state="completed",
                    bytes=size,
                    total=size,
                    speed=0,
                    error="",
                    file=str(target),
                    revision=job.revision + 1,
                )
                self._log(f"第{job.episode}集 下载完成")
                self._emit_job(job_id)
                self._finish(job_id, cancel, last_error=None, removing=False, succeeded=True)
                return
            except CancelledError:
                last_error = "已暂停"
                break
            except AppError as exc:
                last_error = str(exc)
                can_retry = exc.retryable and attempt < attempts and not cancel.is_set()
                self._log(f"第{job.episode}集 第{attempt}次失败：{last_error}")
                if can_retry:
                    time.sleep(1.2 * attempt)
                    continue
                break
            except Exception as exc:  # noqa: BLE001 - 兜底
                logger.exception("下载任务异常")
                last_error = f"未知错误：{exc}"
                break
        with self._lock:
            removing = job_id in self._removing
            self._removing.discard(job_id)
        self._finish(job_id, cancel, last_error=last_error, removing=removing)

    def _finish(
        self,
        job_id: str,
        cancel: threading.Event,
        *,
        last_error: str | None,
        removing: bool,
        succeeded: bool = False,
    ) -> None:
        with self._lock:
            self._active.pop(job_id, None)
            self._progress_at.pop(job_id, None)
        if succeeded:
            self._emit_jobs()
            self._dispatch()
            return
        if removing:
            self._delete_job(job_id)
            self._emit_jobs()
            self._dispatch()
            return
        if cancel.is_set() or last_error == "已暂停":
            self.db.update_job(job_id, state="paused", speed=0, error="已暂停")
            self._log("任务已暂停")
        else:
            self.db.update_job(job_id, state="failed", speed=0, error=last_error or "下载失败")
        self._emit_job(job_id)
        self._dispatch()

    # ---------- 工具 ----------
    def _resolve(self, drama: Drama, episode: int):
        source = self.registry.get(drama.source_key)
        if source is None:
            raise ParseError(f"站源「{drama.source_key}」未启用，请到设置里检查")
        return source.resolve(self.client, drama, episode)

    def _drama_for(self, job: Job) -> Drama:
        row = self.db.get_drama(job.drama_key)
        if row:
            return Drama(
                source_key=str(row["source"]),
                source_name="",
                drama_id=str(row["source_id"]),
                title=str(row["title"]),
                total=int(row["total"] or 0),
                cover=str(row["cover"] or ""),
                sid=int(row["sid"] or 1),
            )
        source_key, _, drama_id = job.drama_key.rpartition(":")
        return Drama(
            source_key=source_key,
            source_name="",
            drama_id=drama_id,
            title=job.drama_title,
        )

    def _progress(self, job_id: str, ratio: float | None, downloaded: int, speed: float) -> None:
        now = time.monotonic()
        with self._lock:
            last = self._progress_at.get(job_id, 0.0)
            if now - last < 0.35:
                return
            self._progress_at[job_id] = now
        total = int(downloaded / ratio) if ratio and ratio > 0 else 0
        self.db.update_job(job_id, bytes=int(downloaded), total=total, speed=float(speed))
        self._emit_job(job_id)

    def _delete_job(self, job_id: str, *, emit: bool = True) -> None:
        job = self.db.job(job_id)
        if job is not None and job.file:
            target = Path(job.file)
            for candidate in (target, target.with_name(target.stem + ".part" + target.suffix)):
                try:
                    candidate.unlink(missing_ok=True)
                except OSError:
                    pass
        self.db.delete_job(job_id)
        if emit:
            self._emit_jobs()
            self._dispatch()

    def _emit_job(self, job_id: str) -> None:
        job = self.db.job(job_id)
        if job is not None:
            try:
                self._job_callback(job.to_dict())
            except Exception:  # noqa: BLE001
                logger.exception("任务回调失败")

    def _emit_jobs(self) -> None:
        try:
            self._jobs_callback(self.snapshot())
        except Exception:  # noqa: BLE001
            logger.exception("任务列表回调失败")

    def _log(self, message: str) -> None:
        if self._log_callback is not None:
            try:
                self._log_callback(message)
                return
            except Exception:  # noqa: BLE001
                logger.exception("日志回调失败")
        logger.info(message)
