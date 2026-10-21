"""图片缓存目录（可自定义）单元测试。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from ui.image_loader import ImageLoader, get_cache_dir, set_cache_dir


class ImageCacheDirTests(unittest.TestCase):
    def setUp(self) -> None:
        self._original = get_cache_dir()

    def tearDown(self) -> None:
        set_cache_dir(self._original)

    def test_switch_dir_changes_path_and_clears_memory_cache(self) -> None:
        with tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as second:
            self.assertTrue(set_cache_dir(first))
            loader = ImageLoader()
            try:
                url = "https://example.test/cover.jpg"
                before = loader.get_cache_path(url)
                self.assertIsNotNone(before)
                self.assertEqual(before.parent, Path(first))

                loader._cache[url] = str(before)  # noqa: SLF001

                self.assertTrue(set_cache_dir(second))
                after = loader.get_cache_path(url)
                self.assertIsNotNone(after)
                self.assertEqual(after.parent, Path(second))
                self.assertEqual(loader._cache, {})  # noqa: SLF001
            finally:
                loader.shutdown()

    def test_unwritable_dir_is_rejected_and_keeps_previous(self) -> None:
        with tempfile.TemporaryDirectory() as good:
            self.assertTrue(set_cache_dir(good))
            self.assertFalse(set_cache_dir("Z:\\definitely-not-exist\\cache"))
            self.assertEqual(get_cache_dir(), Path(good))

    def test_disabled_cache_returns_none_path(self) -> None:
        self.assertFalse(set_cache_dir(None))
        self.assertIsNone(get_cache_dir())
        loader = ImageLoader()
        try:
            self.assertIsNone(loader.get_cache_path("https://example.test/a.png"))
        finally:
            loader.shutdown()


if __name__ == "__main__":
    unittest.main()
