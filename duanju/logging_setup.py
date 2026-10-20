"""日志：文件轮转 + 控制台 + 可选回调（供 UI 日志面板使用）。"""

from __future__ import annotations

import logging
import logging.handlers
import os
import sys
from collections.abc import Callable
from pathlib import Path

from .config import Config

# 强制使用绝对路径，避免打包后权限问题
_LOG_DIR = Path(r"C:\DuanjuLogs")


_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"
_DATEFMT = "%H:%M:%S"


class CallbackHandler(logging.Handler):
    """把日志同步转发给回调（UI 侧用 Qt 信号发射，可从任意线程安全调用）。"""

    def __init__(self, callback: Callable[[str], None]):
        super().__init__()
        self._callback = callback

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self._callback(self.format(record))
        except Exception:  # noqa: BLE001 - 日志绝不能影响主流程
            pass


def setup_logging(config: Config, callback: Callable[[str], None] | None = None) -> Path:
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    for handler in list(root.handlers):
        root.removeHandler(handler)

    console = logging.StreamHandler(sys.stderr)
    console.setLevel(logging.INFO)
    console.setFormatter(logging.Formatter(_FORMAT, _DATEFMT))
    root.addHandler(console)

    # 强制创建日志目录（绝对路径）
    try:
        _LOG_DIR.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        print(f"无法创建日志目录 {_LOG_DIR}: {exc}", file=sys.stderr)

    # 使用绝对路径的 FileHandler（覆盖模式）
    log_file = _LOG_DIR / "app.log"
    try:
        file_handler = logging.FileHandler(
            str(log_file),
            mode="w",  # w 代表每次启动清空覆盖
            encoding="utf-8",
        )
        file_handler.setLevel(logging.INFO)  # 只记录 INFO 及以上级别，防止日志过大
        file_handler.setFormatter(logging.Formatter(_FORMAT, _DATEFMT))
        root.addHandler(file_handler)
        print(f"日志文件: {log_file}", file=sys.stderr)
    except OSError as exc:
        print(f"无法写入日志文件 {log_file}: {exc}", file=sys.stderr)

    if callback is not None:
        callback_handler = CallbackHandler(callback)
        callback_handler.setLevel(logging.INFO)
        callback_handler.setFormatter(logging.Formatter(_FORMAT, _DATEFMT))
        root.addHandler(callback_handler)

    # 屏蔽底层库的废话
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("requests").setLevel(logging.WARNING)
    logging.getLogger("yt_dlp").setLevel(logging.WARNING)

    return _LOG_DIR