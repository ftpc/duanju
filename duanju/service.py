"""服务层：UI 唯一入口；不依赖 PySide6，可被 CLI / 测试复用。"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from pathlib import Path

from .config import Config, SourceConfig
from .db import Database
from .download import FFmpeg
from .errors import AppError, ParseError
from .jobs import JobManager
from .merge import merge_episodes
from .models import Drama, Episode
from .net import HttpClient
from .sources import SourceRegistry, probe_site
from .util import ensure_dir, sanitize_filename

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[float | None, int, float], None]


class Service:
    def __init__(self, config: Config):
        self.config = config
        config.ensure_dirs()
        self.client = HttpClient(config)
        self.db = Database(config.data_dir / "app.db")
        self.ffmpeg = FFmpeg(config)
        self.registry = SourceRegistry(config)
        self._job_callbacks: list[Callable[[dict], None]] = []
        self._jobs_callbacks: list[Callable[[list[dict]], None]] = []
        self._log_callbacks: list[Callable[[str], None]] = []
        self.jobs = JobManager(
            config,
            self.db,
            self.client,
            self.ffmpeg,
            self.registry,
            job_callback=self._emit_job,
            jobs_callback=self._emit_jobs,
            log_callback=self._emit_log,
        )
        self._started = False

    # ---------- 生命周期 ----------
    def start(self) -> None:
        if self._started:
            return
        self._started = True
        self.jobs.start()
        self._emit_log("服务已启动")

    def shutdown(self) -> None:
        if not self._started:
            return
        self._started = False
        self.jobs.stop()
        try:
            self.client.close()
        finally:
            self.db.close()

    # ---------- 事件 ----------
    def on_job(self, callback: Callable[[dict], None]) -> None:
        self._job_callbacks.append(callback)

    def on_jobs(self, callback: Callable[[list[dict]], None]) -> None:
        self._jobs_callbacks.append(callback)

    def on_log(self, callback: Callable[[str], None]) -> None:
        self._log_callbacks.append(callback)

    def _emit_job(self, job: dict) -> None:
        for callback in list(self._job_callbacks):
            try:
                callback(job)
            except Exception:  # noqa: BLE001
                logger.exception("job 回调异常")

    def _emit_jobs(self, jobs: list[dict]) -> None:
        for callback in list(self._jobs_callbacks):
            try:
                callback(jobs)
            except Exception:  # noqa: BLE001
                logger.exception("jobs 回调异常")

    def _emit_log(self, message: str) -> None:
        logger.info(message)
        for callback in list(self._log_callbacks):
            try:
                callback(message)
            except Exception:  # noqa: BLE001
                logger.exception("log 回调异常")

    # ---------- 站源 / 搜索 ----------
    def sources(self) -> list[tuple[str, str]]:
        return self.registry.names()

    def search(self, source_key: str, query: str) -> list[Drama]:
        source = self.registry.get(source_key)
        if source is None:
            raise ParseError("站源不可用，请到设置里检查")
        return source.search(self.client, query)

    def detail(self, drama: Drama) -> list[Episode]:
        source = self.registry.get(drama.source_key)
        if source is None:
            raise ParseError("站源不可用，请到设置里检查")
        episodes = source.detail(self.client, drama)
        self.db.upsert_drama(drama)
        return episodes

    def resolve(self, drama: Drama, episode: int):
        source = self.registry.get(drama.source_key)
        if source is None:
            raise ParseError("站源不可用，请到设置里检查")
        return source.resolve(self.client, drama, episode)

    # ---------- 下载 ----------
    def enqueue(self, drama: Drama, episodes: list[int]) -> int:
        if not episodes:
            return 0
        self.db.upsert_drama(drama)
        return self.jobs.enqueue(drama, episodes)

    def jobs_snapshot(self) -> list[dict]:
        return self.jobs.snapshot()

    def pause_job(self, job_id: str) -> None:
        self.jobs.pause(job_id)

    def resume_job(self, job_id: str) -> None:
        self.jobs.resume(job_id)

    def remove_job(self, job_id: str) -> None:
        self.jobs.remove(job_id)

    def pause_all(self) -> None:
        self.jobs.pause_all()

    def resume_all(self) -> None:
        self.jobs.resume_all()

    def clear_failed(self) -> int:
        return self.jobs.clear_failed()

    def active_jobs(self) -> int:
        return self.jobs.active_count()

    # ---------- 剧库 ----------
    def library(self) -> list[dict]:
        rows = self.db.library_dramas()
        for row in rows:
            merged = self.db.merged_for(str(row["drama_key"]))
            row["merged"] = len(merged)
            row["merged_bytes"] = sum(int(item.get("bytes") or 0) for item in merged)
        return rows

    def episodes(self, drama_key: str) -> list[dict]:
        return [job.to_dict() for job in self.db.episodes_for(drama_key)]

    def completed_files(self, drama_key: str) -> list[dict]:
        return [
            job.to_dict()
            for job in self.db.completed_jobs(drama_key)
            if job.file and Path(job.file).is_file()
        ]

    def merged_items(self, drama_key: str | None = None) -> list[dict]:
        return self.db.merged_for(drama_key) if drama_key else self.db.merged()

    def delete_merged(self, merged_id: str) -> bool:
        item = next((row for row in self.db.merged() if row["id"] == merged_id), None)
        if item is None:
            return False
        path = Path(str(item.get("file") or ""))
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:
            logger.warning("删除合并文件失败：%s", exc)
        self.db.delete_merged(merged_id)
        self._emit_log(f"已删除合并成品：{path.name}")
        return True

    def drama(self, drama_key: str) -> Drama | None:
        row = self.db.get_drama(drama_key)
        if not row:
            return None
        return Drama(
            source_key=str(row["source"]),
            source_name="",
            drama_id=str(row["source_id"]),
            title=str(row["title"]),
            total=int(row["total"] or 0),
            cover=str(row["cover"] or ""),
            sid=int(row["sid"] or 1),
        )

    def local_file(self, drama_key: str, episode: int) -> str:
        for job in self.db.completed_jobs(drama_key):
            if job.episode == episode and job.file and Path(job.file).is_file():
                return job.file
        return ""

    # ---------- 合并整部 ----------
    def merge_drama(
        self,
        drama_key: str,
        on_progress: ProgressCallback,
        cancel_event: threading.Event,
    ) -> dict:
        drama = self.drama(drama_key)
        if drama is None:
            raise ParseError("剧集信息不存在，请刷新剧库")
        jobs = [job for job in self.db.completed_jobs(drama_key) if job.file]
        if not jobs:
            raise ParseError("这部剧还没有已完成的分集，无法合并")
        files = [job.file for job in jobs if Path(job.file).is_file()]
        if len(files) != len(jobs):
            raise ParseError("部分分集文件缺失，请先在下载页重新下载")
        episodes = [job.episode for job in jobs]
        expected = list(range(min(episodes), max(episodes) + 1))
        missing = [number for number in expected if number not in set(episodes)]
        if missing:
            logger.warning("合并存在缺集：%s", missing[:12])
        title = sanitize_filename(drama.title)
        output = ensure_dir(self.config.download_path() / title) / f"{title}_全集.mp4"
        self._emit_log(f"开始合并《{drama.title}》共 {len(files)} 集 → {output.name}")
        merge_episodes(self.ffmpeg, files, output, on_progress, cancel_event)
        size = output.stat().st_size if output.is_file() else 0
        item = self.db.add_merged(drama, str(output), len(files), size)
        self._emit_log(f"合并完成：{output.name}（{size / 1024 / 1024:.1f} MB）")
        if missing:
            item["warning"] = f"缺少 {len(missing)} 集"
        return item

    # ---------- 设置 ----------
    def save_settings(self, values: dict) -> None:
        for key in (
            "download_dir",
            "concurrency",
            "interval",
            "retries",
            "timeout",
            "proxy",
            "cookie",
            "ffmpeg_path",
            "ffprobe_path",
            "theme",
            "volume",
        ):
            if key in values:
                setattr(self.config, key, values[key])
        self.config._normalize()  # noqa: SLF001 - 同一包内
        self.config.ensure_dirs()
        self.config.save()
        self.client.reconfigure()
        self.ffmpeg.refresh()
        self._emit_log("设置已保存")

    def add_source(self, name: str, base_url: str) -> SourceConfig:
        source = self.config.add_source(name, base_url)
        self.config.save()
        self.registry.reload(self.config)
        self._emit_log(f"已添加站源：{source.name}（{source.bases[0]}）")
        return source

    def remove_source(self, key: str) -> bool:
        removed = self.config.remove_source(key)
        if removed:
            self.config.save()
            self.registry.reload(self.config)
            self._emit_log(f"已移除站源：{key}")
        return removed

    def probe_source(self, url: str) -> tuple[bool, str]:
        return probe_site(self.client, url)

    def ffmpeg_status(self) -> str:
        if not self.ffmpeg.available:
            return "未找到 ffmpeg，请在设置里指定路径"
        version = self.ffmpeg.version()
        return version or f"已找到：{self.ffmpeg.binary}"

    def open_download_dir(self) -> None:
        ensure_dir(self.config.download_path())
