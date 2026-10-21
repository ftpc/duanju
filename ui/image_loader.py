"""异步图片加载器（基于 requests + ThreadPoolExecutor）。

设计原则：
- 子线程使用 requests 下载图片
- 下载完成后通过 Signal 将 (url, local_path) 传回主线程
- 主线程加载 QPixmap 并更新 UI
- 绝对不在子线程操作 UI
- 强制详细日志，便于调试
"""

from __future__ import annotations

import hashlib
import logging
import os
from concurrent.futures import ThreadPoolExecutor, Future
from pathlib import Path
from typing import Any

import requests
from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QPixmap

logger = logging.getLogger(__name__)

# 图片缓存目录：固定为 <数据目录>\cache\images，不再支持配置
_CACHE_DIR: Path | None = None


def _default_cache_dir() -> Path:
    """固定缓存目录：%APPDATA%\\DuanjuDownloader\\cache\\images（与日志同树）。"""
    from duanju.config import appdata_dir

    return appdata_dir() / "cache" / "images"


def set_cache_dir(path: "Path | str | None") -> bool:
    """设置图片缓存目录；目录不存在会尝试创建，并验证可写。返回是否可用。"""
    global _CACHE_DIR
    if path is None:
        _CACHE_DIR = None
        logger.warning("图片缓存目录被禁用")
        return False
    target = Path(path)
    try:
        target.mkdir(parents=True, exist_ok=True)
        probe = target / f".write-test-{os.getpid()}"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
    except OSError as exc:
        logger.error("图片缓存目录不可用 %s: %s", target, exc)
        return False
    _CACHE_DIR = target
    logger.info("图片缓存目录已设为: %s", target)
    return True


def get_cache_dir() -> Path | None:
    """当前生效的图片缓存目录。"""
    return _CACHE_DIR


# 首次导入时按固定规则初始化，保证任何入口都指向同一目录；app 启动时会用
# config.image_cache_path() 再设一次（同一路径）
set_cache_dir(_default_cache_dir())

# 默认请求头（绕过 CDN 防盗链）
_DEFAULT_HEADERS = {
    "Referer": "https://www.hongguoapp.cn/",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
}

# 线程池大小
_MAX_WORKERS = 4


