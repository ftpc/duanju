"""持久化与工具函数单元测试。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from duanju.db import Database
from duanju.models import Drama, Job
from duanju.util import (
    episode_filename,
    human_size,
    sanitize_filename,
    short_id,
)


class UtilTests(unittest.TestCase):
    def test_sanitize_windows_illegal_chars(self) -> None:
        self.assertEqual(sanitize_filename('a/b\\c:d*e?f"g<h>i|j'), "a_b_c_d_e_f_g_h_i_j")

    def test_sanitize_reserved_and_empty(self) -> None:
        self.assertEqual(sanitize_filename("CON"), "_CON")
        self.assertEqual(sanitize_filename("   "), "未命名")
        self.assertEqual(sanitize_filename("..."), "未命名")

    def test_episode_filename_padding(self) -> None:
        self.assertEqual(episode_filename(1), "第01集.mp4")
        self.assertEqual(episode_filename(76), "第76集.mp4")
        self.assertEqual(episode_filename(100), "第100集.mp4")

    def test_human_size(self) -> None:
        self.assertEqual(human_size(0), "0 B")
        self.assertEqual(human_size(1536), "1.5 KB")
        self.assertEqual(human_size(5 * 1024 * 1024), "5.0 MB")

    def test_short_id_stable(self) -> None:
        self.assertEqual(short_id("a", 1), short_id("a", 1))
        self.assertNotEqual(short_id("a", 1), short_id("a", 2))
        self.assertEqual(len(short_id("a", 1)), 16)


class DatabaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.temp.name) / "test.db")
        self.drama = Drama(
            source_key="test",
            source_name="测试站",
            drama_id="100",
            title="测试剧",
            total=3,
            sid=1,
        )
        self.db.upsert_drama(self.drama)

    def tearDown(self) -> None:
        self.db.close()
        self.temp.cleanup()

    def test_upsert_drama(self) -> None:
        row = self.db.get_drama("test:100")
        self.assertIsNotNone(row)
        self.assertEqual(row["title"], "测试剧")
        self.assertEqual(row["total"], 3)

    def test_add_jobs_is_idempotent(self) -> None:
        added = self.db.add_jobs(self.drama, [1, 2, 3])
        self.assertEqual(added, 3)
        added_again = self.db.add_jobs(self.drama, [2, 3, 4])
        self.assertEqual(added_again, 1)
        jobs = self.db.jobs()
        self.assertEqual(len(jobs), 4)
        self.assertEqual([job.episode for job in jobs], [1, 2, 3, 4])

    def test_job_state_transitions_and_counts(self) -> None:
        self.db.add_jobs(self.drama, [1, 2])
        jobs = self.db.jobs()
        self.db.update_job(jobs[0].id, state="completed", bytes=123, file="x.mp4")
        self.db.update_job(jobs[1].id, state="failed", error="网络错误")
        counts = self.db.job_counts("test:100")
        self.assertEqual(counts.get("completed"), 1)
        self.assertEqual(counts.get("failed"), 1)
        library = self.db.library_dramas()
        self.assertEqual(library[0]["total"], 2)
        self.assertEqual(library[0]["completed"], 1)

    def test_reset_interrupted(self) -> None:
        self.db.add_jobs(self.drama, [1, 2, 3])
        jobs = self.db.jobs()
        self.db.update_job(jobs[0].id, state="downloading")
        self.db.update_job(jobs[1].id, state="queued")
        count = self.db.reset_interrupted()
        self.assertEqual(count, 3)
        states = {job.id: job.state for job in self.db.jobs()}
        self.assertTrue(all(state == "paused" for state in states.values()))

    def test_merged_items(self) -> None:
        item = self.db.add_merged(self.drama, "D:/x/full.mp4", 3, 1000)
        self.assertEqual(item["episodes"], 3)
        self.assertEqual(len(self.db.merged_for("test:100")), 1)
        self.db.delete_merged(item["id"])
        self.assertEqual(self.db.merged(), [])


class JobModelTests(unittest.TestCase):
    def test_job_roundtrip(self) -> None:
        job = Job.from_row(
            {
                "id": "abc",
                "drama_key": "test:1",
                "drama_title": "剧",
                "episode": 2,
                "state": "queued",
                "bytes": 0,
                "total": 0,
                "speed": 0.0,
                "error": "",
                "file": "",
                "created": 1,
                "updated": 2,
            }
        )
        data = job.to_dict()
        self.assertEqual(data["id"], "abc")
        self.assertEqual(data["episode"], 2)
        self.assertEqual(data["state"], "queued")


if __name__ == "__main__":
    unittest.main()
