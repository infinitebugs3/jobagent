"""Thin psycopg wrapper. No ORM -- the schema is nine tables and SQL is clearer."""

from __future__ import annotations

import os
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from . import search
from .models import RawPosting
from .normalize import dedupe_key, location_facet, seniority_of


def _load_dotenv(path: str = ".env") -> None:
    """Read .env into os.environ without pulling in a dependency.

    A real environment variable always wins, so `DATABASE_URL=... python -m
    jobagent.cli ...` still overrides the file.
    """
    fh = Path(path)
    if not fh.is_file():
        return
    for line in fh.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


_load_dotenv()

DSN = os.environ.get("DATABASE_URL", "postgresql://jobagent:jobagent@localhost:5432/jobagent")


def configure_session(conn) -> None:
    """Pin the trigram cutoff to the one the scorer uses.

    The `<%` operator in search.candidate_clause reads its threshold from this
    GUC, while the score expression compares word_similarity() to FUZZY_FLOOR
    directly. If they drift apart the index-backed candidate filter stops being
    a superset of the scored set and rows disappear with no error anywhere.
    """
    conn.execute(f"set pg_trgm.word_similarity_threshold = {search.FUZZY_FLOOR}")


@contextmanager
def connect():
    with psycopg.connect(DSN, row_factory=dict_row) as conn:
        configure_session(conn)
        yield conn


def run_migrations(conn, path: str = "migrations") -> None:
    """Apply one .sql file, or every .sql in a directory in filename order.

    Every migration is written `if not exists`, so re-running the whole
    directory is the normal case, not a recovery path.
    """
    target = Path(path)
    files = sorted(target.glob("*.sql")) if target.is_dir() else [target]
    for fh in files:
        conn.execute(fh.read_text(encoding="utf-8"))
    conn.commit()


# ---------------------------------------------------------------------------
# Companies
# ---------------------------------------------------------------------------
def upsert_company(conn, name, provider, token, careers_url=None, size_bucket=None) -> int:
    row = conn.execute(
        """
        insert into company (name, ats_provider, ats_token, careers_url, size_bucket)
        values (%s, %s, %s, %s, %s)
        on conflict (ats_provider, ats_token) do update
            set name = excluded.name,
                careers_url = coalesce(excluded.careers_url, company.careers_url)
        returning id
        """,
        (name, provider, token, careers_url, size_bucket),
    ).fetchone()
    return row["id"]


def active_companies(conn) -> list[dict]:
    return conn.execute(
        "select * from company where active and ats_provider <> 'unknown' order by id"
    ).fetchall()


# ---------------------------------------------------------------------------
# Jobs
# ---------------------------------------------------------------------------
def upsert_jobs(conn, company_id: int, company_name: str, postings: list[RawPosting]) -> tuple[int, int]:
    """Returns (inserted, updated). Idempotent -- run it a hundred times."""
    inserted = updated = 0
    for p in postings:
        row = conn.execute(
            """
            insert into job (company_id, external_id, title, location, remote_type,
                             description_md, apply_url, dedupe_key, posted_at,
                             location_bucket, seniority)
            values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            on conflict (company_id, external_id) do update
                set title        = excluded.title,
                    location     = excluded.location,
                    remote_type  = excluded.remote_type,
                    description_md = excluded.description_md,
                    apply_url    = excluded.apply_url,
                    dedupe_key   = excluded.dedupe_key,
                    location_bucket = excluded.location_bucket,
                    seniority    = excluded.seniority,
                    last_seen_at = now(),
                    closed_at    = null
            returning (xmax = 0) as is_insert
            """,
            (
                company_id,
                p.external_id,
                p.title,
                p.location,
                p.remote_type,
                p.description_md,
                p.apply_url,
                dedupe_key(company_name, p.title, p.location),
                p.posted_at,
                location_facet(p.location),
                seniority_of(p.title),
            ),
        ).fetchone()
        if row["is_insert"]:
            inserted += 1
        else:
            updated += 1
    return inserted, updated


