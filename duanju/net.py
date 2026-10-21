"""HTTP 客户端：会话复用、重试退避、按站点限速、代理 / Cookie、人机验证识别。"""

from __future__ import annotations

import logging
import random
import threading
import time
from typing import Any
from urllib.parse import urlsplit

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .config import Config
from .errors import BlockedError, NetworkError, NotFoundError
from .search_state import BLOCKED_HINT
from .version import APP_NAME, VERSION

logger = logging.getLogger(__name__)

# 注意：此 UA 与 ui/cf_challenge_dialog.py 中的 _STABLE_USER_AGENT 保持一致。
# Cloudflare 的 cf_clearance 与 UA 绑定，浏览器和 requests 必须使用相同 UA 字符串，
# 否则已验证的 cookie 会对后续请求失效。
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


class HttpClient:
    def __init__(self, config: Config):
        self._config = config
        self._lock = threading.Lock()
        self._last_request: dict[str, float] = {}
        # Challenge 验证结果（用户手动输入的 Cookie 和 UA）
        # Cloudflare 的 cf_clearance 与 UA 绑定，浏览器与 requests 必须使用相同 UA
        self._override_cookie: str = ""
        self._override_user_agent: str = ""
        self._session = self._build_session()

    # ---------- 生命周期 ----------
    def _build_session(self) -> requests.Session:
        session = requests.Session()
        session.headers.update(
            {
                "User-Agent": USER_AGENT,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.6",
                "Accept-Encoding": "gzip, deflate",
                "Connection": "keep-alive",
            }
        )
        retry = Retry(
            total=max(1, int(self._config.retries) + 1),
            connect=max(1, int(self._config.retries) + 1),
            read=max(1, int(self._config.retries) + 1),
            status=max(1, int(self._config.retries) + 1),
            backoff_factor=0.6,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=frozenset({"GET", "HEAD"}),
            raise_on_status=False,
            respect_retry_after_header=True,
        )
        adapter = HTTPAdapter(max_retries=retry, pool_connections=8, pool_maxsize=8)
        session.mount("https://", adapter)
        session.mount("http://", adapter)
        return session

    def reconfigure(self) -> None:
        with self._lock:
            old = self._session
            self._last_request.clear()
            self._override_cookie = ""
            self._override_user_agent = ""
            self._session = self._build_session()
        try:
            old.close()
        except Exception:  # noqa: BLE001
            pass

    def close(self) -> None:
        with self._lock:
            session, self._session = self._session, self._build_session()
        try:
            session.close()
        except Exception:  # noqa: BLE001
            pass

    # ---------- 请求 ----------
    def _proxies(self) -> dict[str, str] | None:
        proxy = self._config.proxy.strip()
        if not proxy:
            return None
        return {"http": proxy, "https": proxy}

    def _throttle(self, url: str) -> None:
        interval = max(0.0, float(self._config.interval))
        if interval <= 0:
            return
        host = urlsplit(url).netloc or url
        with self._lock:
            now = time.monotonic()
            last = self._last_request.get(host, 0.0)
            wait = interval - (now - last)
            if wait <= 0:
                self._last_request[host] = now
                return
            self._last_request[host] = now + wait
        time.sleep(wait + random.uniform(0.0, 0.15))

    def get(
        self,
        url: str,
        *,
        referer: str | None = None,
        headers: dict[str, str] | None = None,
        cookie: str | None = None,
        timeout: float | None = None,
        allow_redirects: bool = True,
    ) -> requests.Response:
        if not url.startswith(("http://", "https://")):
            raise NetworkError(f"无效地址：{url}")
        self._throttle(url)
        request_headers: dict[str, str] = {}
        if referer:
            request_headers["Referer"] = referer
        # 优先使用 challenge 验证结果中的 Cookie 和 UA
        cookie_value = cookie or self._override_cookie or self._config.cookie
        if cookie_value:
            request_headers["Cookie"] = cookie_value
        if headers:
            request_headers.update(headers)
        # 优先使用 challenge 验证结果中的 UA
        ua = self._override_user_agent or request_headers.get("User-Agent", USER_AGENT)

        logger.info("=== 正在发送请求 ===")
        logger.info("目标URL: %s", url)
        logger.info("使用的 Cookie: %s", cookie_value or "(无)")
        logger.info("使用的 UA: %s", ua)
        logger.info("====================")

        try:
            response = self._session.get(
                url,
                headers={**request_headers, "User-Agent": ua},
                timeout=(10, timeout if timeout is not None else self._config.timeout),
                proxies=self._proxies(),
                allow_redirects=allow_redirects,
            )
        except requests.exceptions.SSLError as exc:
            raise NetworkError(f"HTTPS 证书校验失败：{exc}") from exc
        except requests.exceptions.Timeout as exc:
            raise NetworkError("请求超时，请检查网络或稍后重试") from exc
        except requests.exceptions.RequestException as exc:
            raise NetworkError(f"网络请求失败：{exc}") from exc
        self._raise_for_status(response)
        # 注意：验证结果（cf_clearance）必须保留，Cloudflare 站点后续每个请求都要带，
        # 一旦请求成功就清除会导致第二次请求重新被质询。仅在用户重新验证或请求明确失败时清除。
        return response

    def apply_challenge_result(self, cookies: list[dict[str, str]], user_agent: str) -> None:
        """应用人机验证结果：将 Cookie 列表拼成字符串，设置 UA。

        调用时机：用户完成 Cloudflare 验证后，在搜索重试前调用。
        """
        with self._lock:
            parts = [f"{c['name']}={c['value']}" for c in cookies if c.get("name") and c.get("value")]
            self._override_cookie = "; ".join(parts)
            self._override_user_agent = user_agent
        logger.debug("已应用 challenge 结果，cookie数=%d", len(cookies))

    def clear_challenge_result(self) -> None:
        """清除人机验证结果。

        调用时机：用户手动修改设置中的 Cookie，或验证后重试仍然被质询（说明验证无效/已过期）。
        注意：不要再在"请求成功"时调用，否则 cf_clearance 会被立刻丢弃。
        """
        with self._lock:
            self._override_cookie = ""
            self._override_user_agent = ""
        logger.debug("已清除 challenge 结果")

    @property
    def has_challenge_result(self) -> bool:
        """当前是否已持有用户提供的人机验证结果。"""
        return bool(self._override_cookie)

    @staticmethod
    def _raise_for_status(response: requests.Response) -> None:
        """把 HTTP 错误码翻译成应用内异常。

        保留人机验证识别能力，但只通过 ``BlockedError.code`` 暴露判别结果
        （challenge / forbidden），供日志与三态判定使用，不再驱动 UI 弹框。
        """
        status = response.status_code
        if status < 400:
            return
        if status in (403, 429, 503):
            try:
                body = response.text[:4000]
            except Exception:  # noqa: BLE001
                body = ""
            mitigated = response.headers.get("cf-mitigated", "")
            if mitigated or "Just a moment" in body or "challenge-platform" in body:
                raise BlockedError(
                    f"{BLOCKED_HINT}（站点启用了人机验证，code=challenge）",
                    code="challenge",
                )
            if status == 403:
                raise BlockedError(
                    f"{BLOCKED_HINT}（站点返回 HTTP 403，code=forbidden）",
                    code="forbidden",
                )
        if status == 404:
            raise NotFoundError("页面不存在（可能已下架或 ID 有误）")
        raise NetworkError(f"站点返回 HTTP {status}")

    # ---------- 便捷方法 ----------
    @staticmethod
    def _decode(response: requests.Response) -> str:
        data = response.content
        if data.startswith(b"\xef\xbb\xbf"):
            data = data[3:]
        for encoding in ("utf-8", "gb18030", "big5"):
            try:
                return data.decode(encoding)
            except UnicodeDecodeError:
                continue
        return data.decode("utf-8", "ignore")

    def get_text(self, url: str, **kwargs: Any) -> str:
        return self._decode(self.get(url, **kwargs))

    def get_json(self, url: str, **kwargs: Any) -> Any:
        import json

        text = self.get_text(url, **kwargs).strip()
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise NetworkError("站点返回的不是有效 JSON，可能被拦截或接口变更") from exc

    def head(self, url: str, **kwargs: Any) -> requests.Response:
        return self.get(url, **kwargs)
