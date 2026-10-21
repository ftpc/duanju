"""搜索三态（empty / blocked / error）单元测试。

覆盖三层：
1. ``duanju.search_state`` 文案模板与状态归一；
2. ``Service.search`` 的 (dramas, state, reason) 三态返回；
3. ``MacCmsSource.search`` 的「suggest 空即空结果、不回退 HTML」「HTML 403/404 按无结果」；
4. ``HttpClient._raise_for_status`` 的 BlockedError.code 判别；
5. UI 侧静态校验：搜索链路已无验证弹框残留。
"""

from __future__ import annotations

import unittest
from pathlib import Path

from duanju.config import SourceConfig
from duanju.errors import BlockedError, NetworkError, NotFoundError
from duanju.models import Drama
from duanju.net import HttpClient
from duanju.search_state import (
    BLOCKED_HINT,
    STATE_BLOCKED,
    STATE_EMPTY,
    STATE_ERROR,
    STATE_OK,
    format_state,
    is_blocked_message,
    normalize_state,
)
from duanju.service import Service
from duanju.sources import MacCmsSource

ROOT = Path(__file__).resolve().parent.parent


class FormatStateTests(unittest.TestCase):
    def test_three_templates(self) -> None:
        self.assertEqual(
            format_state(STATE_EMPTY, "总裁", "红果"),
            "未搜到「总裁」相关剧集 · 来源：红果 · 可换关键词或切换站源",
        )
        self.assertEqual(
            format_state(STATE_BLOCKED, "总裁", "红果"),
            "「红果」搜索通道被站点拦截，无法确认「总裁」是否存在 · 请切换站源",
        )
        self.assertEqual(
            format_state(STATE_ERROR, "总裁", "红果"),
            "「红果」暂时无法访问，搜索未完成 · 请稍后重试或切换站源",
        )

    def test_unknown_state_falls_back_to_error(self) -> None:
        self.assertEqual(
            format_state("weird", "q", "s"),
            format_state(STATE_ERROR, "q", "s"),
        )
        self.assertEqual(normalize_state("weird"), STATE_ERROR)
        self.assertEqual(normalize_state(STATE_OK), STATE_OK)

    def test_blocked_message_detection(self) -> None:
        self.assertTrue(is_blocked_message(f"{BLOCKED_HINT}（站点返回 HTTP 403）"))
        self.assertFalse(is_blocked_message("HTTP 500 服务器错误"))


class _SourceStub:
    def __init__(self, result=None, error: Exception | None = None):
        self.result = result or []
        self.error = error
        self.queries: list[str] = []

    def search(self, _client, query: str):
        self.queries.append(query)
        if self.error is not None:
            raise self.error
        return self.result


class _RegistryStub:
    def __init__(self, source):
        self.source = source

    def get(self, key: str):
        return self.source if key == "custom1" else None


class _ServiceStub:
    def __init__(self, source):
        self.registry = _RegistryStub(source)
        self.client = object()


class ServiceSearchStateTests(unittest.TestCase):
    def _run(self, source, query: str = "总裁"):
        return Service.search(_ServiceStub(source), "custom1", query)

    def test_ok_state(self) -> None:
        drama = Drama(source_key="custom1", source_name="站源", drama_id="1", title="总裁")
        dramas, state, reason = self._run(_SourceStub([drama]))
        self.assertEqual(state, STATE_OK)
        self.assertEqual(len(dramas), 1)
        self.assertEqual(reason, "")

    def test_empty_state(self) -> None:
        dramas, state, _ = self._run(_SourceStub([]))
        self.assertEqual((dramas, state), ([], STATE_EMPTY))

    def test_blocked_state_keeps_code_reason(self) -> None:
        exc = BlockedError(f"{BLOCKED_HINT}（站点启用了人机验证，code=challenge）", code="challenge")
        dramas, state, reason = self._run(_SourceStub(error=exc))
        self.assertEqual((dramas, state), ([], STATE_BLOCKED))
        self.assertIn(BLOCKED_HINT, reason)

    def test_error_state_on_network_failure(self) -> None:
        dramas, state, reason = self._run(_SourceStub(error=NetworkError("连接超时")))
        self.assertEqual((dramas, state), ([], STATE_ERROR))
        self.assertIn("连接超时", reason)

    def test_unknown_source_is_error(self) -> None:
        dramas, state, _ = Service.search(_ServiceStub(_SourceStub([])), "missing", "总裁")
        self.assertEqual((dramas, state), ([], STATE_ERROR))

    def test_blank_query_is_empty(self) -> None:
        source = _SourceStub([])
        dramas, state, _ = self._run(source, "   ")
        self.assertEqual((dramas, state), ([], STATE_EMPTY))
        self.assertEqual(source.queries, [])

    def test_retry_with_challenge_removed(self) -> None:
        self.assertFalse(hasattr(Service, "retry_search_with_challenge"))


