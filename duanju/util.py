"""通用小工具。"""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
import sys
from pathlib import Path

_ILLEGAL = re.compile(r'[\\/:*?"<>|\x00-\x1f]')
_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


def sanitize_filename(name: str, *, fallback: str = "未命名", max_len: int = 60) -> str:
    """清洗 Windows 非法文件名字符，并处理保留名 / 结尾点空格。"""
    text = _ILLEGAL.sub("_", str(name or "")).strip()
    text = re.sub(r"[. ]+$", "", text)
    if not text:
        text = fallback
    if text.upper() in _RESERVED:
        text = "_" + text
    return text[:max_len].strip() or fallback


def episode_filename(number: int, ext: str = ".mp4", label: str = "") -> str:
    """分段剧（第1-20集）用真实标题命名，避免"第01集.mp4 里装着 20 集"。"""
    caption = sanitize_filename(label, fallback="")
    if caption and caption != f"第{int(number)}集":
        return f"{caption}{ext}"
    return f"第{int(number):02d}集{ext}"


def human_size(size: float | int) -> str:
    size = float(size or 0)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1024
    return f"{size:.1f} TB"


def human_speed(bytes_per_second: float) -> str:
    if not bytes_per_second or bytes_per_second <= 0:
        return ""
    return human_size(bytes_per_second) + "/s"


def short_id(*parts: object) -> str:
    raw = "\x1f".join(str(p) for p in parts).encode("utf-8")
    return hashlib.sha1(raw).hexdigest()[:16]


def ensure_dir(path: str | Path) -> Path:
    p = Path(path)
    p.mkdir(parents=True, exist_ok=True)
    return p


def atomic_write_text(path: str | Path, text: str, encoding: str = "utf-8") -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".writing")
    with open(tmp, "w", encoding=encoding, newline="\n") as fh:
        fh.write(text)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, target)


def open_in_explorer(path: str | Path, *, select: bool = False) -> None:
    """在资源管理器中打开目录或定位文件。"""
    p = Path(path)
    if sys.platform == "win32":
        if p.is_file() and select:
            subprocess.Popen(["explorer", "/select,", str(p)])
        else:
            target = p if p.is_dir() else p.parent
            os.startfile(str(target))  # noqa: S606 - 用户主动点击
        return
    if sys.platform == "darwin":
        subprocess.Popen(["open", str(p if p.is_dir() else p.parent)])
    else:
        subprocess.Popen(["xdg-open", str(p if p.is_dir() else p.parent)])


def reveal_file(path: str | Path) -> None:
    open_in_explorer(path, select=True)


def safe_float(value: object, default: float = 0.0) -> float:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def safe_int(value: object, default: int = 0) -> int:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
