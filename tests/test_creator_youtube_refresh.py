from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from creator_youtube_refresh import (
    growth_per_day,
    incremental_target,
    parse_watch_page,
    parse_youtube_feed,
    performance_target,
)
from creator_youtube_rss_refresh import parse_rss_video_stats


class CreatorYouTubeRefreshTests(unittest.TestCase):
    def test_youtube_rss_parser_reads_verified_ids_and_dates(self) -> None:
        sample = """<?xml version='1.0' encoding='UTF-8'?>
<feed xmlns:yt='http://www.youtube.com/xml/schemas/2015' xmlns='http://www.w3.org/2005/Atom'>
  <entry>
    <yt:videoId>abc123</yt:videoId>
    <yt:channelId>UC1234567890123456789012</yt:channelId>
    <title>New Video</title>
    <published>2026-09-05T12:00:00+00:00</published>
  </entry>
</feed>"""
        rows = parse_youtube_feed(sample)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["videoId"], "abc123")
        self.assertEqual(rows[0]["title"], "New Video")
        self.assertEqual(rows[0]["publishedAt"].date().isoformat(), "2026-09-05")

    def test_production_rss_parser_reads_official_view_statistics(self) -> None:
        sample = """<?xml version='1.0' encoding='UTF-8'?>
<feed xmlns:yt='http://www.youtube.com/xml/schemas/2015'
      xmlns:media='http://search.yahoo.com/mrss/'
      xmlns='http://www.w3.org/2005/Atom'>
  <entry>
    <yt:videoId>abc123</yt:videoId>
    <yt:channelId>UC1234567890123456789012</yt:channelId>
    <title>New Video</title>
    <published>2026-09-05T12:00:00+00:00</published>
    <media:group>
      <media:community><media:statistics views='987654'/></media:community>
    </media:group>
  </entry>
</feed>"""
        rows = parse_rss_video_stats(sample)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["views"], 987654)
        self.assertEqual(rows[0]["channelId"], "UC1234567890123456789012")

    def test_production_rss_parser_fails_closed_without_view_statistics(self) -> None:
        sample = """<?xml version='1.0' encoding='UTF-8'?>
<feed xmlns:yt='http://www.youtube.com/xml/schemas/2015' xmlns='http://www.w3.org/2005/Atom'>
  <entry><yt:videoId>abc123</yt:videoId><title>New Video</title><published>2026-09-05T12:00:00+00:00</published></entry>
</feed>"""
        self.assertEqual(parse_rss_video_stats(sample), [])

    def test_watch_page_parser_requires_expected_video(self) -> None:
        html = '''<script>var ytInitialPlayerResponse = {"videoDetails":{"videoId":"abc123","channelId":"UC1234567890123456789012","title":"Test","viewCount":"987654"}};</script>'''
        parsed = parse_watch_page(html, "abc123", "UC1234567890123456789012")
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed["views"], 987654)
        self.assertIsNone(parse_watch_page(html, "wrong", "UC1234567890123456789012"))

    def test_growth_uses_snapshot_delta_not_lifetime_views(self) -> None:
        now = datetime(2026, 9, 7, 12, tzinfo=timezone.utc)
        prior = {"views": 1_000_000, "checkedAt": (now - timedelta(hours=12)).isoformat()}
        growth = growth_per_day(prior, 1_100_000, now)
        self.assertEqual(round(growth), 200_000)

    def test_performance_curve_is_uncapped(self) -> None:
        bucket, move = performance_target(64.0)
        self.assertEqual(bucket, "breakout")
        self.assertGreater(move, 2.5)

    def test_incremental_target_never_undoes_past_positive_outcome(self) -> None:
        self.assertEqual(incremental_target(1.2, 0.8), 0.0)
        self.assertAlmostEqual(incremental_target(1.2, 2.0), 0.8)
        self.assertEqual(incremental_target(1.2, -1.0), 0.0)
        self.assertLess(incremental_target(-0.5, -1.2), 0)


if __name__ == "__main__":
    unittest.main()