class ImageLoader(QObject):
    """异步图片加载器，支持本地缓存。"""

    # 信号：图片加载完成，传递 URL 和本地文件路径
    image_loaded = Signal(str, str)  # url, local_path
    # 信号：图片加载失败，传递 URL 和错误信息
    image_failed = Signal(str, str)  # url, error

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self._cache: dict[str, str] = {}  # url -> 本地路径
        self._in_flight: set[str] = set()  # 正在下载的 URL 集合
        self._executor = ThreadPoolExecutor(max_workers=_MAX_WORKERS)
        self._futures: dict[str, Future] = {}  # url -> Future
        self._cache_dir_snapshot: Path | None = get_cache_dir()
        logger.info("ImageLoader 初始化完成，线程池大小: %d", _MAX_WORKERS)

    def _get_cache_path(self, url: str) -> Path | None:
        """根据 URL 生成缓存文件路径（目录改了会自动切到新目录）。"""
        cache_dir = get_cache_dir()
        if cache_dir is None:
            return None
        if getattr(self, "_cache_dir_snapshot", None) != cache_dir:
            # 切换了缓存目录：旧目录下的内存映射全部作废，避免命中不算数的旧路径
            self._cache_dir_snapshot = cache_dir
            self._cache.clear()
            logger.info("图片缓存目录变更，已清空内存缓存: %s", cache_dir)
        url_hash = hashlib.md5(url.encode()).hexdigest()
        ext = ".jpg" if "jpg" in url.lower() or "jpeg" in url.lower() else ".png"
        return cache_dir / f"{url_hash}{ext}"

    def get_cache_path(self, url: str) -> Path | None:
        """获取缓存路径（供外部调用）。"""
        return self._get_cache_path(url)

    def load(self, url: str) -> None:
        """开始加载图片。"""
        if not url:
            logger.warning("load() 被调用但 URL 为空")
            return

        logger.info("load() 被调用: %s", url[:80])

        # 检查内存缓存
        if url in self._cache:
            local_path = self._cache[url]
            logger.info("命中内存缓存: %s -> %s", url[:40], local_path)
            if Path(local_path).exists():
                logger.info("内存缓存文件存在，发射信号: %s", local_path)
                self.image_loaded.emit(url, local_path)
                return
            else:
                logger.warning("内存缓存记录的路径不存在，重新下载: %s", local_path)

        # 检查磁盘缓存
        cache_path = self._get_cache_path(url)
        if cache_path and cache_path.exists():
            logger.info("命中磁盘缓存: %s", cache_path)
            self._cache[url] = str(cache_path)
            logger.info("磁盘缓存文件存在，发射信号: %s", cache_path)
            self.image_loaded.emit(url, str(cache_path))
            return

        # 防止重复下载
        if url in self._in_flight:
            logger.warning("URL 正在下载中，跳过重复提交: %s", url[:60])
            return

        # 标记为正在下载
        self._in_flight.add(url)
        logger.info("标记为正在下载，当前下载中 URL 数: %d", len(self._in_flight))

        # 提交到线程池下载
        logger.info("提交下载任务到线程池: %s", url[:60])
        future = self._executor.submit(self._download_task, url, str(cache_path))
        self._futures[url] = future
        future.add_done_callback(lambda f: self._in_flight.discard(url))
        future.add_done_callback(lambda f: self._futures.pop(url, None))
        logger.info("下载任务已提交，等待完成...")

    def _download_task(self, url: str, cache_path: str) -> None:
        """在子线程中下载图片并保存。

        注意：此方法在子线程中运行，不能操作 UI。
        下载完成后通过信号通知主线程。
        """
        logger.info("[THREAD] 开始下载图片: %s", url[:80])
        try:
            logger.info("[THREAD] 发送 HTTP 请求: %s", url[:80])
            response = requests.get(url, headers=_DEFAULT_HEADERS, timeout=10)
            logger.info("[THREAD] HTTP 响应状态码: %d", response.status_code)
            response.raise_for_status()

            image_data = response.content
            logger.info("[THREAD] 收到图片数据，大小: %d bytes", len(image_data))

            if not image_data:
                raise ValueError("空响应")

            # 保存到缓存
            cache_p = Path(cache_path)
            cache_p.parent.mkdir(parents=True, exist_ok=True)
            cache_p.write_bytes(image_data)
            logger.info("[THREAD] 图片保存成功: %s", cache_p)

            # 关键：发送信号回主线程
            logger.info("[THREAD] 准备发射 image_loaded 信号: url=%s, path=%s", url[:40], cache_p)
            self.image_loaded.emit(url, str(cache_p))
            logger.info("[THREAD] 已发射 image_loaded 信号: %s", url[:60])

            # 更新内存缓存
            self._cache[url] = str(cache_p)

        except Exception as exc:
            logger.error("[THREAD] 图片下载异常 %s: %s", url[:60], exc, exc_info=True)
            self.image_failed.emit(url, str(exc))
        finally:
            # 无论成功失败，从下载集合中移除
            self._in_flight.discard(url)
            logger.info("[THREAD] 下载完成/失败，从 in_flight 移除: %s, 剩余: %d", url[:40], len(self._in_flight))

    def clear_cache(self) -> int:
        """清除内存缓存，返回清除的图片数量。"""
        count = len(self._cache)
        self._cache.clear()
        logger.info("清除 %d 个图片内存缓存", count)
        return count

    def shutdown(self) -> None:
        """关闭线程池。"""
        self._executor.shutdown(wait=False)
        logger.info("ImageLoader 已关闭")
