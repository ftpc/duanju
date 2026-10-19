"""SQLite 持久化：剧库、下载任务、合并成品。"""

from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Iterable

from .errors import StorageError
from .models import Drama, Job
from .util import short_id as _short_id

SCHEMA = """
CREATE TABLE IF NOT EXISTS dramas (
    key TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    source_id TEXT NOT NULL,
    title TEXT NOT NULL,
    total INTEGER NOT NULL DEFAULT 0,
    cover TEXT NOT NULL DEFAULT '',
    sid INTEGER NOT NULL DEFAULT 1,
    added_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    drama_key TEXT NOT NULL,
    drama_title TEXT NOT NULL,
    episode INTEGER NOT NULL,
    state TEXT NOT NULL,
    bytes INTEGER NOT NULL DEFAULT 0,
    total INTEGER NOT NULL DEFAULT 0,
    speed REAL NOT NULL DEFAULT 0,
    error TEXT NOT NULL DEFAULT '',
    file TEXT NOT NULL DEFAULT '',
    created INTEGER NOT NULL,
    updated INTEGER NOT NULL,
    revision INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_jobs_drama ON jobs(drama_key);
CREATE INDEX IF NOT EXISTS idx_jobs_state ON jobs(state);
CREATE TABLE IF NOT EXISTS merged (
    id TEXT PRIMARY KEY,
    drama_key TEXT NOT NULL,
    drama_title TEXT NOT NULL,
    file TEXT NOT NULL,
    episodes INTEGER NOT NULL,
    bytes INTEGER NOT NULL DEFAULT 0,
    created INTEGER NOT NULL
);
"""

_JOB_FIELDS = {
    "state",
    "bytes",
    "total",
    "speed",
    "error",
    "file",
    "revision",
    "drama_title",
}


def _now() -> int:
    return int(time.time())


