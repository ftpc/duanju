"""跨层数据模型。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Drama:
    source_key: str
    source_name: str
    drama_id: str
    title: str
    total: int = 0
    description: str = ""
    cover: str = ""
    sid: int = 1

    @property
    def key(self) -> str:
        return f"{self.source_key}:{self.drama_id}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_key": self.source_key,
            "source_name": self.source_name,
            "drama_id": self.drama_id,
            "title": self.title,
            "total": self.total,
            "description": self.description,
            "cover": self.cover,
            "sid": self.sid,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Drama":
        return cls(
            source_key=str(data.get("source_key", "")),
            source_name=str(data.get("source_name", "")),
            drama_id=str(data.get("drama_id", "")),
            title=str(data.get("title") or "短剧"),
            total=int(data.get("total") or 0),
            description=str(data.get("description") or ""),
            cover=str(data.get("cover") or ""),
            sid=int(data.get("sid") or 1),
        )


@dataclass(frozen=True)
class Episode:
    number: int
    title: str = ""
    page_url: str = ""
    sid: int = 1
    # 站源接口（苹果CMS 的 /api.php/provide/vod/）直给的媒体地址。
    # 多条剧集被站点打包成一段时（如"第1-20集"），这里存该段的 m3u8 直链，
    # resolve 时可直接使用，不必再去解析播放页。
    direct_url: str = ""

    def label(self) -> str:
        return self.title or f"第{self.number}集"


@dataclass(frozen=True)
class MediaPlan:
    """一次可下载 / 可播放的媒体地址。"""

    url: str
    referer: str = ""
    headers: dict[str, str] = field(default_factory=dict)
    kind: str = "hls"  # hls | mp4
    duration: float = 0.0

    def request_headers(self) -> dict[str, str]:
        headers = dict(self.headers)
        if self.referer:
            headers.setdefault("Referer", self.referer)
        return headers


@dataclass
class Job:
    id: str
    drama_key: str
    drama_title: str
    episode: int
    state: str = "queued"
    bytes: int = 0
    total: int = 0
    speed: float = 0.0
    error: str = ""
    file: str = ""
    created: int = 0
    updated: int = 0
    revision: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "drama_key": self.drama_key,
            "drama_title": self.drama_title,
            "episode": self.episode,
            "state": self.state,
            "bytes": self.bytes,
            "total": self.total,
            "speed": self.speed,
            "error": self.error,
            "file": self.file,
            "created": self.created,
            "updated": self.updated,
            "revision": self.revision,
        }

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> "Job":
        return cls(
            id=str(row["id"]),
            drama_key=str(row["drama_key"]),
            drama_title=str(row.get("drama_title") or ""),
            episode=int(row.get("episode") or 0),
            state=str(row.get("state") or "queued"),
            bytes=int(row.get("bytes") or 0),
            total=int(row.get("total") or 0),
            speed=float(row.get("speed") or 0.0),
            error=str(row.get("error") or ""),
            file=str(row.get("file") or ""),
            created=int(row.get("created") or 0),
            updated=int(row.get("updated") or 0),
            revision=int(row.get("revision") or 0),
        )


JOB_STATES = (
    "queued",
    "downloading",
    "paused",
    "failed",
    "completed",
)

STATE_LABELS = {
    "queued": "等待下载",
    "downloading": "下载中",
    "paused": "已暂停",
    "failed": "失败",
    "completed": "已完成",
}
