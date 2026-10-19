"""日志：文件轮转 + 控制台 + 可选回调（供 UI 日志面板使用）。"""

from __future__ import annotations

import logging
import logging.handlers
import sys
from collections.abc import Callable
from pathlib import Path

from .config import Config

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

    config.log_dir.mkdir(parents=True, exist_ok=True)
    file_handler = logging.handlers.RotatingFileHandler(
        config.log_dir / "app.log",
        maxBytes=5 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(logging.Formatter(_FORMAT, _DATEFMT))
    root.addHandler(file_handler)

    if callback is not None:
        callback_handler = CallbackHandler(callback)
        callback_handler.setLevel(logging.INFO)
        callback_handler.setFormatter(logging.Formatter(_FORMAT, _DATEFMT))
        root.addHandler(callback_handler)

    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("requests").setLevel(logging.WARNING)
    return config.log_dir
