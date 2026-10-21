"""搜索三态（ok / empty / blocked / error）的统一定义与文案。

设计约定：

* 句式统一为「结果 + 来源 + 下一步」，由 :func:`format_state` 生成；
* ``empty`` / ``blocked`` / ``error`` 三态只更新界面文案，不弹窗、不弹验证框；
* 本模块不依赖 Qt，可被 service 层与测试直接复用。
"""

from __future__ import annotations

STATE_OK = "ok"
STATE_EMPTY = "empty"
STATE_BLOCKED = "blocked"
STATE_ERROR = "error"

# 三态模板：键为状态，值为「结果 + 来源 + 下一步」句式
_TEMPLATES: dict[str, str] = {
    STATE_EMPTY: "未搜到「{query}」相关剧集 · 来源：{source} · 可换关键词或切换站源",
    STATE_BLOCKED: "「{source}」搜索通道被站点拦截，无法确认「{query}」是否存在 · 请切换站源",
    STATE_ERROR: "「{source}」暂时无法访问，搜索未完成 · 请稍后重试或切换站源",
}

# 站源受限的固定提示：详情 / 在线播放遇 403 时替代原 Cookie 验证框
BLOCKED_HINT = "该站源受限，请切换站源"


def format_state(state: str, query: str = "", source: str = "") -> str:
    """按三态模板生成提示文案；未知状态按 error 处理。"""
    template = _TEMPLATES.get(state, _TEMPLATES[STATE_ERROR])
    return template.format(query=(query or "").strip(), source=source or "当前站源")


def normalize_state(state: str) -> str:
    """把未知状态归一到三态之一（ok 原样保留）。"""
    if state == STATE_OK:
        return STATE_OK
    return state if state in _TEMPLATES else STATE_ERROR


def is_blocked_message(message: str) -> bool:
    """判断错误文本是否来自站源受限（BlockedError）。

    bridge 只透传异常文本，这里用固定提示词做判定，
    避免 UI 再依赖异常类型或散落的 "Cloudflare" 关键字。
    """
    text = message or ""
    return BLOCKED_HINT in text or "人机验证" in text or "challenge-platform" in text
