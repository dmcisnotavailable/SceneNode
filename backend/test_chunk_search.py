# backend/test_chunk_search.py

import unittest

from chunking import build_chunks
from search import search_segments, search_chunks


class TestChunkSearch(unittest.TestCase):

    def setUp(self):
        self.transcript = {
            "segments": [
                {
                    "start": 10,
                    "end": 16,
                    "text": "我们计划第二天早上",
                },
                {
                    "start": 16,
                    "end": 22,
                    "text": "去海洋馆参观企鹅。",
                },
                {
                    "start": 30,
                    "end": 36,
                    "text": "下午我们去吃拉面。",
                },
                {
                    "start": 36,
                    "end": 42,
                    "text": "然后回酒店休息。",
                },
            ]
        }

    def test_chunk_creation(self):
        chunks = build_chunks(self.transcript)

        self.assertEqual(len(chunks), 4)

        self.assertEqual(
            chunks[0]["segment_indices"],
            [0, 1],
        )

        self.assertEqual(chunks[0]["start"], 10)
        self.assertEqual(chunks[0]["end"], 22)

    def test_old_search_misses_boundary(self):
        results = search_segments(
            self.transcript,
            "早上去海洋馆",
        )

        self.assertEqual(len(results), 0)

    def test_new_search_finds_boundary(self):
        results = search_chunks(
            self.transcript,
            "早上去海洋馆",
        )

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["start"], 10)
        self.assertEqual(results[0]["end"], 22)
        self.assertEqual(
            results[0]["segment_indices"],
            [0, 1],
        )

    def test_exact_keyword(self):
        results = search_chunks(
            self.transcript,
            "企鹅",
        )

        self.assertEqual(len(results), 1)
        self.assertIn("企鹅", results[0]["text"])

    def test_no_match(self):
        results = search_chunks(
            self.transcript,
            "火星探测器",
        )

        self.assertEqual(results, [])

    def test_empty_query(self):
        results = search_chunks(
            self.transcript,
            "   ",
        )

        self.assertEqual(results, [])

    def test_original_segments_unchanged(self):
        original = self.transcript["segments"][0].copy()

        search_chunks(
            self.transcript,
            "早上去海洋馆",
        )

        self.assertEqual(
            self.transcript["segments"][0],
            original,
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
