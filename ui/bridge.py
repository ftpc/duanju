"""UI 与后台线程的桥：所有耗时操作放到线程里，结果通过 Qt 信号回到主线程。"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QObject, Signal, Slot

logger = logging.getLogger(__name__)


class Bridge(QObject):
    job_updated = Signal(dict)
    jobs_changed = Signal(list)
    log_message = Signal(str)
    progress = Signal(str, float, int, float)  # task_id, ratio(-1=未知), 已下载, 速度

    _success = Signal(object, object)
    _failure = Signal(object, str)

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self._success.connect(self._on_success)
        self._failure.connect(self._on_failure)

    # ---------- 服务事件 ----------
    def attach(self, service: Any) -> None:
        service.on_job(self.job_updated.emit)
        service.on_jobs(self.jobs_changed.emit)
        # 日志统一走 logging 的 CallbackHandler（带级别与时间），避免与 on_log 重复

    # ---------- 后台任务 ----------
    @staticmethod
    def _describe(exc: BaseException) -> str:
        message = str(exc).strip()
        return message or exc.__class__.__name__

    def run(
        self,
        function: Callable[[], Any],
        on_ok: Callable[[Any], None] | None = None,
        on_err: Callable[[str], None] | None = None,
    ) -> None:
        def worker() -> None:
            try:
                result = function()
            except Exception as exc:  # noqa: BLE001
                logger.debug("后台任务失败", exc_info=True)
                self._failure.emit(on_err, self._describe(exc))
            else:
                self._success.emit(on_ok, result)

        threading.Thread(target=worker, daemon=True).start()

    def run_with_progress(
        self,
        task_id: str,
        function: Callable[[Callable[[float | None, int, float], None]], Any],
        on_ok: Callable[[Any], None] | None = None,
        on_err: Callable[[str], None] | None = None,
    ) -> None:
        def worker() -> None:
            def report(ratio: float | None, downloaded: int, speed: float) -> None:
                self.progress.emit(
                    task_id,
                    -1.0 if ratio is None else float(ratio),
                    int(downloaded),
                    float(speed),
                )

            try:
                result = function(report)
            except Exception as exc:  # noqa: BLE001
                logger.debug("后台任务失败", exc_info=True)
                self._failure.emit(on_err, self._describe(exc))
            else:
                self._success.emit(on_ok, result)

        threading.Thread(target=worker, daemon=True).start()

    # ---------- 信号槽 ----------
    @Slot(object, object)
    def _on_success(self, callback: object, result: object) -> None:
        if callable(callback):
            try:
                callback(result)
            except Exception:  # noqa: BLE001
                logger.exception("成功回调处理失败")

    @Slot(object, str)
    def _on_failure(self, callback: object, message: str) -> None:
        if callable(callback):
            try:
                callback(message)
            except Exception:  # noqa: BLE001
                logger.exception("失败回调处理失败")
        else:
            logger.error("后台任务失败：%s", message)
