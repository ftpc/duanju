"""站源适配层。

当前实现的是「苹果CMS（MacCMS / conch 模板）」类站点：

- 搜索：优先站点的 JSON 联想接口 ``/index.php/ajax/suggest``，失败回退 HTML 搜索页；
- 详情：``/voddetail/{id}.html`` 里的 ``/vodplay/{id}-{sid}-{集}.html`` 链接；
- 取流：``/vodplay/...`` 页内嵌 ``player_xxx={...}`` JSON 里的 m3u8 / mp4 地址。

多站源 = 配置里可以有多个 ``MacCmsSource``；用户可在设置里添加任意同类站点。
"""

from __future__ import annotations

import json
import re
from collections import Counter
from urllib.parse import quote, urljoin

from .config import Config, SourceConfig
from .errors import AppError, BlockedError, NetworkError, NotFoundError, ParseError
from .models import Drama, Episode, MediaPlan
from .net import USER_AGENT, HttpClient

_PLAYER_RE = re.compile(r"player_[A-Za-z0-9]+\s*=\s*(\{.*?\})\s*;?\s*</script>", re.S)
_DETAIL_LINK_RE = re.compile(r"/vodplay/(\d+)-(\d+)-(\d+)\.html")
_VODDETAIL_RE = re.compile(r'href="/voddetail/(\d+)\.html"[^>]*title="([^"]*)"')
_VODDETAIL_TEXT_RE = re.compile(r'href="/voddetail/(\d+)\.html"[^>]*>([^<]+)</a>')
_TITLE_RE = re.compile(r"<title>(.*?)</title>", re.S)


