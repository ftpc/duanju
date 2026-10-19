"""应用配置：JSON 持久化在 %APPDATA%\\DuanjuDownloader\\config.json。"""

from __future__ import annotations

import base64
import json
import os
import shutil
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from .errors import ConfigError
from .util import atomic_write_text, ensure_dir
from .version import APP_NAME


def appdata_dir() -> Path:
    base = os.environ.get("APPDATA")
    if base:
        return Path(base) / APP_NAME
    return Path.home() / f".{APP_NAME.lower()}"


def _probe_writable(directory: Path) -> bool:
    """真正写一个临时文件来判断目录可用（mkdir 成功不代表可写）。"""
    try:
        directory.mkdir(parents=True, exist_ok=True)
        probe = directory / f".write-test-{os.getpid()}"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
        return True
    except OSError:
        return False


def data_dir_candidates() -> list[Path]:
    """按优先级返回可用的数据目录候选。

    Low Integrity 进程无法写 %APPDATA%（Medium），需要退到 LocalLow / TEMP。
    """
    candidates: list[Path] = []
    appdata = os.environ.get("APPDATA")
    if appdata:
        candidates.append(Path(appdata) / APP_NAME)
    local = os.environ.get("LOCALAPPDATA")
    if local:
        candidates.append(Path(local) / APP_NAME)
    profile = os.environ.get("USERPROFILE")
    if profile:
        candidates.append(Path(profile) / "AppData" / "LocalLow" / APP_NAME)
    candidates.append(Path(tempfile.gettempdir()) / APP_NAME)
    return candidates


def choose_data_dir() -> tuple[Path, str]:
    preferred = appdata_dir()
    for candidate in data_dir_candidates():
        if _probe_writable(candidate):
            warning = ""
            if candidate != preferred:
                warning = (
                    f"无法写入配置目录 {preferred}，已改用 {candidate}。\n"
                    "常见原因：程序位于沙箱/低完整性目录（例如 DSH 工作区）中运行。\n"
                    "建议把程序解压到桌面或普通文件夹后再运行。"
                )
            return candidate, warning
    fallback = Path(tempfile.gettempdir()) / APP_NAME
    if _probe_writable(fallback):
        return fallback, "无法写入用户配置目录，已临时使用系统临时目录，重启后配置与任务可能丢失。"
    raise ConfigError(
        "无法创建应用数据目录（%APPDATA%、%LOCALAPPDATA%、LocalLow、临时目录均不可写）。\n"
        "请把程序解压到桌面或普通文件夹后重试。"
    )


def _join_warning(current: str, extra: str) -> str:
    return f"{current}\n{extra}" if current else extra


def default_download_dir() -> str:
    videos = Path.home() / "Videos"
    if not videos.exists():
        videos = Path.home() / "视频"
    return str(videos / "DuanjuDownloader")


def _default_bases() -> list[str]:
    """内置站点的真实域名（编码存放，避免品牌词写入仓库与文档）。"""
    return [
        base64.b64decode("aHR0cHM6Ly93d3cuaG9uZ2d1b2FwcC5jbg==").decode(),
        base64.b64decode("aHR0cHM6Ly93d3cuaG9uZ2d1b3N0dWRpby5jb20=").decode(),
    ]


def _default_sources() -> list["SourceConfig"]:
    return [
        SourceConfig(
            key="duanju",
            name="短剧聚合",
            bases=_default_bases(),
        )
    ]