class Database:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        try:
            self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        except sqlite3.Error as exc:
            raise StorageError(f"无法打开数据库：{exc}") from exc
        self._conn.row_factory = sqlite3.Row
        with self._lock, self._conn:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
            self._conn.executescript(SCHEMA)

    # ---------- 剧库 ----------
    def upsert_drama(self, drama: Drama) -> None:
        now = _now()
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO dramas(key, source, source_id, title, total, cover, sid, added_at, updated_at)
                VALUES(?,?,?,?,?,?,?,?,?)
                ON CONFLICT(key) DO UPDATE SET
                    title=excluded.title,
                    total=CASE WHEN excluded.total > 0 THEN excluded.total ELSE dramas.total END,
                    cover=CASE WHEN excluded.cover != '' THEN excluded.cover ELSE dramas.cover END,
                    sid=excluded.sid,
                    updated_at=excluded.updated_at
                """,
                (
                    drama.key,
                    drama.source_key,
                    drama.drama_id,
                    drama.title,
                    int(drama.total or 0),
                    drama.cover,
                    int(drama.sid or 1),
                    now,
                    now,
                ),
            )

    def get_drama(self, key: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM dramas WHERE key=?", (key,)).fetchone()
        return dict(row) if row else None

    def dramas(self, limit: int = 500) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM dramas ORDER BY updated_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(row) for row in rows]

    # ---------- 下载任务 ----------
    def add_jobs(self, drama: Drama, episodes: Iterable[int]) -> int:
        now = _now()
        added = 0
        with self._lock, self._conn:
            for episode in episodes:
                episode = int(episode)
                if episode <= 0:
                    continue
                job_id = _short_id(drama.key, episode)
                cursor = self._conn.execute(
                    """
                    INSERT OR IGNORE INTO jobs
                    (id, drama_key, drama_title, episode, state, created, updated)
                    VALUES(?,?,?,?,'queued',?,?)
                    """,
                    (job_id, drama.key, drama.title, episode, now, now),
                )
                added += cursor.rowcount
        return added

    def jobs(self, limit: int = 5000) -> list[Job]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM jobs ORDER BY created ASC, episode ASC LIMIT ?", (limit,)
            ).fetchall()
        return [Job.from_row(dict(row)) for row in rows]

    def job(self, job_id: str) -> Job | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        return Job.from_row(dict(row)) if row else None

    def next_queued(self, exclude: set[str] | None = None) -> Job | None:
        exclude = exclude or set()
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM jobs WHERE state='queued' ORDER BY created ASC, episode ASC LIMIT 50"
            ).fetchall()
        for row in rows:
            job = Job.from_row(dict(row))
            if job.id not in exclude:
                return job
        return None

    def update_job(self, job_id: str, **fields: Any) -> None:
        updates = {key: value for key, value in fields.items() if key in _JOB_FIELDS}
        if not updates:
            return
        updates["updated"] = _now()
        assignments = ", ".join(f"{key}=?" for key in updates)
        values = list(updates.values()) + [job_id]
        with self._lock, self._conn:
            self._conn.execute(f"UPDATE jobs SET {assignments} WHERE id=?", values)

    def delete_job(self, job_id: str) -> None:
        with self._lock, self._conn:
            self._conn.execute("DELETE FROM jobs WHERE id=?", (job_id,))

    def delete_jobs(self, job_ids: Iterable[str]) -> None:
        ids = list(job_ids)
        if not ids:
            return
        placeholders = ",".join("?" for _ in ids)
        with self._lock, self._conn:
            self._conn.execute(f"DELETE FROM jobs WHERE id IN ({placeholders})", ids)

    def reset_interrupted(self) -> int:
        with self._lock, self._conn:
            cursor = self._conn.execute(
                """
                UPDATE jobs SET state='paused', speed=0,
                       error='上次退出时任务未完成，点击继续'
                WHERE state IN ('queued','downloading')
                """
            )
        return cursor.rowcount

    def completed_jobs(self, drama_key: str) -> list[Job]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM jobs WHERE drama_key=? AND state='completed' ORDER BY episode ASC",
                (drama_key,),
            ).fetchall()
        return [Job.from_row(dict(row)) for row in rows]

    def job_counts(self, drama_key: str) -> dict[str, int]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT state, COUNT(*) AS count FROM jobs WHERE drama_key=? GROUP BY state",
                (drama_key,),
            ).fetchall()
        return {str(row["state"]): int(row["count"]) for row in rows}

    # ---------- 剧库聚合（下载页 / 剧库页） ----------
    def library_dramas(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT drama_key,
                       MAX(drama_title) AS title,
                       COUNT(*) AS total,
                       SUM(CASE WHEN state='completed' THEN 1 ELSE 0 END) AS completed,
                       SUM(CASE WHEN state='failed' THEN 1 ELSE 0 END) AS failed,
                       SUM(CASE WHEN state='downloading' THEN 1 ELSE 0 END) AS downloading,
                       MAX(updated) AS updated
                FROM jobs
                GROUP BY drama_key
                ORDER BY updated DESC
                """
            ).fetchall()
        return [dict(row) for row in rows]

    def episodes_for(self, drama_key: str) -> list[Job]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM jobs WHERE drama_key=? ORDER BY episode ASC", (drama_key,)
            ).fetchall()
        return [Job.from_row(dict(row)) for row in rows]

    # ---------- 合并成品 ----------
    def add_merged(
        self, drama: Drama, file: str, episodes: int, size: int
    ) -> dict[str, Any]:
        merged_id = _short_id("merged", drama.key, file)
        now = _now()
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT OR REPLACE INTO merged(id, drama_key, drama_title, file, episodes, bytes, created)
                VALUES(?,?,?,?,?,?,?)
                """,
                (merged_id, drama.key, drama.title, file, int(episodes), int(size), now),
            )
        return {
            "id": merged_id,
            "drama_key": drama.key,
            "drama_title": drama.title,
            "file": file,
            "episodes": int(episodes),
            "bytes": int(size),
            "created": now,
        }

    def merged(self, limit: int = 200) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM merged ORDER BY created DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(row) for row in rows]

    def merged_for(self, drama_key: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM merged WHERE drama_key=? ORDER BY created DESC", (drama_key,)
            ).fetchall()
        return [dict(row) for row in rows]

    def delete_merged(self, merged_id: str) -> None:
        with self._lock, self._conn:
            self._conn.execute("DELETE FROM merged WHERE id=?", (merged_id,))

    def close(self) -> None:
        with self._lock:
            try:
                self._conn.close()
            except sqlite3.Error:
                pass
