"""站源适配层。"""

from __future__ import annotations

import json
import logging
import re
from collections import Counter
from urllib.parse import quote, urljoin

from .config import Config, SourceConfig
from .errors import AppError, BlockedError, NetworkError, NotFoundError, ParseError
from .models import Drama, Episode, MediaPlan
from .net import USER_AGENT, HttpClient

logger = logging.getLogger(__name__)

_PLAYER_RE = re.compile(r"player_[A-Za-z0-9]+\s*=\s*(\{.*?\})\s*;?\s*</script>", re.S)
_DETAIL_LINK_RE = re.compile(r"/(?:vodplay|play)/(\d+)-(\d+)-(\d+)\.html")
_VODDETAIL_RE = re.compile(r'href="/(?:voddetail|vod)/(\d+)\.html"[^>]*title="([^"]*)"')
_VODDETAIL_TEXT_RE = re.compile(r'href="/(?:voddetail|vod)/(\d+)\.html"[^>]*>([^<]+)</a>')
_TITLE_RE = re.compile(r"<title>(.*?)</title>", re.S)
# 苹果CMS 标准数据接口：返回 vod_play_from / vod_play_url（含全部分集或分段直链）。
# 站点改版后详情页可能只暴露第 1 集（其余集/段被折叠进播放器脚本或干脆只在接口里），
# 解析详情页拿不到多集，因此接口是详情/取流的主兜底来源。
_API_DETAIL_PATH = "/api.php/provide/vod/?ac=detail&ids={}"


