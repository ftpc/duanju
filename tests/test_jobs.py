"""任务队列状态机测试：用假站源 / 假 ffmpeg，不联网。"""

from __future__ import annotations

import tempfile
import threading
import time
import unittest
from pathlib import Path

from duanju.config import Config
from duanju.db import Database
from duanju.errors import CancelledError, ParseError
from duanju.jobs import JobManager
from duanju.models import Drama, MediaPlan


class FakeSource:
    def __init__(self, fail: bool = False):
        self.fail = fail

    def resolve(self, _client, _drama, _episode):
        if self.fail:
            raise ParseError("播放页没有可用的视频地址")
        return MediaPlan(url="https://example.test/index.m3u8", kind="hls")


class FakeRegistry:
    def __init__(self, source):
        self.source = source

    def get(self, _key):
        return self.source


class FakeFFmpeg:
    binary = "ffmpeg"
    available = True
    probe_binary = ""

    def __init__(self, delay: float = 0.01):
        self.delay = delay

    def download(self, _plan, target, on_progress, cancel_event, _client):
        target = Path(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        for index in range(6):
            if cancel_event.is_set():
                raise CancelledError("任务已暂停")
            time.sleep(self.delay)
            on_progress((index + 1) / 6, (index + 1) * 1000, 1000.0)
        target.write_bytes(b"fake-mp4" * 100)

    def probe(self, _path):
        return {"duration": 200.0, "video": "h264", "audio": "aac"}

    def version(self):
        return "fake-ffmpeg"


def wait_until(predicate, timeout: float = 10.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return False


class JobFlowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.config = Config()
        self.config.download_dir = self.temp.name
        self.config.concurrency = 2
        self.config.retries = 0
        self.db = Database(Path(self.temp.name) / "jobs.db")
        self.drama = Drama(
            source_key="test",
            source_name="测试站",
            drama_id="1",
            title="测试剧",
            total=2,
            sid=1,
        )
        self.db.upsert_drama(self.drama)
        self.events: list[dict] = []
        self.snapshots: list[list[dict]] = []

    def _make_manager(self, source=None, ffmpeg=None):
        manager = JobManager(
            self.config,
            self.db,
            client=None,  # 假站源不使用
            ffmpeg=ffmpeg or FakeFFmpeg(),
            registry=FakeRegistry(source or FakeSource()),
            job_callback=self.events.append,
            jobs_callback=self.snapshots.append,
        )
        manager.start()
        self.addCleanup(manager.stop)
        return manager

    def tearDown(self) -> None:
        self.db.close()
        self.temp.cleanup()

    def test_enqueue_and_complete(self) -> None:
        manager = self._make_manager()
        added = manager.enqueue(self.drama, [1, 2])
        self.assertEqual(added, 2)
        self.assertTrue(wait_until(lambda: all(j.state == "completed" for j in self.db.jobs())))
        jobs = self.db.jobs()
        self.assertEqual(len(jobs), 2)
        for job in jobs:
            self.assertTrue(Path(job.file).is_file())
            self.assertEqual(job.state, "completed")
        self.assertTrue(any(event["state"] == "completed" for event in self.events))

    def test_duplicate_enqueue_is_ignored(self) -> None:
        manager = self._make_manager()
        self.assertEqual(manager.enqueue(self.drama, [1, 1, 2]), 2)
        self.assertEqual(manager.enqueue(self.drama, [1, 2]), 0)
        wait_until(lambda: all(j.state == "completed" for j in self.db.jobs()))

    def test_remove_deletes_file_and_record(self) -> None:
        manager = self._make_manager()
        manager.enqueue(self.drama, [1])
        self.assertTrue(wait_until(lambda: self.db.jobs() and self.db.jobs()[0].state == "completed"))
        job = self.db.jobs()[0]
        path = Path(job.file)
        self.assertTrue(path.is_file())
        manager.remove(job.id)
        self.assertTrue(wait_until(lambda: self.db.job(job.id) is None))
        self.assertFalse(path.exists())

    def test_failure_marks_failed_then_resume(self) -> None:
        manager = self._make_manager(source=FakeSource(fail=True))
        manager.enqueue(self.drama, [1])
        self.assertTrue(wait_until(lambda: self.db.jobs() and self.db.jobs()[0].state == "failed"))
        job = self.db.jobs()[0]
        self.assertIn("没有可用的视频地址", job.error)
        # 换成可成功的站源后继续
        manager.registry = FakeRegistry(FakeSource())
        manager.resume(job.id)
        self.assertTrue(wait_until(lambda: self.db.job(job.id).state == "completed"))

    def test_pause_while_downloading(self) -> None:
        manager = self._make_manager(ffmpeg=FakeFFmpeg(delay=0.25))
        manager.enqueue(self.drama, [1])
        self.assertTrue(wait_until(lambda: self.db.jobs() and self.db.jobs()[0].state == "downloading"))
        manager.pause(self.db.jobs()[0].id)
        self.assertTrue(wait_until(lambda: self.db.job(self.db.jobs()[0].id).state == "paused"))
        # 继续后应能完成
        manager.resume(self.db.jobs()[0].id)
        self.assertTrue(wait_until(lambda: self.db.job(self.db.jobs()[0].id).state == "completed", 15))


if __name__ == "__main__":
    unittest.main()