def close_stale_jobs(conn, company_id: int, grace_hours: int = 36) -> int:
    """Anything not seen in the last two syncs is presumed filled or pulled.
    Free closure detection -- no extra requests."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=grace_hours)
    res = conn.execute(
        """
        update job set closed_at = now()
        where company_id = %s and closed_at is null and last_seen_at < %s
        """,
        (company_id, cutoff),
    )
    return res.rowcount


# ---------------------------------------------------------------------------
# Sync bookkeeping
# ---------------------------------------------------------------------------
def start_run(conn, company_id: int, provider: str) -> int:
    return conn.execute(
        "insert into sync_run (company_id, provider) values (%s, %s) returning id",
        (company_id, provider),
    ).fetchone()["id"]


def finish_run(conn, run_id: int, seen=0, inserted=0, updated=0, ok=True, error=None) -> None:
    conn.execute(
        """
        update sync_run
           set finished_at = now(), jobs_seen = %s, jobs_inserted = %s,
               jobs_updated = %s, ok = %s, error = %s
         where id = %s
        """,
        (seen, inserted, updated, ok, error, run_id),
    )


def backfill_facets(conn, batch_size: int = 1000, force: bool = False) -> int:
    """Populate location_bucket / seniority on rows that predate migration 002.

    Idempotent. `force` recomputes every row, which is what you want after
    editing the alias table in normalize.py.
    """
    where = "" if force else "where location_bucket is null or seniority is null"
    rows = conn.execute(f"select id, title, location from job {where}").fetchall()
    if not rows:
        return 0

    for start in range(0, len(rows), batch_size):
        chunk = rows[start : start + batch_size]
        values = [
            (r["id"], location_facet(r["location"]), seniority_of(r["title"]))
            for r in chunk
        ]
        # One statement per batch instead of one per row: 10.5k rows in ~11 round
        # trips rather than 10,500.
        conn.execute(
            """
            update job j
               set location_bucket = v.bucket,
                   seniority       = v.seniority
              from (select * from unnest(
                        %s::bigint[], %s::text[], %s::text[]
                    ) as t(id, bucket, seniority)) v
             where j.id = v.id
            """,
            (
                [v[0] for v in values],
                [v[1] for v in values],
                [v[2] for v in values],
            ),
        )
    conn.commit()
    return len(rows)


# ---------------------------------------------------------------------------
# Synonyms
# ---------------------------------------------------------------------------
def upsert_synonyms(conn, pairs: list[tuple[str, list[str]]]) -> int:
    for term, expansions in pairs:
        conn.execute(
            """
            insert into search_synonym (term, expansions) values (%s, %s)
            on conflict (term) do update set expansions = excluded.expansions
            """,
            (term.lower().strip(), [e.lower().strip() for e in expansions]),
        )
    return len(pairs)


def synonym_map(conn) -> dict[str, list[str]]:
    """Whole table, one query. ~100 rows -- callers cache it per request."""
    return {r["term"]: r["expansions"] for r in conn.execute("select * from search_synonym")}


# ---------------------------------------------------------------------------
# Saved searches
# ---------------------------------------------------------------------------
def list_search_profiles(conn, active_only: bool = True) -> list[dict]:
    where = "where active" if active_only else ""
    return conn.execute(
        f"select * from search_profile {where} order by is_default desc, name"
    ).fetchall()


def get_search_profile(conn, profile_id: int) -> dict | None:
    return conn.execute(
        "select * from search_profile where id = %s", (profile_id,)
    ).fetchone()


def default_search_profile(conn) -> dict | None:
    return conn.execute(
        "select * from search_profile where is_default and active"
    ).fetchone()


def upsert_search_profile(
    conn,
    name: str,
    query_text: str = "",
    exclude_text: str = "",
    filters: dict | None = None,
    is_default: bool = False,
    profile_id: int | None = None,
) -> int:
    """Insert or update by id; a null id inserts. `is_default` is exclusive --
    the partial unique index would reject a second one, so demote first."""
    if is_default:
        conn.execute("update search_profile set is_default = false where is_default")

    payload = Jsonb(filters or {})
    if profile_id is None:
        row = conn.execute(
            """
            insert into search_profile (name, query_text, exclude_text, filters, is_default)
            values (%s, %s, %s, %s, %s)
            returning id
            """,
            (name, query_text, exclude_text, payload, is_default),
        ).fetchone()
    else:
        row = conn.execute(
            """
            update search_profile
               set name = %s, query_text = %s, exclude_text = %s,
                   filters = %s, is_default = %s, updated_at = now()
             where id = %s
            returning id
            """,
            (name, query_text, exclude_text, payload, is_default, profile_id),
        ).fetchone()
    return row["id"]


def delete_search_profile(conn, profile_id: int) -> int:
    return conn.execute(
        "delete from search_profile where id = %s", (profile_id,)
    ).rowcount


# ---------------------------------------------------------------------------
# Labels
# ---------------------------------------------------------------------------
def label_job(
    conn,
    job_id: int,
    verdict: str,
    reason: str = "",
    source: str = "review",
    reason_tags: list[str] | None = None,
) -> int:
    """Label the whole dedupe group, not one row.

    The same role is posted on several boards; 10,520 jobs collapse to 8,544
    distinct dedupe_keys. Labelling per row means reviewing the same job twice
    and double-counting it in every eval. Returns rows affected.
    """
    row = conn.execute("select dedupe_key from job where id = %s", (job_id,)).fetchone()
    if row is None:
        raise ValueError(f"no job with id {job_id}")
    key = row["dedupe_key"]

    res = conn.execute(
        """
        insert into job_label (job_id, verdict, reason, source, reason_tags, dedupe_key)
        select j.id, %s, %s, %s, %s, %s
          from job j
         where j.dedupe_key = %s and j.closed_at is null
        on conflict (job_id) do update
            set verdict     = excluded.verdict,
                reason      = excluded.reason,
                source      = excluded.source,
                reason_tags = excluded.reason_tags,
                dedupe_key  = excluded.dedupe_key,
                labeled_at  = now(),
                updated_at  = now()
        """,
        (verdict, reason, source, reason_tags or [], key, key),
    )
    return res.rowcount


def unlabel_job(conn, job_id: int) -> int:
    """Undo. Removes the verdict from every job in the same dedupe group."""
    return conn.execute(
        """
        delete from job_label
         where dedupe_key = (select dedupe_key from job where id = %s)
            or job_id = %s
        """,
        (job_id, job_id),
    ).rowcount
