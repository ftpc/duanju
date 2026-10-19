"""HTTP 客户端：会话复用、重试退避、按站点限速、代理 / Cookie、人机验证识别。"""

from __future__ import annotations

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
from .version import APP_NAME, VERSION

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


class HttpClient:
    def __init__(self, config: Config):
        self._config = config
        self._lock = threading.Lock()
        self._last_request: dict[str, float] = {}
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
        cookie_value = cookie if cookie is not None else self._config.cookie
        if cookie_value:
            request_headers["Cookie"] = cookie_value
        if headers:
            request_headers.update(headers)
        try:
            response = self._session.get(
                url,
                headers=request_headers,
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
        return response

    @staticmethod
    def _raise_for_status(response: requests.Response) -> None:
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
                    "站点要求人机验证（Cloudflare），请在浏览器访问一次该站后重试，或改用其它站源"
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
