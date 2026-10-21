"""分段直链（第1-20集 / 第21-40集）解析与命名单测。"""

from __future__ import annotations

import unittest

from duanju.sources import MacCmsSource
from duanju.util import episode_filename

API_PAYLOAD = {
    "code": 1,
    "msg": "数据列表",
    "page": 1,
    "pagecount": 1,
    "limit": "20",
    "total": 1,  # 顶层 total 是分页条数，不是集数
    "list": [
        {
            "vod_name": "示例短剧",
            "vod_total": 75,
            "vod_play_url": (
                "第1-20集$https://cdn.test/a/index.m3u8#"
                "第21-40集$https://cdn.test/b/index.m3u8#"
                "第41-60集$https://cdn.test/c/index.m3u8#"
                "第61-75集完结$https://cdn.test/d/index.m3u8"
            ),
        }
    ],
}


class ParseSegmentsTest(unittest.TestCase):
    def test_multi_segment(self) -> None:
        rows = MacCmsSource._parse_api_segments(API_PAYLOAD)
        self.assertEqual(len(rows), 4)
        self.assertEqual(rows[0], ("第1-20集", "https://cdn.test/a/index.m3u8"))
        self.assertEqual(rows[-1][0], "第61-75集完结")

    def test_only_first_play_group(self) -> None:
        payload = {
            "list": [
                {
                    "vod_play_url": (
                        "第1-2集$https://cdn.test/one.m3u8"
                        "$$$第1-2集$https://cdn.test/backup.m3u8"
                    )
                }
            ]
        }
        rows = MacCmsSource._parse_api_segments(payload)
        self.assertEqual(rows, [("第1-2集", "https://cdn.test/one.m3u8")])

    def test_blank_and_broken_entries_skipped(self) -> None:
        payload = {"list": [{"vod_play_url": "第1集$https://x.test/1.m3u8#无地址##第2集$"}]}
        rows = MacCmsSource._parse_api_segments(payload)
        self.assertEqual(rows, [("第1集", "https://x.test/1.m3u8")])

    def test_not_dict_payload(self) -> None:
        self.assertEqual(MacCmsSource._parse_api_segments([]), [])
        self.assertEqual(MacCmsSource._parse_api_segments("oops"), [])
        self.assertEqual(MacCmsSource._parse_api_segments({"list": []}), [])

    def test_drama_total(self) -> None:
        self.assertEqual(MacCmsSource._api_drama_total(API_PAYLOAD), 75)
        self.assertEqual(MacCmsSource._api_drama_total({"list": [{}]}), 0)
        self.assertEqual(MacCmsSource._api_drama_total(None), 0)


class EpisodeFilenameTest(unittest.TestCase):
    def test_plain_episode(self) -> None:
        self.assertEqual(episode_filename(3), "第03集.mp4")

    def test_same_as_number_label_falls_back(self) -> None:
        self.assertEqual(episode_filename(3, label="第3集"), "第03集.mp4")

    def test_segment_label(self) -> None:
        self.assertEqual(episode_filename(1, label="第1-20集"), "第1-20集.mp4")
        self.assertEqual(episode_filename(4, label="第61-75集完结"), "第61-75集完结.mp4")


if __name__ == "__main__":
    unittest.main()
