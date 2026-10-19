"""解析层单元测试：搜索 / 详情 / 播放页 → m3u8。"""

from __future__ import annotations

import unittest

from duanju.config import SourceConfig
from duanju.errors import NetworkError
from duanju.sources import MacCmsSource

SEARCH_JSON = {
    "code": 1,
    "msg": "ok",
    "total": 2,
    "list": [
        {"id": 15297, "name": "示例短剧 A", "pic": "/upload/x.png"},
        {"id": 11792, "name": "示例短剧 B", "pic": "https://x.test/y.jpg"},
        {"id": 15297, "name": "重复条目"},
    ],
}

SEARCH_HTML = """
<html><body>
<a href="/voddetail/15297.html" title="示例短剧 A">示例短剧</a>
<a href="/voddetail/11792.html" title="示例短剧 B">初恋</a>
<a href="/voddetail/15297.html" title="重复">重复</a>
</body></html>
"""

DETAIL_HTML = """
<html><head><title>示例短剧 A-短剧-在线观看</title></head><body>
<a href="/vodplay/15297-1-1.html">第1集</a>
<a href="/vodplay/15297-1-2.html">第2集</a>
<a href="/vodplay/15297-1-3.html">第3集</a>
<a href="/vodplay/15297-2-1.html">第1集(备用源)</a>
<a href="/vodplay/999-1-1.html">别的剧</a>
</body></html>
"""

PLAYER_HTML = r"""
<html><body>
<script type="text/javascript">var player_aaaa={"flag":"play","encrypt":0,
"vod_data":{"vod_name":"\u5bb4\u5f8b\uff0c\u4f60\u7684\u767d\u6708\u5149\u56de\u56fd\u4e86"},
"url":"https:\/\/v.fengbao8.com\/video\/yanlv\/\u7b2c1\u96c6\/index.m3u8","from":"bfzym3u8"}</script>
</body></html>
"""

PLAYER_MULTI_HTML = r"""
<script>var player_bbbb={"flag":"play","url":"https://cdn.test/a/720/index.m3u8;https://cdn.test/a/1080/index.m3u8"};</script>
"""


class FakeClient:
    """按 URL 片段返回预置内容，模拟站点的三种接口。"""

    def __init__(self):
        self.calls: list[str] = []
        self.search_html_fallback = False

    def get_json(self, url: str, **_kwargs):
        self.calls.append(url)
        if "ajax/suggest" in url:
            if self.search_html_fallback:
                raise NetworkError("模拟接口不可用")
            return SEARCH_JSON
        raise NetworkError(f"未知接口：{url}")

    def get_text(self, url: str, **_kwargs):
        self.calls.append(url)
        if "vodsearch" in url:
            return SEARCH_HTML
        if "voddetail" in url:
            return DETAIL_HTML
        if "vodplay" in url:
            return PLAYER_HTML
        if url.endswith(".m3u8"):
            raise NetworkError("测试中不请求真实媒体")
        return ""

    def get(self, *_args, **_kwargs):  # pragma: no cover - 未使用
        raise AssertionError("不期望直接调用 get")


class SourceParseTests(unittest.TestCase):
    def setUp(self) -> None:
        config = SourceConfig(key="test", name="测试站", bases=["https://example.test"])
        self.source = MacCmsSource(config)
        self.client = FakeClient()

    def test_search_uses_json_api_and_dedupes(self) -> None:
        dramas = self.source.search(self.client, "示例短剧")
        self.assertEqual([d.drama_id for d in dramas], ["15297", "11792"])
        self.assertEqual(dramas[0].title, "示例短剧 A")
        self.assertEqual(dramas[0].source_key, "test")
        self.assertTrue(dramas[0].cover.startswith("https://example.test/"))

    def test_search_falls_back_to_html(self) -> None:
        self.client.search_html_fallback = True
        dramas = self.source.search(self.client, "示例短剧")
        self.assertEqual([d.drama_id for d in dramas], ["15297", "11792"])

    def test_detail_picks_dominant_sid_and_sorts(self) -> None:
        from duanju.models import Drama

        drama = Drama(source_key="test", source_name="测试站", drama_id="15297", title="示例短剧")
        episodes = self.source.detail(self.client, drama)
        self.assertEqual([e.number for e in episodes], [1, 2, 3])
        self.assertEqual(drama.total, 3)
        self.assertEqual(drama.sid, 1)
        self.assertEqual(episodes[0].page_url, "https://example.test/vodplay/15297-1-1.html")

    def test_resolve_extracts_m3u8(self) -> None:
        from duanju.models import Drama

        drama = Drama(source_key="test", source_name="测试站", drama_id="15297", title="示例短剧", sid=1)
        plan = self.source.resolve(self.client, drama, 1)
        self.assertEqual(plan.kind, "hls")
        self.assertIn("index.m3u8", plan.url)
        self.assertIn("v.fengbao8.com", plan.url)
        self.assertEqual(plan.referer, "https://example.test/")

    def test_resolve_splits_multiple_urls(self) -> None:
        from duanju.models import Drama

        class MultiClient(FakeClient):
            def get_text(self, url: str, **kwargs):
                if "vodplay" in url:
                    return PLAYER_MULTI_HTML
                return super().get_text(url, **kwargs)

        drama = Drama(source_key="test", source_name="测试站", drama_id="1", title="x", sid=1)
        plan = self.source.resolve(MultiClient(), drama, 1)
        self.assertEqual(plan.url, "https://cdn.test/a/720/index.m3u8")


if __name__ == "__main__":
    unittest.main()
