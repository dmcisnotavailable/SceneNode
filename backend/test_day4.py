# backend/test_day4.py

import unittest

from chunking import build_chunks
from search import search_chunks
from semantic_service import (
    fingerprint,
    is_vague_query,
)


class TestDay4(unittest.TestCase):

    def test_gap_prevents_merge(self):
        transcript = {
            "segments": [
                {
                    "start": 0,
                    "end": 5,
                    "text": "我们去海边",
                },
                {
                    "start": 20,
                    "end": 25,
                    "text": "看日落",
                },
            ]
        }

        chunks = build_chunks(
            transcript,
            max_gap_seconds=3.0,
        )

        self.assertEqual(
            chunks[0]["segment_indices"],
            [0],
        )

    def test_adjacent_segments_merge(self):
        transcript = {
            "segments": [
                {
                    "start": 0,
                    "end": 5,
                    "text": "第二天早上",
                },
                {
                    "start": 5,
                    "end": 10,
                    "text": "去海洋馆",
                },
            ]
        }

        results = search_chunks(
            transcript,
            "早上去海洋馆",
        )

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["start"], 0)
        self.assertEqual(results[0]["end"], 10)

    def test_vague_query(self):
        self.assertTrue(is_vague_query("what"))
        self.assertTrue(is_vague_query("为什么？"))
        self.assertFalse(
            is_vague_query("第二天去哪看夕阳")
        )

    def test_cache_fingerprint_changes(self):
        transcript = {
            "segments": [
                {
                    "start": 0,
                    "end": 5,
                    "text": "原始内容",
                }
            ]
        }

        old_hash = fingerprint(transcript)

        transcript["segments"][0]["text"] = "修改后的内容"

        new_hash = fingerprint(transcript)

        self.assertNotEqual(old_hash, new_hash)


if __name__ == "__main__":
    unittest.main(verbosity=2)
