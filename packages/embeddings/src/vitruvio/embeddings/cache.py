"""Caching vectors by the content that produced them.

Not optional. ``Index.build`` is a full rebuild on every commit *and* on every open, so a brain with fifty thousand
blocks would re-embed all of them to add one -- at 4.5 ms per embedding, nearly four minutes for a one-block write. With
the cache it is one embedding.

**The key is the embedded string, not the block.** ``sha256(model_tag | space | role | content)``, which means:

* re-registering identical content is free;
* two blocks whose projection happens to be identical share one vector;
* editing a field the projection does not read costs nothing;
* and a change to the model, the projection or the chunker invalidates cleanly, because all three are inside the tag.

SQLite, because it has to survive a restart, tolerate two processes, and support partial reads. One file per model tag,
so switching models never invalidates the old vectors and switching back is free rather than a four-minute rebuild.

It lives under the brain's derived directory, so it is never inside ``blobs/`` and can never end up in a published
layer. It is fully re-derivable and it can be gigabytes.
"""

from __future__ import annotations

import hashlib
import sqlite3
import struct
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence
    from pathlib import Path

    from vitruvio.embeddings.base import Vector

SCHEMA = """
CREATE TABLE IF NOT EXISTS vectors (
  key   BLOB PRIMARY KEY,
  space TEXT NOT NULL,
  dims  INTEGER NOT NULL,
  data  BLOB NOT NULL
) WITHOUT ROWID
"""

BATCH = 500
"""How many keys to look up at once. SQLite caps bound parameters, and a rebuild can ask for a hundred thousand."""


def cache_key(model_tag: str, space: str, role: str, content: str) -> bytes:
    """
    The key a vector is filed under.

    Over the *embedded string* rather than over the block, which is what makes the cache survive an edit to an
    unprojected field, and what makes two identical projections share one vector.

    Args:
        model_tag (str): The full model tag, which carries the projection and chunker identities too.
        space (str): Which embedding space.
        role (str): query or passage, since some models prefix them differently.
        content (str): The exact string that will be embedded.

    Returns:
        bytes: A 32-byte key.
    """
    digest = hashlib.sha256()
    for part in (model_tag, space, role, content):
        digest.update(part.encode("utf-8"))
        digest.update(b"\x00")
    return digest.digest()


