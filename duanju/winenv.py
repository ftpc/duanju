"""Windows 运行环境探测：进程完整性级别。

某些受限目录（沙箱工作区、低完整性目录）里启动的 exe 会以 Low Integrity 运行，
此时无法写入 %APPDATA% 这类 Medium 完整性目录，表现为 WinError 5。
本模块用于启动时识别并给出可读提示。
"""

from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes

_TOKEN_QUERY = 0x0008
_TOKEN_INTEGRITY_LEVEL = 25

_LEVELS = {
    0x0000: "untrusted",
    0x1000: "low",
    0x2000: "medium",
    0x2100: "medium-plus",
    0x3000: "high",
    0x4000: "system",
}


class _SID_AND_ATTRIBUTES(ctypes.Structure):
    _fields_ = [("Sid", ctypes.c_void_p), ("Attributes", wintypes.DWORD)]


class _TOKEN_MANDATORY_LABEL(ctypes.Structure):
    _fields_ = [("Label", _SID_AND_ATTRIBUTES)]


def integrity_level() -> str:
    """返回当前进程完整性级别：untrusted/low/medium/medium-plus/high/system/unknown。"""
    if sys.platform != "win32":
        return "unknown"
    try:
        advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        advapi32.OpenProcessToken.restype = wintypes.BOOL
        advapi32.OpenProcessToken.argtypes = [
            wintypes.HANDLE,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.HANDLE),
        ]
        advapi32.GetTokenInformation.restype = wintypes.BOOL
        advapi32.GetTokenInformation.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
        ]
        advapi32.GetSidSubAuthorityCount.restype = ctypes.POINTER(ctypes.c_ubyte)
        advapi32.GetSidSubAuthorityCount.argtypes = [ctypes.c_void_p]
        advapi32.GetSidSubAuthority.restype = ctypes.POINTER(wintypes.DWORD)
        advapi32.GetSidSubAuthority.argtypes = [ctypes.c_void_p, wintypes.DWORD]
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

        token = wintypes.HANDLE()
        if not advapi32.OpenProcessToken(
            kernel32.GetCurrentProcess(), _TOKEN_QUERY, ctypes.byref(token)
        ):
            return "unknown"
        try:
            size = wintypes.DWORD()
            advapi32.GetTokenInformation(
                token, _TOKEN_INTEGRITY_LEVEL, None, 0, ctypes.byref(size)
            )
            if size.value <= 0:
                return "unknown"
            buffer = ctypes.create_string_buffer(size.value)
            if not advapi32.GetTokenInformation(
                token, _TOKEN_INTEGRITY_LEVEL, buffer, size, ctypes.byref(size)
            ):
                return "unknown"
            label = ctypes.cast(buffer, ctypes.POINTER(_TOKEN_MANDATORY_LABEL)).contents
            sid = label.Label.Sid
            count = advapi32.GetSidSubAuthorityCount(sid)[0]
            rid = advapi32.GetSidSubAuthority(sid, count - 1)[0]
            return _LEVELS.get(int(rid), f"0x{int(rid):x}")
        finally:
            kernel32.CloseHandle(token)
    except Exception:  # noqa: BLE001 - 探测失败不应影响启动
        return "unknown"


def is_restricted() -> bool:
    return integrity_level() in ("untrusted", "low")