class _SuggestClient:
    """按关键字模拟 suggest / HTML 搜索页的两种返回。"""

    def __init__(self, json_result=None, json_error=None, text_result="", text_error=None):
        self.json_result = json_result
        self.json_error = json_error
        self.text_result = text_result
        self.text_error = text_error
        self.text_calls: list[str] = []

    def get_json(self, url: str, **_kwargs):
        if self.json_error is not None:
            raise self.json_error
        return self.json_result

    def get_text(self, url: str, **_kwargs):
        self.text_calls.append(url)
        if self.text_error is not None:
            raise self.text_error
        return self.text_result


class SourceSearchFlowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.source = MacCmsSource(
            SourceConfig(key="test", name="测试站", bases=["https://example.test"])
        )

    def test_suggest_empty_skips_html_fallback(self) -> None:
        client = _SuggestClient(json_result={"list": []}, text_result="<html>任意</html>")
        self.assertEqual(self.source.search(client, "不存在"), [])
        self.assertEqual(client.text_calls, [])

    def test_suggest_malformed_returns_empty(self) -> None:
        client = _SuggestClient(json_result={"unexpected": 1})
        self.assertEqual(self.source.search(client, "x"), [])
        self.assertEqual(client.text_calls, [])

    def test_suggest_blocked_propagates(self) -> None:
        exc = BlockedError(f"{BLOCKED_HINT}（HTTP 403）", code="forbidden")
        client = _SuggestClient(json_error=exc)
        with self.assertRaises(BlockedError) as ctx:
            self.source.search(client, "总裁")
        self.assertEqual(ctx.exception.code, "forbidden")

    def test_html_search_404_counts_as_empty(self) -> None:
        client = _SuggestClient(
            json_error=NetworkError("接口不可用"),
            text_error=NotFoundError("页面不存在"),
        )
        self.assertEqual(self.source.search(client, "总裁"), [])

    def test_html_search_403_counts_as_empty(self) -> None:
        client = _SuggestClient(
            json_error=NetworkError("接口不可用"),
            text_error=BlockedError(f"{BLOCKED_HINT}（HTTP 403）", code="forbidden"),
        )
        self.assertEqual(self.source.search(client, "总裁"), [])

    def test_html_search_success_still_works(self) -> None:
        html = '<a href="/voddetail/15297.html" title="示例短剧">示例短剧</a>'
        client = _SuggestClient(json_error=NetworkError("接口不可用"), text_result=html)
        dramas = self.source.search(client, "示例")
        self.assertEqual([d.drama_id for d in dramas], ["15297"])


class _Response:
    def __init__(self, status: int, body: str = "", headers: dict | None = None):
        self.status_code = status
        self.text = body
        self.headers = headers or {}


class RaiseForStatusTests(unittest.TestCase):
    def test_challenge_blocked_has_code(self) -> None:
        with self.assertRaises(BlockedError) as ctx:
            HttpClient._raise_for_status(_Response(403, "<title>Just a moment...</title>"))
        self.assertEqual(ctx.exception.code, "challenge")

    def test_plain_403_forbidden_has_code(self) -> None:
        with self.assertRaises(BlockedError) as ctx:
            HttpClient._raise_for_status(_Response(403, "forbidden"))
        self.assertEqual(ctx.exception.code, "forbidden")
        self.assertIn(BLOCKED_HINT, str(ctx.exception))

    def test_404_is_not_found(self) -> None:
        with self.assertRaises(NotFoundError):
            HttpClient._raise_for_status(_Response(404))

    def test_429_without_challenge_is_network_error(self) -> None:
        with self.assertRaises(NetworkError):
            HttpClient._raise_for_status(_Response(429))


class NoChallengeDialogInSearchTests(unittest.TestCase):
    """静态校验：搜索链路不再引用人机验证弹框。"""

    def _read(self, relative: str) -> str:
        return (ROOT / relative).read_text(encoding="utf-8")

    def test_search_page_has_no_challenge_dialog(self) -> None:
        text = self._read("ui/search_page.py")
        for token in (
            "challenge_result_received",
            "_show_challenge_dialog",
            "CfChallengeDialog",
            "_on_search_ok",
        ):
            self.assertNotIn(token, text)

    def test_main_window_has_no_challenge_handler(self) -> None:
        text = self._read("ui/main_window.py")
        self.assertNotIn("_on_challenge_result", text)
        self.assertNotIn("challenge_result_received", text)

    def test_service_has_no_challenge_retry(self) -> None:
        text = self._read("duanju/service.py")
        for token in (
            "retry_search_with_challenge",
            "detail_with_challenge",
            "resolve_with_challenge",
        ):
            self.assertNotIn(token, text)

    def test_cf_challenge_dialog_file_kept(self) -> None:
        self.assertTrue((ROOT / "ui" / "cf_challenge_dialog.py").exists())


if __name__ == "__main__":
    unittest.main()