class EmbeddingCache:
    """
    Vectors on disk, keyed by what produced them.

    Attributes:
        path (Path): The SQLite file.
        model_tag (str): Which model's vectors this holds.
    """

    def __init__(self, path: Path, model_tag: str) -> None:
        """
        Open or create the cache.

        Args:
            path (Path): Where it lives.
            model_tag (str): The model tag, used in every key.
        """
        self.path = path
        self.model_tag = model_tag
        path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(path)
        # WAL so a reader and a writer coexist: two vitruvio processes over one brain is ordinary, and a locked cache
        # would make the second one fail rather than wait.
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute(SCHEMA)
        self._connection.commit()

    @classmethod
    def for_model(cls, home: Path, model_tag: str) -> EmbeddingCache:
        """
        The cache file belonging to one model tag.

        Args:
            home (Path): The directory holding caches.
            model_tag (str): The tag.

        Returns:
            EmbeddingCache: The cache.
        """
        name = hashlib.sha256(model_tag.encode("utf-8")).hexdigest()[:16]
        return cls(home / f"{name}.sqlite", model_tag)

    def get_many(self, keys: Sequence[bytes]) -> dict[bytes, Vector]:
        """
        Look up several vectors at once.

        Args:
            keys (Sequence[bytes]): Cache keys.

        Returns:
            dict[bytes, Vector]: What was found. Absent keys are simply missing.
        """
        if not keys:
            return {}
        found: dict[bytes, Vector] = {}
        for start in range(0, len(keys), BATCH):
            batch = keys[start : start + BATCH]
            placeholders = ",".join("?" * len(batch))
            rows = self._connection.execute(
                f"SELECT key, dims, data FROM vectors WHERE key IN ({placeholders})",
                batch,
            )
            for key, dims, data in rows:
                found[bytes(key)] = struct.unpack(f"<{dims}f", data)
        return found

    def put_many(self, items: Mapping[bytes, Vector], space: str) -> None:
        """
        Store several vectors.

        Args:
            items (Mapping[bytes, Vector]): Key to vector.
            space (str): Which embedding space they belong to.
        """
        if not items:
            return
        rows = [(key, space, len(vector), struct.pack(f"<{len(vector)}f", *vector)) for key, vector in items.items()]
        self._connection.executemany(
            "INSERT OR REPLACE INTO vectors (key, space, dims, data) VALUES (?, ?, ?, ?)", rows
        )
        self._connection.commit()

    def count(self) -> int:
        """How many vectors are held."""
        return int(self._connection.execute("SELECT COUNT(*) FROM vectors").fetchone()[0])

    def vacuum(self, keep: Iterable[bytes]) -> int:
        """
        Delete everything not in ``keep``, and reclaim the space.

        Args:
            keep (Iterable[bytes]): Keys still in use.

        Returns:
            int: How many rows were removed.
        """
        retained = set(keep)
        removed = 0
        for (key,) in self._connection.execute("SELECT key FROM vectors").fetchall():
            if bytes(key) not in retained:
                self._connection.execute("DELETE FROM vectors WHERE key = ?", (key,))
                removed += 1
        self._connection.commit()
        if removed:
            self._connection.execute("VACUUM")
        return removed

    def close(self) -> None:
        """
        Close the connection. Idempotent, so a ``__del__`` after an explicit close is safe.
        """
        if self._connection is not None:
            self._connection.close()
            self._connection = None  # type: ignore[assignment]

    def __enter__(self) -> EmbeddingCache:
        """Enter a scope that closes the connection on exit."""
        return self

    def __exit__(self, *exception: object) -> None:
        """Close the connection."""
        self.close()

    def __del__(self) -> None:
        """
        Release the connection when this cache is collected.

        The cache's lifetime *is* the index's lifetime -- there is no earlier point at which closing would be correct,
        because the index may embed again at any time. So this is the ownership rather than a workaround for a missing
        teardown, and without it the connection was never closed at all: ``close()`` existed and nothing called it.

        Python 3.13 is what exposed it, by emitting ``ResourceWarning`` when an unclosed connection is deallocated --
        which ``filterwarnings = error`` turned into a test failure on that version alone. The leak was there on every
        version; only 3.13 said so.

        Guarded, because during interpreter shutdown a module global can already be ``None``.
        """
        import contextlib

        with contextlib.suppress(Exception):  # pragma: no cover - interpreter shutdown
            self.close()


class MemoryCache:
    """
    A cache with no file, for tests and for a caller that does not want a sidecar.

    Attributes:
        model_tag (str): Which model's vectors this holds.
    """

    def __init__(self, model_tag: str = "memory") -> None:
        """Build an empty cache."""
        self.model_tag = model_tag
        self._vectors: dict[bytes, Vector] = {}

    def get_many(self, keys: Sequence[bytes]) -> dict[bytes, Vector]:
        """Look up several vectors."""
        return {key: self._vectors[key] for key in keys if key in self._vectors}

    def put_many(self, items: Mapping[bytes, Vector], space: str) -> None:
        """Store several vectors."""
        self._vectors.update(items)

    def count(self) -> int:
        """How many vectors are held."""
        return len(self._vectors)

    def vacuum(self, keep: Iterable[bytes]) -> int:
        """Drop everything not in ``keep``."""
        retained = set(keep)
        removed = [key for key in self._vectors if key not in retained]
        for key in removed:
            del self._vectors[key]
        return len(removed)

    def close(self) -> None:
        """Nothing to close."""
