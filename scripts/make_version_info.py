# -*- mode: python ; coding: utf-8 -*-
r"""由 duanju/version.py 生成 PyInstaller 的 Windows 版本资源文件。

用法：python scripts\make_version_info.py [输出路径]
默认输出 build\version_info.txt，duanju.spec 会自动调用本模块。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from duanju.version import APP_NAME, VERSION  # noqa: E402


def parse_version(version: str) -> tuple[int, int, int, int]:
    """把 "0.2.0" 之类的版本号补齐成四段整数，非数字部分按 0 处理。"""
    chunks = (version.split(".") + ["0", "0", "0", "0"])[:4]
    parts = []
    for chunk in chunks:
        digits = "".join(ch for ch in chunk if ch.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts)  # type: ignore[return-value]


def write_version_file(dest, version: str = VERSION, app_name: str = APP_NAME) -> Path:
    """生成 Windows 版本资源文本，返回写入路径。"""
    from PyInstaller.utils.win32.versioninfo import (
        FixedFileInfo,
        StringFileInfo,
        StringStruct,
        StringTable,
        VarFileInfo,
        VarStruct,
        VSVersionInfo,
    )

    quad = parse_version(version)
    info = VSVersionInfo(
        ffi=FixedFileInfo(
            filevers=quad,
            prodvers=quad,
            mask=0x3F,
            flags=0x0,
            OS=0x40004,
            fileType=0x1,
            subtype=0x0,
            date=(0, 0),
        ),
        kids=[
            StringFileInfo(
                [
                    StringTable(
                        "040904B0",
                        [
                            StringStruct("CompanyName", "duanju"),
                            StringStruct("FileDescription", app_name),
                            StringStruct("FileVersion", version),
                            StringStruct("InternalName", app_name),
                            StringStruct("LegalCopyright", "Copyright (c) duanju contributors"),
                            StringStruct("OriginalFilename", app_name + ".exe"),
                            StringStruct("ProductName", app_name),
                            StringStruct("ProductVersion", version),
                        ],
                    )
                ]
            ),
            VarFileInfo([VarStruct("Translation", [1033, 1200])]),
        ],
    )

    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(str(info), encoding="utf-8")
    return dest


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "build" / "version_info.txt"
    print(write_version_file(target))
