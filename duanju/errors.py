"""统一的错误类型。

UI 只依赖 ``AppError`` 的 ``category`` 与 ``retryable`` 做提示与重试决策，
不再把异常栈直接抛给用户。
"""

from __future__ import annotations


class AppError(Exception):
    """应用内所有可预期错误的基类。"""

    def __init__(self, message: str, *, category: str = "error", retryable: bool = False):
        super().__init__(message)
        self.message = message
        self.category = category
        self.retryable = retryable

    def __str__(self) -> str:
        return self.message


class NetworkError(AppError):
    def __init__(self, message: str):
        super().__init__(message, category="network", retryable=True)


class BlockedError(AppError):
    """被 WAF / 人机验证拦截。

    ``code`` 仅用于日志与上层状态判定（challenge=人机验证质询，
    forbidden=普通 403 拒绝），不再驱动任何 UI 弹窗。
    """

    def __init__(self, message: str, *, code: str = "challenge"):
        super().__init__(message, category="blocked", retryable=False)
        self.code = code


class NotFoundError(AppError):
    def __init__(self, message: str):
        super().__init__(message, category="not_found", retryable=False)


class ParseError(AppError):
    def __init__(self, message: str):
        super().__init__(message, category="parse", retryable=False)


class DownloadError(AppError):
    def __init__(self, message: str, *, retryable: bool = True):
        super().__init__(message, category="download", retryable=retryable)


class StorageError(AppError):
    def __init__(self, message: str):
        super().__init__(message, category="storage", retryable=False)


class ConfigError(AppError):
    def __init__(self, message: str):
        super().__init__(message, category="config", retryable=False)


class CancelledError(AppError):
    def __init__(self, message: str = "任务已取消"):
        super().__init__(message, category="cancelled", retryable=False)
