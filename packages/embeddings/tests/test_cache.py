"""The vector cache, and the one property a shared brain depends on.

The cache's lifetime is the index's lifetime, and an index belongs to a brain that a long-lived interface opens on
one thread and lets the interpreter collect on another. That crossing is the whole subject here.
"""

from __future__ import annotations

import threading
from pathlib import Path

from vitruvio.embeddings.cache import EmbeddingCache


class TestAcrossThreads:
    def test_a_cache_opened_on_one_thread_closes_on_another(self, tmp_path: Path) -> None:
        """Otherwise `close()` raises into `__del__`'s guard and the connection is never closed at all.

        The visible symptom is a `ResourceWarning` at deallocation, which `filterwarnings = error` turns into a
        failure blamed on whichever test happened to be running when the collector fired.
        """
        opened: list[EmbeddingCache] = []
        thread = threading.Thread(target=lambda: opened.append(EmbeddingCache(tmp_path / "vectors.sqlite", "test/1")))
        thread.start()
        thread.join()

        opened[0].close()

    def test_a_cache_opened_on_one_thread_is_readable_on_another(self, tmp_path: Path) -> None:
        cache = EmbeddingCache(tmp_path / "vectors.sqlite", "test/1")
        cache.put_many({b"key": (1.0, 2.0)}, space="test/1")
        found: list[dict[bytes, tuple[float, ...]]] = []

        thread = threading.Thread(target=lambda: found.append(cache.get_many([b"key"])))
        thread.start()
        thread.join()

        assert found == [{b"key": (1.0, 2.0)}]
