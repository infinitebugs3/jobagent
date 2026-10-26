"""Connection pool and the two caches that make a page load feel instant.

`db.connect()` opens a fresh connection per call, which is right for a CLI that
runs one command and exits and wrong for a server handling a request every few
hundred milliseconds. This module adds a pool for the API without touching
`db.connect()`, so the CLI keeps working exactly as before.
"""

from __future__ import annotations

import time
from contextlib import contextmanager

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from jobagent import db, search

_pool: ConnectionPool | None = None


def _configure(conn) -> None:
    # Every pooled connection needs the trigram GUC the scorer depends on.
    # Set once when the connection is created, not per query.
    #
    # The commit is required, not tidiness: `SET` opens a transaction, and the
    # pool rejects any connection a configure hook leaves in INTRANS.
    db.configure_session(conn)
    conn.commit()


def pool() -> ConnectionPool:
    global _pool
    if _pool is None:
        _pool = ConnectionPool(
            db.DSN,
            min_size=1,
            max_size=8,
            kwargs={"row_factory": dict_row},
            configure=_configure,
            open=True,
        )
    return _pool


@contextmanager
def conn():
    with pool().connection() as c:
        yield c


def close_pool() -> None:
    global _pool
    if _pool is not None:
        _pool.close()
        _pool = None


# ---------------------------------------------------------------------------
# Caches
# ---------------------------------------------------------------------------
class TTLCache:
    """Smallest thing that works. Single process, single user, no eviction
    pressure -- reaching for Redis here would be cosplay."""

    def __init__(self, ttl_seconds: float):
        self.ttl = ttl_seconds
        self._store: dict = {}

    def get(self, key):
        hit = self._store.get(key)
        if hit and time.monotonic() - hit[0] < self.ttl:
            return hit[1]
        return None

    def set(self, key, value):
        self._store[key] = (time.monotonic(), value)
        return value

    def clear(self) -> None:
        self._store.clear()


# Synonyms change only when someone runs `seed-synonyms`. Re-reading ~80 rows on
# every keystroke of a search box is pure waste.
_synonyms = TTLCache(ttl_seconds=300)

# Facet counts are the expensive part of a page load (a 12-term profile has to
# scan and aggregate) and the least time-sensitive thing on screen -- the corpus
# only changes when `sync` runs. A minute of staleness is invisible; a second of
# latency on every filter click is not.
facets_cache = TTLCache(ttl_seconds=60)


def synonym_map(c) -> dict[str, list[str]]:
    cached = _synonyms.get("all")
    if cached is None:
        cached = _synonyms.set("all", db.synonym_map(c))
    return cached


def expand(c, query_text: str):
    return search.expand_terms(query_text, synonym_map(c))


def invalidate_caches() -> None:
    """Called after any write that could change what a search returns."""
    _synonyms.clear()
    facets_cache.clear()
