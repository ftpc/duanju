"""Cloudflare 人机验证处理对话框（简化版：仅收集 Cookie）。

设计原则：
- 对话框只负责收集用户输入的 Cookie，绝不发送网络请求
- 解析失败时返回空列表，不抛出异常
- 用户确认后通过信号通知 UI 层，由 UI 层决定是否重试
"""

from __future__ import annotations

import logging
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QLineEdit,
    QMessageBox,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

logger = logging.getLogger(__name__)

# Cloudflare 的 cf_clearance 与 UA 绑定，浏览器与 requests 必须使用相同 UA。
# 这里固定一个 Chrome Windows UA，避免浏览器和请求头不一致导致 cookie 失效。
_STABLE_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


def parse_cookie_string(cookie_str: str) -> list[dict[str, str]]:
    """最稳定的 Cookie 解析函数。

    支持格式：
    - `name1=value1; name2=value2`
    - 包含空格的格式
    - 值中包含等号的情况

    解析失败时返回空列表，绝不抛出异常。
    """
    result: list[dict[str, str]] = []
    if not cookie_str:
        return result
    try:
        for item in cookie_str.split(";"):
            item = item.strip()
            if not item:
                continue
            if "=" in item:
                name, value = item.split("=", 1)
                result.append({"name": name.strip(), "value": value.strip()})
            else:
                # 没有等号也保存，防止数据丢失
                result.append({"name": item, "value": ""})
    except Exception as exc:  # noqa: BLE001 - 防止任何异常
        logger.warning("Cookie 解析异常：%s", exc)
    return result


class CfChallengeDialog(QDialog):
    """人机验证处理对话框。

    用户需在系统浏览器中打开指定 URL 完成验证，然后将 Cookie 粘贴到此对话框。
    对话框关闭后立即返回结果，不执行任何网络操作。
    """

    def __init__(self, url: str, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("人机验证")
        self.setFixedSize(620, 380)
        self._url = url.strip()
        self._cookies: list[dict[str, str]] = []
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        # 说明文字
        info = QLabel(
            "当前环境未启用内嵌浏览器。请在系统浏览器中打开下方地址完成验证，"
            "然后将浏览器的 Cookie 和 User-Agent 粘贴到下方输入框。\n\n"
            "获取方法：在浏览器控制台输入 document.cookie 和 navigator.userAgent"
        )
        info.setWordWrap(True)
        layout.addWidget(info)

        # URL 展示区（可复制）
        url_label = QLabel("验证地址：")
        url_label.setObjectName("Muted")
        layout.addWidget(url_label)
        self._url_display = QLabel(self._url)
        self._url_display.setTextInteractionFlags(
            Qt.TextSelectableByMouse | Qt.TextSelectableByKeyboard
        )
        self._url_display.setWordWrap(True)
        layout.addWidget(self._url_display)

        # Cookie 输入区（多行文本框）
        cookie_label = QLabel("Cookie（粘贴自浏览器）：")
        cookie_label.setObjectName("Muted")
        layout.addWidget(cookie_label)
        self._cookie_edit = QTextEdit()
        self._cookie_edit.setMaximumHeight(120)
        self._cookie_edit.setPlaceholderText(
            "粘贴 Cookie 字符串，例如：cf_clearance=xxx; session=yyy"
        )
        layout.addWidget(self._cookie_edit)

        # UA 输入区（单行文本框）
        ua_label = QLabel("User-Agent（粘贴自浏览器）：")
        ua_label.setObjectName("Muted")
        layout.addWidget(ua_label)
        self._ua_input = QLineEdit()
        self._ua_input.setPlaceholderText(
            "请粘贴浏览器的 User-Agent（在浏览器控制台输入 navigator.userAgent 获取）"
        )
        layout.addWidget(self._ua_input)

        # 按钮区
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setDefault(True)
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _on_accept(self) -> None:
        """用户点击 OK 时解析 Cookie。"""
        raw = self._cookie_edit.toPlainText().strip()
        if not raw:
            QMessageBox.warning(self, "提示", "请先粘贴 Cookie 内容")
            return

        # 使用最稳定的解析函数
        self._cookies = parse_cookie_string(raw)
        if not self._cookies:
            QMessageBox.warning(
                self,
                "格式错误",
                "无法解析 Cookie，请检查格式是否正确（例如：name=value; name2=value2）",
            )
            return

        logger.info("解析到 %d 个 cookie", len(self._cookies))
        self.accept()

    def get_cookies(self) -> list[dict[str, str]]:
        """返回解析后的 Cookie 列表。"""
        return list(self._cookies)

    def get_user_agent(self) -> str:
        """返回用户输入的 UA，如果没有填写则使用默认 UA。

        注意：必须与浏览器实际使用的 UA 一致，否则 cf_clearance 会失效。
        """
        ua = self._ua_input.text().strip()
        if ua:
            logger.info("使用用户提供的 UA: %s", ua[:50] + "..." if len(ua) > 50 else ua)
            return ua
        logger.warning("未提供 UA，使用默认值")
        return _STABLE_USER_AGENT