class MacCmsSource:
    """一个苹果CMS 站点的适配器。"""

    def __init__(self, config: SourceConfig):
        self.config = config
        self.key = config.key
        self.name = config.name
        self.bases = list(config.bases)

    # ---------- 基础请求 ----------
    def _get_html(self, client: HttpClient, path: str) -> tuple[str, str]:
        """按域名顺序尝试，返回 (html, 实际使用的 base)。"""
        last_error: Exception | None = None
        for base in self.bases:
            url = path if path.startswith("http") else urljoin(base + "/", path.lstrip("/"))
            try:
                html = client.get_text(
                    url,
                    referer=base + "/",
                    cookie=self.config.cookie or None,
                )
                if html.strip():
                    return html, base
                last_error = ParseError("站点返回了空页面")
            except (NetworkError, BlockedError, NotFoundError) as exc:
                last_error = exc
                continue
        if last_error is not None:
            raise last_error
        raise NetworkError("站源地址为空")

    # ---------- 搜索 ----------
    def search(self, client: HttpClient, query: str) -> list[Drama]:
        query = query.strip()
        if not query:
            return []
        quoted = quote(query)
        # 1) JSON 联想接口（更快、更稳、不受搜索页 WAF 影响）
        for base in self.bases:
            url = f"{base}/index.php/ajax/suggest?mid=1&wd={quoted}&limit=50"
            try:
                data = client.get_json(
                    url,
                    referer=base + "/",
                    cookie=self.config.cookie or None,
                )
            except (NetworkError, BlockedError, ParseError):
                continue
            items = data.get("list") if isinstance(data, dict) else None
            if not isinstance(items, list):
                continue
            dramas = self._parse_suggest(items, base)
            if dramas:
                return dramas
        # 2) HTML 搜索页
        last_error: Exception | None = None
        for base in self.bases:
            url = f"{base}/vodsearch/-------------.html?wd={quoted}"
            try:
                html = client.get_text(
                    url,
                    referer=base + "/",
                    cookie=self.config.cookie or None,
                )
            except (NetworkError, BlockedError, NotFoundError) as exc:
                last_error = exc
                continue
            dramas = self._parse_search_html(html)
            if dramas:
                return dramas
        if last_error is not None:
            raise last_error
        raise ParseError("没有搜索到匹配的剧集，请换个关键词或检查站源")

    def _parse_suggest(self, items: list, base: str = "") -> list[Drama]:
        dramas: list[Drama] = []
        seen: set[str] = set()
        for item in items:
            if not isinstance(item, dict):
                continue
            drama_id = str(item.get("id") or "").strip()
            title = str(item.get("name") or "").strip()
            if not drama_id or not title or drama_id in seen:
                continue
            seen.add(drama_id)
            cover = str(item.get("pic") or "").strip()
            if cover.startswith("//"):
                cover = "https:" + cover
            elif cover.startswith("/") and base:
                cover = urljoin(base + "/", cover.lstrip("/"))
            dramas.append(
                Drama(
                    source_key=self.key,
                    source_name=self.name,
                    drama_id=drama_id,
                    title=title,
                    cover=cover,
                )
            )
        return dramas

    def _parse_search_html(self, html: str) -> list[Drama]:
        pairs = _VODDETAIL_RE.findall(html)
        if not pairs:
            pairs = [(i, t.strip()) for i, t in _VODDETAIL_TEXT_RE.findall(html)]
        dramas: list[Drama] = []
        seen: set[str] = set()
        for drama_id, title in pairs:
            title = re.sub(r"\s+", " ", title or "").strip()
            if not drama_id or not title or drama_id in seen:
                continue
            seen.add(drama_id)
            dramas.append(
                Drama(
                    source_key=self.key,
                    source_name=self.name,
                    drama_id=drama_id,
                    title=title,
                )
            )
        return dramas

    # ---------- 详情 / 剧集 ----------
    def detail(self, client: HttpClient, drama: Drama) -> list[Episode]:
        html, base = self._get_html(client, f"/voddetail/{drama.drama_id}.html")
        links = _DETAIL_LINK_RE.findall(html)
        links = [row for row in links if row[0] == drama.drama_id]
        if not links:
            raise ParseError("详情页没有解析到剧集列表，站点结构可能已变化")
        sid_counter = Counter(int(row[1]) for row in links)
        sid = drama.sid if drama.sid in [s for s, _ in sid_counter.most_common()] else sid_counter.most_common(1)[0][0]
        numbers = sorted({int(row[2]) for row in links if int(row[1]) == sid})
        if not numbers:
            raise ParseError("详情页只列出了不可播放的剧集")
        drama.total = len(numbers)
        drama.sid = sid
        if drama.title in ("", "短剧"):
            match = _TITLE_RE.search(html)
            if match:
                drama.title = re.sub(r"[-_|].*$", "", match.group(1)).strip() or drama.title
        return [
            Episode(
                number=number,
                title=f"第{number}集",
                page_url=f"{base}/vodplay/{drama.drama_id}-{sid}-{number}.html",
                sid=sid,
            )
            for number in numbers
        ]

    # ---------- 取播放地址 ----------
    def resolve(self, client: HttpClient, drama: Drama, episode: int) -> MediaPlan:
        sid = int(drama.sid or 1)
        candidates = [sid] + [s for s in (1, 2, 3, 4, 5) if s != sid]
        last_error: Exception | None = None
        for attempt_sid in candidates:
            path = f"/vodplay/{drama.drama_id}-{attempt_sid}-{int(episode)}.html"
            try:
                html, base = self._get_html(client, path)
            except (NetworkError, BlockedError, NotFoundError) as exc:
                last_error = exc
                continue
            plan = self._extract_plan(html, base, drama, int(episode))
            if plan is not None:
                return plan
        if last_error is not None:
            raise last_error
        raise ParseError(
            f"第{episode}集播放页没有可用的视频地址（可能试看/需登录，或已下架）"
        )

    def _extract_plan(self, html: str, base: str, drama: Drama, episode: int) -> MediaPlan | None:
        for match in _PLAYER_RE.finditer(html):
            try:
                payload = json.loads(match.group(1))
            except (json.JSONDecodeError, TypeError):
                continue
            if not isinstance(payload, dict):
                continue
            raw_url = str(payload.get("url") or "").strip()
            if not raw_url:
                continue
            # 有的站点把多个清晰度塞在一个字段里，用分号分隔
            raw_url = raw_url.split(";")[0].strip()
            if raw_url.startswith("//"):
                raw_url = "https:" + raw_url
            media_url = urljoin(base + "/", raw_url)
            lowered = media_url.lower()
            if ".m3u8" in lowered:
                kind = "hls"
            elif ".mp4" in lowered:
                kind = "mp4"
            else:
                continue
            return MediaPlan(
                url=media_url,
                referer=base + "/",
                headers={
                    "User-Agent": USER_AGENT,
                    "Referer": base + "/",
                    "Accept": "*/*",
                    "Accept-Language": "zh-CN,zh;q=0.9",
                },
                kind=kind,
            )
        return None

    # ---------- 站点探测 ----------
    def probe(self, client: HttpClient) -> tuple[bool, str]:
        errors: list[str] = []
        for base in self.bases:
            try:
                html = client.get_text(base + "/", timeout=20, cookie=self.config.cookie or None)
            except AppError as exc:
                errors.append(f"{base}: {exc}")
                continue
            if "/voddetail/" in html or "vodplay" in html or "/vodsearch/" in html:
                return True, f"站点可用：{base}"
            errors.append(f"{base}: 页面可访问，但未检测到苹果CMS 特征")
        return False, "；".join(errors) or "站源不可用"


class SourceRegistry:
    """按配置创建 / 刷新站源实例。"""

    def __init__(self, config: Config):
        self._sources: dict[str, MacCmsSource] = {}
        self.reload(config)

    def reload(self, config: Config) -> None:
        self._sources = {
            source.key: MacCmsSource(source)
            for source in config.sources
            if source.enabled and source.bases
        }

    def get(self, key: str) -> MacCmsSource | None:
        return self._sources.get(key)

    def all(self) -> list[MacCmsSource]:
        return list(self._sources.values())

    def names(self) -> list[tuple[str, str]]:
        return [(source.key, source.name) for source in self._sources.values()]


def probe_site(client: HttpClient, base_url: str) -> tuple[bool, str]:
    """设置页「测试站点」用：不依赖已保存配置。"""
    base = base_url.strip().rstrip("/")
    if not base.startswith(("http://", "https://")):
        return False, "地址必须以 http:// 或 https:// 开头"
    try:
        html = client.get_text(base + "/", timeout=20)
    except AppError as exc:
        return False, str(exc)
    if "/voddetail/" in html or "/vodplay/" in html or "/vodsearch/" in html:
        return True, f"站点可用：{base}"
    return False, "页面可访问，但没有检测到苹果CMS 特征"
