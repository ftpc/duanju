"""配置与运行环境单元测试。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from duanju.config import Config, data_dir_candidates, choose_data_dir
from duanju.winenv import integrity_level


class ConfigTests(unittest.TestCase):
    def test_load_explicit_path_creates_config(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "config.json"
            config = Config.load(path)
            self.assertEqual(config.path, path)
            self.assertEqual(config.data_dir, path.parent)
            self.assertTrue(path.is_file())
            self.assertEqual(config.warning, "")

    def test_load_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "config.json"
            first = Config.load(path)
            first.concurrency = 4
            first.save()
            second = Config.load(path)
            self.assertEqual(second.concurrency, 4)

    def test_download_dir_falls_back_when_unwritable(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "config.json"
            config = Config.load(path)
            config.download_dir = "Z:\\definitely-not-exist\\x"
            config._ensure_download_dir()  # noqa: SLF001
            self.assertTrue(Path(config.download_dir).is_dir())
            self.assertIn("已改用", config.warning)

    def test_data_dir_candidates_ordered(self) -> None:
        candidates = data_dir_candidates()
        self.assertGreaterEqual(len(candidates), 2)


class WinEnvTests(unittest.TestCase):
    def test_integrity_level_string(self) -> None:
        level = integrity_level()
        self.assertIn(
            level,
            {"untrusted", "low", "medium", "medium-plus", "high", "system", "unknown"},
        )


if __name__ == "__main__":
    unittest.main()
