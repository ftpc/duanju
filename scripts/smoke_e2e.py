#!/usr/bin/env python3
"""端到端冒烟：本地假站源 + 真 ffmpeg + 真任务队列 + 合并。

覆盖链路：
    搜索(JSON) → 详情(HTML) → 播放页(player JSON) → 任务队列并发下载
    → SQLite 持久化 → 整部合并 → 时长校验

全程离线，不访问任何真实站源。用法：
    python scripts/smoke_e2e.py
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass
os.environ["APPDATA"] = tempfile.mkdtemp(prefix="duanju-smoke-e2e-")

from duanju.config import Config, SourceConfig  # noqa: E402
from duanju.service import Service  # noqa: E402
from duanju.util import human_size  # noqa: E402

DRAMA_ID = "1"
EPISODES = (1, 2)


def make_media(server_root: Path, ffmpeg: str) -> None:
    for episode in EPISODES:
        target = server_root / "media" / f"ep{episode}"
        target.mkdir(parents=True, exist_ok=True)
        result = subprocess.run(
            [
                ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
                "-f", "lavfi", "-i", "testsrc=size=320x240:rate=25",
                "-t", "6", "-c:v", "libx264", "-preset", "ultrafast", "-g", "25",
                "-f", "hls", "-hls_time", "2", "-hls_list_size", "0",
                "-hls_segment_filename", str(target / "seg%d.ts"),
                str(target / "index.m3u8"),
            ],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr[-800:])


class FakeSiteHandler(SimpleHTTPRequestHandler):
    base_url = ""

    def log_message(self, *_args):  # noqa: ANN002 - 静默访问日志
        pass

    def _send(self, body: str, content_type: str = "text/html; charset=utf-8") -> None:
        data = body.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):  # noqa: N802
        path = urlparse(self.path).path
        if path.startswith("/index.php/ajax/suggest"):
            payload = {
                "code": 1,
                "total": 1,
                "list": [{"id": int(DRAMA_ID), "name": "本地测试剧", "pic": "/cover.jpg"}],
            }
            self._send(json.dumps(payload, ensure_ascii=False), "application/json; charset=utf-8")
            return
        if path == f"/voddetail/{DRAMA_ID}.html":
            links = "".join(
                f'<a href="/vodplay/{DRAMA_ID}-1-{number}.html">第{number}集</a>'
                for number in EPISODES
            )
            self._send(f"<html><body>{links}</body></html>")
            return
        if path.startswith(f"/vodplay/{DRAMA_ID}-1-"):
            episode = int(path.rsplit("-", 1)[-1].split(".")[0])
            payload = {
                "flag": "play",
                "encrypt": 0,
                "vod_data": {"vod_name": "本地测试剧"},
                "url": f"{self.base_url}media/ep{episode}/index.m3u8",
            }
            body = (
                '<html><body><script type="text/javascript">'
                f"var player_aaaa={json.dumps(payload, ensure_ascii=False)}"
                "</script></body></html>"
            )
            self._send(body)
            return
        super().do_GET()


def wait_until(predicate, timeout: float = 120.0, interval: float = 0.3) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


def main() -> int:
    config = Config.load()
    if not config.resolved_ffmpeg():
        print("未找到 ffmpeg，无法执行端到端测试")
        return 1

    work = Path(tempfile.mkdtemp(prefix="duanju-e2e-site-"))
    download_dir = Path(tempfile.mkdtemp(prefix="duanju-e2e-dl-"))
    server = None
    service = None
    try:
        print("[准备] 生成本地测试 HLS…")
        make_media(work, config.resolved_ffmpeg())

        handler = partial(FakeSiteHandler, directory=str(work))
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        base = f"http://127.0.0.1:{server.server_address[1]}/"
        FakeSiteHandler.base_url = base
        threading.Thread(target=server.serve_forever, daemon=True).start()
        print("本地假站：", base)

        config.download_dir = str(download_dir)
        config.concurrency = 2
        config.retries = 1
        config.sources = [SourceConfig(key="local", name="本地测试", bases=[base])]

        service = Service(config)
        service.start()
        source_key = service.sources()[0][0]

        print("\n[1/5] 搜索…")
        dramas = service.search(source_key, "测试")
        assert dramas and dramas[0].drama_id == DRAMA_ID, dramas
        drama = dramas[0]
        print(f"  {drama.title}（{drama.drama_id}）")

        print("\n[2/5] 详情 / 分集…")
        episodes = service.detail(drama)
        assert [episode.number for episode in episodes] == list(EPISODES), episodes
        print(f"  {drama.title} 共 {len(episodes)} 集，sid={drama.sid}")

        print("\n[3/5] 入队下载…")
        added = service.enqueue(drama, [episode.number for episode in episodes])
        print(f"  新增 {added} 个任务")
        assert added == len(EPISODES)

        assert wait_until(
            lambda: all(job["state"] == "completed" for job in service.jobs_snapshot()),
            timeout=120,
        ), [job for job in service.jobs_snapshot()]
        jobs = service.jobs_snapshot()
        for job in jobs:
            path = Path(job["file"])
            info = service.ffmpeg.probe(path)
            print(f"  第{job['episode']}集 -> {path.name}  {human_size(path.stat().st_size)}  时长 {info.get('duration', 0):.1f}s")
            assert path.is_file() and path.stat().st_size > 10_000
            assert (info.get("duration") or 0) > 4

        print("\n[4/5] 整部合并…")
        merged = service.merge_drama(drama.key, lambda ratio, done, speed: None, threading.Event())
        merged_path = Path(merged["file"])
        merged_info = service.ffmpeg.probe(merged_path)
        print(f"  {merged_path.name}  {human_size(merged_path.stat().st_size)}  时长 {merged_info.get('duration', 0):.1f}s")
        assert 10.5 <= (merged_info.get("duration") or 0) <= 13.5, merged_info

        print("\n[5/5] 重启恢复（模拟上次未完成任务）…")
        interrupted = service.jobs_snapshot()[0]
        service.db.update_job(interrupted["id"], state="downloading")
        service.db.reset_interrupted()
        restored = service.db.job(interrupted["id"])
        assert restored and restored.state == "paused"
        print("  未完成任务已恢复为暂停状态")

        print("\n端到端冒烟通过 [OK]")
        return 0
    finally:
        if service is not None:
            service.shutdown()
        if server is not None:
            server.shutdown()
        shutil.rmtree(work, ignore_errors=True)
        shutil.rmtree(download_dir, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