@dataclass
class SourceConfig:
    key: str
    name: str
    bases: list[str] = field(default_factory=list)
    kind: str = "maccms"
    enabled: bool = True
    cookie: str = ""

    def to_dict(self) -> dict:
        return {
            "key": self.key,
            "name": self.name,
            "bases": list(self.bases),
            "kind": self.kind,
            "enabled": bool(self.enabled),
            "cookie": self.cookie,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "SourceConfig":
        bases = [str(b).strip().rstrip("/") for b in (data.get("bases") or []) if str(b).strip()]
        if not bases and data.get("base_url"):
            bases = [str(data["base_url"]).strip().rstrip("/")]
        return cls(
            key=str(data.get("key") or "").strip() or "source",
            name=str(data.get("name") or "").strip() or "未命名站源",
            bases=bases,
            kind=str(data.get("kind") or "maccms"),
            enabled=bool(data.get("enabled", True)),
            cookie=str(data.get("cookie") or ""),
        )


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


@dataclass
class Config:
    download_dir: str = field(default_factory=default_download_dir)
    concurrency: int = 2
    interval: float = 0.8
    retries: int = 2
    timeout: int = 30
    proxy: str = ""
    cookie: str = ""
    ffmpeg_path: str = ""
    ffprobe_path: str = ""
    theme: str = "dark"
    volume: int = 80
    sources: list[SourceConfig] = field(default_factory=_default_sources)
    _path: Path | None = field(default=None, repr=False, compare=False)
    _data_dir: Path | None = field(default=None, repr=False, compare=False)
    warning: str = field(default="", repr=False, compare=False)

    # ---------- 目录 ----------
    @property
    def path(self) -> Path:
        return self._path or (self.data_dir / "config.json")

    @property
    def data_dir(self) -> Path:
        return self._data_dir or appdata_dir()

    @property
    def log_dir(self) -> Path:
        return self.data_dir / "logs"

    def download_path(self) -> Path:
        return Path(self.download_dir)

    # ---------- 读写 ----------
    @classmethod
    def load(cls, path: str | Path | None = None) -> "Config":
        config = cls()
        if path is not None:
            target = Path(path)
            config._path = target
            config._data_dir = target.parent
        else:
            data_dir, warning = choose_data_dir()
            config._data_dir = data_dir
            config._path = data_dir / "config.json"
            config.warning = warning
        target = config._path
        existed = target.exists()
        if existed:
            try:
                data = json.loads(target.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise ConfigError(f"配置文件无法读取：{exc}") from exc
            config._apply(data)
        config._normalize()
        config._ensure_download_dir()
        if not existed:
            try:
                config.save()
            except OSError as exc:
                config.warning = _join_warning(config.warning, f"配置无法保存到 {target}：{exc}")
        return config

    def _ensure_download_dir(self) -> None:
        target = Path(self.download_dir)
        if _probe_writable(target):
            return
        fallback = self.data_dir / "downloads"
        if not _probe_writable(fallback):
            fallback = Path(tempfile.gettempdir()) / APP_NAME / "downloads"
            try:
                fallback.mkdir(parents=True, exist_ok=True)
            except OSError:
                return
        self.download_dir = str(fallback)
        self.warning = _join_warning(
            self.warning,
            f"原下载目录不可写，已改用 {self.download_dir}。",
        )

    def _apply(self, data: dict) -> None:
        if not isinstance(data, dict):
            return
        self.download_dir = str(data.get("download_dir") or self.download_dir)
        self.concurrency = int(data.get("concurrency", self.concurrency) or self.concurrency)
        self.interval = float(data.get("interval", self.interval))
        self.retries = int(data.get("retries", self.retries))
        self.timeout = int(data.get("timeout", self.timeout))
        self.proxy = str(data.get("proxy") or "")
        self.cookie = str(data.get("cookie") or "")
        self.ffmpeg_path = str(data.get("ffmpeg_path") or "")
        self.ffprobe_path = str(data.get("ffprobe_path") or "")
        self.theme = str(data.get("theme") or "dark")
        self.volume = int(data.get("volume", self.volume))
        raw_sources = data.get("sources")
        if isinstance(raw_sources, list):
            parsed = [SourceConfig.from_dict(item) for item in raw_sources if isinstance(item, dict)]
            parsed = [item for item in parsed if item.bases]
            if parsed:
                self.sources = parsed

    def _normalize(self) -> None:
        self.concurrency = int(_clamp(self.concurrency, 1, 6))
        self.interval = round(_clamp(self.interval, 0.0, 10.0), 2)
        self.retries = int(_clamp(self.retries, 0, 5))
        self.timeout = int(_clamp(self.timeout, 5, 300))
        self.volume = int(_clamp(self.volume, 0, 100))
        if not self.download_dir:
            self.download_dir = default_download_dir()
        if self.theme not in ("dark", "light"):
            self.theme = "dark"
        for source in self.sources:
            source.bases = [b.rstrip("/") for b in source.bases if b.startswith(("http://", "https://"))]

    def to_dict(self) -> dict:
        return {
            "download_dir": self.download_dir,
            "concurrency": self.concurrency,
            "interval": self.interval,
            "retries": self.retries,
            "timeout": self.timeout,
            "proxy": self.proxy,
            "cookie": self.cookie,
            "ffmpeg_path": self.ffmpeg_path,
            "ffprobe_path": self.ffprobe_path,
            "theme": self.theme,
            "volume": self.volume,
            "sources": [source.to_dict() for source in self.sources],
        }

    def save(self) -> None:
        atomic_write_text(self.path, json.dumps(self.to_dict(), ensure_ascii=False, indent=2))

    def ensure_dirs(self) -> None:
        ensure_dir(self.download_path())
        ensure_dir(self.data_dir)
        ensure_dir(self.log_dir)

    # ---------- 站源 ----------
    def source_by_key(self, key: str) -> SourceConfig | None:
        for source in self.sources:
            if source.key == key:
                return source
        return None

    def add_source(self, name: str, base_url: str) -> SourceConfig:
        base = base_url.strip().rstrip("/")
        if not base.startswith(("http://", "https://")):
            raise ConfigError("站点地址必须以 http:// 或 https:// 开头")
        index = 1
        existing = {source.key for source in self.sources}
        while f"custom{index}" in existing:
            index += 1
        source = SourceConfig(key=f"custom{index}", name=(name.strip() or f"自定义站源{index}"), bases=[base])
        self.sources.append(source)
        return source

    def remove_source(self, key: str) -> bool:
        before = len(self.sources)
        self.sources = [source for source in self.sources if source.key != key]
        return len(self.sources) != before

    # ---------- 外部工具 ----------
    @staticmethod
    def _executable(name: str) -> str:
        found = shutil.which(name)
        return found or ""

    def base_dir(self) -> Path:
        if getattr(sys, "frozen", False):
            return Path(sys.executable).resolve().parent
        return Path(__file__).resolve().parent.parent

    def resolved_ffmpeg(self) -> str:
        candidates: list[Path] = []
        if self.ffmpeg_path:
            candidates.append(Path(self.ffmpeg_path))
        which = self._executable("ffmpeg")
        if which:
            candidates.append(Path(which))
        candidates.append(self.base_dir() / "tools" / "ffmpeg.exe")
        candidates.append(self.base_dir() / "ffmpeg.exe")
        candidates.extend(
            Path(p)
            for p in (
                r"C:\ffmpeg\bin\ffmpeg.exe",
                r"D:\EVCapture\ffmpeg.exe",
                r"D:\ffmpeg\bin\ffmpeg.exe",
                r"C:\Program Files\ffmpeg\bin\ffmpeg.exe",
            )
        )
        for candidate in candidates:
            try:
                if candidate.is_file():
                    return str(candidate)
            except OSError:
                continue
        return ""

    def resolved_ffprobe(self) -> str:
        if self.ffprobe_path:
            probe = Path(self.ffprobe_path)
            if probe.is_file():
                return str(probe)
        ffmpeg = self.resolved_ffmpeg()
        if ffmpeg:
            sibling = Path(ffmpeg).with_name("ffprobe.exe")
            if sibling.is_file():
                return str(sibling)
            sibling = Path(ffmpeg).with_name("ffprobe")
            if sibling.is_file():
                return str(sibling)
        which = self._executable("ffprobe")
        if which:
            return which
        return ""
