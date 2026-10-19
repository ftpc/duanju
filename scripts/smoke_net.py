#!/usr/bin/env python3
"""网络冒烟：真实访问站源，验证 搜索 → 详情 → 取流 三步（不下载视频）。

用法：
    python scripts/smoke_net.py
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
os.environ["APPDATA"] = tempfile.mkdtemp(prefix="duanju-smoke-net-")

from duanju.config import Config  # noqa: E402
from duanju.service import Service  # noqa: E402


def main() -> int:
    config = Config.load()
    service = Service(config)
    try:
        sources = service.sources()
        print("站源：", sources)
        if not sources:
            print("没有可用站源")
            return 1
        key, name = sources[0]
        print(f"使用站源：{name}（{key}）")

        print("\n[1/3] 搜索「夫人」…")
        dramas = service.search(key, "夫人")
        if not dramas:
            print("搜索没有结果")
            return 1
        for drama in dramas[:5]:
            print(f"  - id={drama.drama_id:>6}  {drama.title}")
        target = dramas[0]

        print(f"\n[2/3] 读取《{target.title}》分集…")
        episodes = service.detail(target)
        print(f"  共 {len(episodes)} 集，首集 {episodes[0].number}，末集 {episodes[-1].number}")
        print(f"  total={target.total} sid={target.sid}")

        print(f"\n[3/3] 解析第 {episodes[0].number} 集播放地址…")
        plan = service.resolve(target, episodes[0].number)
        print(f"  kind={plan.kind}")
        print(f"  url={plan.url[:110]}…")
        if plan.kind == "hls" and service.ffmpeg.available:
            try:
                duration = service.ffmpeg.hls_duration(plan, service.client)
                print(f"  播放列表时长：{duration:.1f} 秒")
            except Exception as exc:  # noqa: BLE001
                print(f"  读取播放列表时长失败（不影响下载）：{exc}")
        print("\n网络冒烟通过 [OK]")
        return 0
    finally:
        service.db.close()
        service.client.close()


if __name__ == "__main__":
    raise SystemExit(main())