class MacCmsSource:
    def __init__(self, config: SourceConfig):
        self.config = config
        self.key = config.key
        self.name = config.name
        self.bases = list(config.bases)
        # drama_id -> (base, [(段标题, 直链), ...])；命中 API 的剧集缓存下来，
        # 首次解析后播放/下载都不必重复请求接口；空结果单独记录，避免反复打接口。
        self._api_cache: dict[str, tuple[str, list[tuple[str, str]]]] = {}
        self._api_miss: set[str] = set()
        # 接口返回的真实总集数（详情页只列 1 集时以它为准，避免 UI 显示成"4 集"）
        self._api_total: dict[str, int] = {}
        # 详情阶段确认"剧集号=接口分段号"的剧；只有这些剧的 resolve 才优先走接口，
        # 页面本身能列全多集的剧仍走原 HTML 解析，不额外多打接口请求。
        self._api_mode: set[str] = set()

    def _get_html(self, client: HttpClient, path: str) -> tuple[str, str]:
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

    def search(self, client: HttpClient, query: str) -> list[Drama]:
        """搜索剧集。

        约定（与 UI 三态对应）：

        * suggest 接口可用但返回空列表 → 直接返回空结果，**不再回退** HTML 搜索页；
        * suggest 被拦截（BlockedError）→ 原样上抛，由上层判定为 blocked；
        * 仅当所有 base 的 suggest 都因网络/解析失败时，才回退 HTML 搜索页；
        * HTML 搜索页 404/403 一律按"无结果"返回并记 warning，不外抛。
        """
        query = query.strip()
        if not query:
            return []
        quoted = quote(query)

        last_error: Exception | None = None
        for base in self.bases:
            url = f"{base}/index.php/ajax/suggest?mid=1&wd={quoted}&limit=50"
            try:
                data = client.get_json(url, referer=base + "/", cookie=self.config.cookie or None)
            except BlockedError:
                # 站源受限：交上层判定为 blocked 状态，继续换 base 只会重复被拦截
                raise
            except (NetworkError, ParseError) as exc:
                last_error = exc
                continue

            items = data.get("list") if isinstance(data, dict) else None
            if not isinstance(items, list):
                logger.warning("搜索接口返回结构异常（%s），按空结果处理", base)
                return []
            dramas = self._parse_suggest(items, base)
            # suggest 是权威结果：返回空即"确实没有"，直接返回空列表，不再回退 HTML 搜索页
            return dramas

        # 到这里说明所有 base 的 suggest 都不可用，才尝试 HTML 搜索页
        if not self.bases:
            raise NetworkError("站源地址为空")
        html_unavailable = False
        for base in self.bases:
            url = f"{base}/vodsearch/-------------.html?wd={quoted}"
            try:
                html = client.get_text(url, referer=base + "/", cookie=self.config.cookie or None)
            except (BlockedError, NotFoundError) as exc:
                # 搜索页被拦截或不存在：按"无结果"返回，不上升为异常
                logger.warning("搜索页不可用（%s），按无结果处理：%s", base, exc)
                html_unavailable = True
                continue
            except NetworkError as exc:
                last_error = exc
                continue
            dramas = self._parse_search_html(html)
            if dramas:
                return dramas

        if html_unavailable:
            return []
        if last_error is not None:
            raise last_error
        return []

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
            dramas.append(Drama(source_key=self.key, source_name=self.name, drama_id=drama_id, title=title, cover=cover))
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
            dramas.append(Drama(source_key=self.key, source_name=self.name, drama_id=drama_id, title=title))
        return dramas

    # ---------- 首页推荐 / 分类列表 ----------
    # 卡片链接形态按站点区分：老站(hongguoapp) 用 /voddetail/{id}.html，djw123 用 /vod/{id}.html
    _PLACEHOLDER_HINTS = ("load.gif", "loading.", "placeholder", "nopic")

    # 苹果CMS 伪静态 URL 的 11 个连字符段里，第 3 段起是页码位：
    # /vodshow/51--------{page}---.html、/show/duanju--------{page}---.html
    # 页码留空即第 1 页（站点显示为 -----------）。此处按域名给出分类流模板与每页条数。
    _CATALOG_TEMPLATES: dict[str, tuple[str, int]] = {
        "hongguoapp": ("/vodshow/51--------{page}---.html", 36),
        "djw123": ("/show/duanju--------{page}---.html", 48),
    }

    def _catalog(self) -> tuple[str, int] | None:
        """返回 (分类页路径模板, 每页条数)；不支持分页的站点返回 None。"""
        base_url = (self.bases[0] if self.bases else "").lower()
        for domain, template in self._CATALOG_TEMPLATES.items():
            if domain in base_url:
                return template
        return None

    @property
    def page_size(self) -> int:
        catalog = self._catalog()
        return catalog[1] if catalog else 0

    @property
    def supports_paging(self) -> bool:
        return self._catalog() is not None

    def _site_paths(self) -> list[str]:
        """推荐页候选路径（按域名判断，不依赖配置里的 key 字面量）。"""
        base_url = (self.bases[0] if self.bases else "").lower()
        if "hongguoapp" in base_url:
            # 首屏改走分类流 /vodshow/51-----------.html（36 条/页、可按页续取）；
            # 站点首页 "/" 为人工编排位（24 条、无分页），仅作解析失败时的回退。
            return ["/vodshow/51-----------.html", "/"]
        if "djw123" in base_url:
            return ["/show/duanju-----------.html", "/type/duanju.html", "/"]
        return ["/"]

    def _card_href(self) -> str:
        base_url = (self.bases[0] if self.bases else "").lower()
        return "/vod/" if "djw123" in base_url else "/voddetail/"

    @staticmethod
    def _total_pages(html: str, template: str) -> int:
        """从分类页 HTML 中解析总页数；解析不到返回 0。"""
        prefix, _, suffix = template.partition("{page}")
        pattern = re.compile(re.escape(prefix) + r"(\d+)" + re.escape(suffix))
        pages = [int(value) for value in pattern.findall(html)]
        if pages:
            return max(pages)
        # 老站页脚形如 <div class="hl-page-total">1&nbsp;/&nbsp;985页</div>
        match = re.search(
            r'hl-page-total[^>]*>\s*\d+\s*(?:&nbsp;|\s)*/\s*(?:&nbsp;|\s)*(\d+)',
            html,
        )
        return int(match.group(1)) if match else 0

    def get_homepage(self, client: HttpClient, page: int = 1) -> list[Drama]:
        """获取发现页列表（page=1 即首屏），保留旧签名。"""
        return self.catalog_page(client, page)[0]

    def catalog_page(self, client: HttpClient, page: int = 1) -> tuple[list[Drama], bool]:
        """抓取分类流指定页，返回 (剧集列表, 是否还有下一页)。

        第 1 页路径与站点默认分类入口一致（页码位留空），页码 >=2 时填入模板；
        页码越界时站点会返回空列表，此时按"没有下一页"处理。
        """
        page = max(1, int(page))
        catalog = self._catalog()
        if catalog is not None:
            template, page_size = catalog
            path = template.format(page="" if page == 1 else page)
            try:
                html, base = self._get_html(client, path)
            except Exception:
                # 第 1 页仍允许退回旧候选路径；续页必须把真实错误交给 UI
                if page > 1:
                    raise
            else:
                dramas = self._parse_homepage_html(html, base)
                if dramas:
                    total = self._total_pages(html, template)
                    has_more = page < total if total else len(dramas) >= page_size
                    return dramas, has_more
                if page > 1:
                    return [], False

        for path in self._site_paths():
            try:
                html, base = self._get_html(client, path)
            except Exception:
                continue
            dramas = self._parse_homepage_html(html, base)
            if dramas:
                return dramas, False
        return [], False

    @classmethod
    def _extract_cover(cls, block: str) -> str:
        """统一封面提取。

        老站分类页把封面放在 <a data-original>，两站首页都放在 <img data-original>，
        因此先按 data-original / data-src / data-echo 取，最后才退回 <img src>，
        并过滤 loading.png / load.gif 之类的占位图。
        """
        for pattern in (
            r'data-original="([^"]+)"',
            r'data-src="([^"]+)"',
            r'data-echo="([^"]+)"',
            r'<img[^>]*?src="([^"]+)"',
        ):
            for raw in re.findall(pattern, block, re.I):
                cover = raw.strip()
                if not cover or cover.startswith("data:"):
                    continue
                if any(hint in cover.lower() for hint in cls._PLACEHOLDER_HINTS):
                    continue
                return cover
        return ""

    def _parse_homepage_html(self, html: str, base: str) -> list[Drama]:
        """从 HTML 中提取带封面的剧集卡片。"""
        dramas: list[Drama] = []
        seen: set[str] = set()
        pattern = re.compile(
            r'(<a[^>]+href="' + re.escape(self._card_href()) + r'(\d+)\.html"[^>]*>.*?</a>)',
            re.S | re.I,
        )
        for match in pattern.finditer(html):
            full_block = match.group(1)
            drama_id = match.group(2)
            if not drama_id or drama_id in seen:
                continue

            cover = self._extract_cover(full_block)
            if not cover:
                # 个别模板把封面挂在相邻节点，向后多看一段
                cover = self._extract_cover(html[match.start():match.end() + 600])
            if not cover:
                continue

            title = ""
            title_match = re.search(r'title="([^"]+)"', full_block, re.I)
            if title_match:
                title = title_match.group(1)
            else:
                alt_match = re.search(r'alt="([^"]+)"', full_block, re.I)
                if alt_match:
                    title = alt_match.group(1)
                else:
                    text_match = re.search(r'>([^<]+)<', full_block)
                    if text_match:
                        title = text_match.group(1).strip()
            title = re.sub(r"\s+", " ", title or "").strip()
            if not title:
                continue

            seen.add(drama_id)
            if cover.startswith("//"):
                cover = "https:" + cover
            elif cover.startswith("/") and base:
                cover = urljoin(base + "/", cover.lstrip("/"))
            dramas.append(
                Drama(source_key=self.key, source_name=self.name, drama_id=drama_id, title=title, cover=cover)
            )
        return dramas

    # ---------- 详情 / 剧集 ----------
    def detail(self, client: HttpClient, drama: Drama) -> list[Episode]:
        """解析剧集列表。

        先按详情页 HTML 解析（保持原有行为）；当页面只列出 1 集（站点把全集/多集
        打包进单个播放页，或详情页不再渲染选集）时，用苹果CMS 数据接口补齐，
        拿到"第1-20集 / 第21-40集"这类分段直链，多集在线播放才有后续可追加。
        """
        episodes: list[Episode] = []
        html_error: Exception | None = None
        try:
            episodes = self._detail_from_html(client, drama)
        except BlockedError:
            # 站源受限必须原样上抛，不能被接口兜底掩盖。
            raise
        except Exception as exc:  # noqa: BLE001
            html_error = exc

        if len(episodes) > 1:
            return episodes

        api_episodes = self._api_episodes(client, drama)
        if len(api_episodes) > len(episodes):
            logger.info(
                "站源 %s 详情页仅 %d 集，改用接口分段：%d 段（剧 %s）",
                self.key, len(episodes), len(api_episodes), drama.drama_id,
            )
            return api_episodes
        if episodes:
            return episodes
        raise html_error or ParseError("详情页没有解析到剧集列表")

    def _detail_from_html(self, client: HttpClient, drama: Drama) -> list[Episode]:
        paths_to_try = [
            f"/voddetail/{drama.drama_id}.html",
            f"/detail/{drama.drama_id}.html",
            f"/vod/{drama.drama_id}.html",
            f"/index.php/vod/detail/id/{drama.drama_id}.html",
        ]
        html, base = "", ""
        for path in paths_to_try:
            try:
                html, base = self._get_html(client, path)
                if _DETAIL_LINK_RE.search(html) or "play/" in html:
                    break
            except BlockedError:
                # 站源受限必须原样上抛：UI 只提示"该站源受限，请切换站源"，
                # 不能降级成"无法获取剧集详情页"而把真实原因抹掉。
                raise
            except Exception:
                continue
                
        if not html:
            raise ParseError("无法获取剧集详情页")

        links = _DETAIL_LINK_RE.findall(html)
        links = [row for row in links if row[0] == drama.drama_id]
        if not links:
            raise ParseError("详情页没有解析到剧集列表")
            
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
                
        is_play_format = "play/" in html and "vodplay" not in html
        url_prefix = "/play/" if is_play_format else "/vodplay/"
        
        return [
            Episode(
                number=number,
                title=f"第{number}集",
                page_url=f"{base}{url_prefix}{drama.drama_id}-{sid}-{number}.html",
                sid=sid,
            )
            for number in numbers
        ]

    # ---------- 站源数据接口（苹果CMS） ----------
    def _api_segments(self, client: HttpClient, drama: Drama) -> list[tuple[str, str]]:
        """从苹果CMS 数据接口取"分段"列表 [(段标题, 直链)]。

        返回空列表表示该站源不提供接口（如被关闭 / 返回非 JSON），此时调用方
        回退到详情页解析。接口失败不抛 BlockedError：详情页主路径仍可用，
        不能因为接口被墙就让整个播放链路报"站源受限"。
        """
        drama_id = str(drama.drama_id)
        if drama_id in self._api_cache:
            return self._api_cache[drama_id][1]
        if drama_id in self._api_miss:
            return []

        for base in self.bases:
            url = urljoin(base + "/", _API_DETAIL_PATH.format(drama_id).lstrip("/"))
            try:
                payload = client.get_json(
                    url,
                    referer=base + "/",
                    cookie=self.config.cookie or None,
                )
            except Exception as exc:  # noqa: BLE001
                # 接口不可用（Cloudflare 拦截 / 已关闭 / 非 JSON）都静默跳过，
                # 只留调试日志，避免污染用户可见的播放错误。
                logger.debug("站源 %s 数据接口不可用：%s（%s）", self.key, exc, url)
                continue
            rows = self._parse_api_segments(payload)
            if rows:
                self._api_cache[drama_id] = (base, rows)
                self._api_total[drama_id] = self._api_drama_total(payload) or len(rows)
                return rows

        self._api_miss.add(drama_id)
        return []

    @staticmethod
    def _api_drama_total(payload: object) -> int:
        """接口里的"全剧集数"：真实值在 list[0].vod_total，顶层 total 是分页条数。"""
        if not isinstance(payload, dict):
            return 0
        items = payload.get("list")
        if isinstance(items, list) and items and isinstance(items[0], dict):
            for key in ("vod_total", "vod_serial"):
                try:
                    value = int(items[0].get(key) or 0)
                except (TypeError, ValueError):
                    value = 0
                if value > 0:
                    return value
        return 0

    @staticmethod
    def _parse_api_segments(payload: object) -> list[tuple[str, str]]:
        """解析 vod_play_url：多源用 $$$ 分组，组内 # 分集、$ 分"标题/地址"。"""
        if not isinstance(payload, dict):
            return []
        items = payload.get("list")
        if not isinstance(items, list):
            return []
        for item in items:
            if not isinstance(item, dict):
                continue
            raw = str(item.get("vod_play_url") or "").split("$$$")[0]
            rows: list[tuple[str, str]] = []
            for part in raw.split("#"):
                part = part.strip()
                if not part:
                    continue
                name, _, media_url = part.partition("$")
                media_url = media_url.strip()
                if not media_url:
                    continue
                rows.append((name.strip() or f"第{len(rows) + 1}集", media_url))
            if rows:
                return rows
        return []

    def _api_episodes(self, client: HttpClient, drama: Drama) -> list[Episode]:
        rows = self._api_segments(client, drama)
        if not rows:
            return []
        self._api_mode.add(str(drama.drama_id))
        drama.total = max(
            int(drama.total or 0),
            len(rows),
            int(self._api_total.get(str(drama.drama_id), 0) or 0),
        )
        return [
            Episode(
                number=index,
                title=title,
                page_url="",
                sid=int(drama.sid or 1),
                direct_url=media_url,
            )
            for index, (title, media_url) in enumerate(rows, start=1)
        ]

    def _api_plan(self, client: HttpClient, drama: Drama, episode: int) -> MediaPlan | None:
        """按接口分段号取直链；号越界或无接口返回 None。"""
        rows = self._api_segments(client, drama)
        if not rows or not (1 <= episode <= len(rows)):
            return None
        base = self._api_cache.get(str(drama.drama_id), ("", []))[0] or (self.bases[0] if self.bases else "")
        media_url = rows[episode - 1][1]
        if media_url.startswith("//"):
            media_url = "https:" + media_url
        elif not media_url.startswith("http"):
            media_url = urljoin(base + "/", media_url.lstrip("/"))
        kind = "hls" if "m3u8" in media_url else ("mp4" if ".mp4" in media_url.lower() else "hls")
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

    # ---------- 取播放地址 ----------
    def resolve(self, client: HttpClient, drama: Drama, episode: int) -> MediaPlan:
        episode = int(episode)
        # 详情阶段已确认走接口分段的剧（站点把多集打包、详情页只列第 1 集）：
        # 直接用接口直链，既省掉播放页请求（首集更快），也避免第 2..N 段
        # 根本没有可打开的播放页而导致后台追加全部失败。
        if str(drama.drama_id) in self._api_mode:
            api_plan = self._api_plan(client, drama, episode)
            if api_plan is not None:
                return api_plan

        sid = int(drama.sid or 1)
        candidates = [sid] + [s for s in (1, 2, 3, 4, 5) if s != sid]
        last_error: Exception | None = None
        
        for attempt_sid in candidates:
            for prefix in ["/play/", "/vodplay/"]:
                path = f"{prefix}{drama.drama_id}-{attempt_sid}-{int(episode)}.html"
                try:
                    html, base = self._get_html(client, path)
                    plan = self._extract_plan(html, base, drama, int(episode))
                    if plan is not None:
                        return plan
                except BlockedError:
                    # 被 Cloudflare 质询时立即上抛：继续把 10 组候选 URL 连打一遍
                    # 只会加重风控，也拿不到地址。
                    raise
                except (NetworkError, NotFoundError, ParseError) as exc:
                    # 某些站点的 /play/ 或 /vodplay/ 会返回 200 空页面，
                    # 必须继续尝试下一个候选路径，不能直接判死。
                    last_error = exc
                    continue
                    
        # 兜底：未经详情阶段（如重启后恢复的下载任务）直接播放/下载时，
        # 播放页全部打不开可再试一次接口分段，避免多集剧只有第 1 段可用。
        api_plan = self._api_plan(client, drama, episode)
        if api_plan is not None:
            return api_plan

        if last_error is not None:
            raise last_error
        raise ParseError(f"第{episode}集播放页没有可用的视频地址")

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
            raw_url = raw_url.split(";")[0].strip()
            if raw_url.startswith("//"):
                raw_url = "https:" + raw_url
            media_url = urljoin(base + "/", raw_url)
            
            if "m3u8" in media_url or ".mp4" in media_url:
                kind = "hls" if "m3u8" in media_url else "mp4"
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
            if "/voddetail/" in html or "vodplay" in html or "/vodsearch/" in html or "/play/" in html or "/vod/" in html:
                return True, f"站点可用：{base}"
            errors.append(f"{base}: 页面可访问，但未检测到苹果CMS 特征")
        return False, "；".join(errors) or "站源不可用"


class SourceRegistry:
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
    base = base_url.strip().rstrip("/")
    if not base.startswith(("http://", "https://")):
        return False, "地址必须以 http:// 或 https:// 开头"
    try:
        html = client.get_text(base + "/", timeout=20)
    except AppError as exc:
        return False, str(exc)
    if "/voddetail/" in html or "/vodplay/" in html or "/vodsearch/" in html or "/play/" in html or "/vod/" in html:
        return True, f"站点可用：{base}"
    return False, "页面可访问，但没有检测到苹果CMS 特征"