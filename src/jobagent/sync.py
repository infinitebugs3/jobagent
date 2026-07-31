"""The sync loop. Polite, idempotent, observable.

Rate limiting is not optional politeness theatre -- these are free public
endpoints and hammering them is how they stop being free public endpoints.
1-2 requests/second is plenty for a registry of a few hundred companies.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import httpx

from . import db
from .ats.fetchers import FetchError, fetch, make_client


class RateLimiter:
    """Simple per-provider spacing. Sequential by design -- you are one user."""

    def __init__(self, per_second: float = 1.5):
        self.min_interval = 1.0 / per_second
        self._last: dict[str, float] = {}

    def wait(self, key: str) -> None:
        now = time.monotonic()
        last = self._last.get(key, 0.0)
        sleep_for = self.min_interval - (now - last)
        if sleep_for > 0:
            time.sleep(sleep_for)
        self._last[key] = time.monotonic()


@dataclass
class SyncStats:
    companies: int = 0
    ok: int = 0
    failed: int = 0
    inserted: int = 0
    updated: int = 0
    closed: int = 0

    def __str__(self) -> str:
        return (
            f"{self.companies} companies | {self.ok} ok, {self.failed} failed | "
            f"+{self.inserted} new, ~{self.updated} updated, -{self.closed} closed"
        )


def sync_company(conn, client: httpx.Client, company: dict, limiter: RateLimiter) -> tuple[int, int, int]:
    provider, token = company["ats_provider"], company["ats_token"]
    run_id = db.start_run(conn, company["id"], provider)
    # Commit the run row BEFORE the risky work. Otherwise the rollback in the
    # error path below discards this insert too, finish_run() updates zero rows,
    # and the failure erases its own evidence -- which is the one thing this
    # table exists to prevent.
    conn.commit()
    try:
        limiter.wait(provider)
        postings = fetch(provider, token, client)
        inserted, updated = db.upsert_jobs(conn, company["id"], company["name"], postings)
        closed = db.close_stale_jobs(conn, company["id"])
        db.finish_run(conn, run_id, len(postings), inserted, updated, ok=True)
        conn.execute("update company set last_synced_at = now() where id = %s", (company["id"],))
        conn.commit()
        return inserted, updated, closed
    except (FetchError, httpx.HTTPError) as exc:
        conn.rollback()
        db.finish_run(conn, run_id, ok=False, error=str(exc)[:500])
        conn.commit()
        raise


def sync_all(conn, per_second: float = 1.5, verbose: bool = True) -> SyncStats:
    stats = SyncStats()
    limiter = RateLimiter(per_second)
    companies = db.active_companies(conn)
    stats.companies = len(companies)

    with make_client() as client:
        for c in companies:
            try:
                ins, upd, clo = sync_company(conn, client, c, limiter)
                stats.ok += 1
                stats.inserted += ins
                stats.updated += upd
                stats.closed += clo
                if verbose and ins:
                    print(f"  {c['name']:<32} +{ins}")
            except Exception as exc:  # noqa: BLE001 - one bad board must not stop the run
                stats.failed += 1
                if verbose:
                    print(f"  {c['name']:<32} FAILED: {exc}")
    return stats
