#!/usr/bin/env python3
"""从 PyPI 镜像批量下载符合当前环境的 wheel（用于离线安装 / 网络差时兜底）。

用法：
    python scripts/fetch_wheels.py --wheelhouse wheelhouse requests PySide6-Essentials
    python -m pip install --user --no-index --find-links wheelhouse -r requirements.txt
"""

from __future__ import annotations

import argparse
import re
import sys
import time
import urllib.request
from pathlib import Path
from urllib.parse import unquote, urljoin

try:
    from pip._vendor.packaging.version import InvalidVersion, parse as parse_version
except Exception:  # noqa: BLE001 - pip 不可用时退化为字符串排序
    parse_version = None
    InvalidVersion = Exception  # type: ignore[assignment]

DEFAULT_INDEX = "https://mirrors.aliyun.com/pypi/simple"
_WHEEL_RE = re.compile(
    r"^(?P<name>.+?)-(?P<version>[0-9][^-]*)-(?P<py>[^-]+)-(?P<abi>[^-]+)-(?P<plat>[^-]+)\.whl$"
)
_HREF_RE = re.compile(r'href="([^"]+)"')


def _compatible(py: str, abi: str, plat: str) -> bool:
    """判断 wheel 标签是否可用于当前解释器（Windows + CPython 3.12）。"""
    if plat not in ("any", "win_amd64", "win32", "win"):
        return False
    if py in ("py3", "py2.py3"):
        return True
    if abi == "abi3" and py.startswith("cp3"):
        return True  # 稳定 ABI：cp36-abi3 等可用于 3.12
    current = f"cp{sys.version_info.major}{sys.version_info.minor}"
    return py == current and abi in (current, "none")


def _preference(py: str, abi: str) -> int:
    current = f"cp{sys.version_info.major}{sys.version_info.minor}"
    if py == current and abi == current:
        return 3
    if abi == "abi3":
        return 2
    return 1


def _download(url: str, target: Path) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": "wheel-fetcher/1.0"})
    with urllib.request.urlopen(request, timeout=60) as response, open(target, "wb") as handle:
        total = int(response.headers.get("Content-Length") or 0)
        received = 0
        started = time.monotonic()
        while True:
            chunk = response.read(256 * 1024)
            if not chunk:
                break
            handle.write(chunk)
            received += len(chunk)
            if total:
                percent = received * 100 / total
                speed = received / max(1e-6, time.monotonic() - started) / 1024
                print(f"\r    {target.name}: {percent:5.1f}%  {speed:7.0f} KB/s", end="", flush=True)
    print()


def fetch(package: str, wheelhouse: Path, index: str) -> Path | None:
    package = package.lower().replace("_", "-")
    url = f"{index}/{package}/"
    print(f"[{package}] 读取索引 {url}")
    with urllib.request.urlopen(
        urllib.request.Request(url, headers={"User-Agent": "wheel-fetcher/1.0"}), timeout=60
    ) as response:
        html = response.read().decode("utf-8", "ignore")
    candidates: list[tuple[object, int, str, str]] = []
    for href in _HREF_RE.findall(html):
        filename = unquote(href.split("#")[0].rsplit("/", 1)[-1])
        match = _WHEEL_RE.match(filename)
        if not match:
            continue
        if not _compatible(match["py"], match["abi"], match["plat"]):
            continue
        version_text = match["version"]
        try:
            version = parse_version(version_text) if parse_version else version_text
        except InvalidVersion:
            continue
        preference = _preference(match["py"], match["abi"])
        resolved = urljoin(url, href)
        candidates.append((version, preference, filename, resolved))
    if not candidates:
        print(f"[{package}] [WARN] 没有找到兼容 wheel（可尝试非 Windows 平台或手动安装）")
        return None
    candidates.sort(key=lambda item: (item[0], item[1]))
    version, _pref, filename, resolved = candidates[-1]
    target = wheelhouse / filename
    if target.exists() and target.stat().st_size > 0:
        print(f"[{package}] 已存在：{filename}")
        return target
    print(f"[{package}] 选择 {filename}")
    _download(resolved, target)
    return target


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        pass
    parser = argparse.ArgumentParser(description="从镜像下载兼容 wheel")
    parser.add_argument("packages", nargs="+", help="包名列表，例如 PySide6-Essentials requests")
    parser.add_argument("--wheelhouse", default="wheelhouse", help="下载目录")
    parser.add_argument("--index", default=DEFAULT_INDEX, help="PyPI 镜像 simple 地址")
    args = parser.parse_args()
    wheelhouse = Path(args.wheelhouse)
    wheelhouse.mkdir(parents=True, exist_ok=True)
    failed = []
    for package in args.packages:
        try:
            if fetch(package, wheelhouse, args.index.rstrip("/")) is None:
                failed.append(package)
        except Exception as exc:  # noqa: BLE001
            print(f"[{package}] 失败：{exc}")
            failed.append(package)
    print(f"\n完成：{len(args.packages) - len(failed)}/{len(args.packages)}，目录 {wheelhouse}")
    if failed:
        print("失败：", ", ".join(failed))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
