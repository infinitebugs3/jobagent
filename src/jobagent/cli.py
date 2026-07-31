"""CLI. Every day-1 workflow is one command.

    python -m jobagent.cli init
    python -m jobagent.cli detect data/companies.csv
    python -m jobagent.cli sync
    python -m jobagent.cli list --location bangalore --max-age 14
    python -m jobagent.cli label 42 apply "good infra ownership"
    python -m jobagent.cli stats
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

from . import db, profile, query, search, sync
from .ats.detector import detect
from .ats.fetchers import make_client


def cmd_init(args) -> int:
    with db.connect() as conn:
        db.run_migrations(conn, args.migration)
    print("schema applied")
    return 0


def cmd_detect(args) -> int:
    """Read a CSV of companies, resolve each to an ATS, write results to the registry."""
    path = Path(args.csv)
    rows = list(csv.DictReader(path.open(encoding="utf-8")))
    resolved, unresolved = 0, []

    with db.connect() as conn, make_client() as client:
        for row in rows:
            name = (row.get("name") or "").strip()
            if not name:
                continue
            url = (row.get("careers_url") or "").strip() or None
            hit = detect(name, url, client, probe=not args.no_probe)
            if hit:
                db.upsert_company(conn, name, hit.provider, hit.token, url, row.get("size_bucket"))
                resolved += 1
                print(f"  {name:<32} {hit.provider}:{hit.token}  ({hit.how})")
            else:
                unresolved.append(name)
                print(f"  {name:<32} -- unresolved")
        conn.commit()

    if unresolved:
        out = Path("data/unresolved.csv")
        out.parent.mkdir(exist_ok=True)
        out.write_text("\n".join(unresolved), encoding="utf-8")
        print(f"\n{len(unresolved)} unresolved written to {out} -- do not chase the tail")

    total = resolved + len(unresolved)
    rate = (resolved / total * 100) if total else 0
    print(f"\nresolved {resolved}/{total} ({rate:.0f}%)")
    return 0


def cmd_sync(args) -> int:
    with db.connect() as conn:
        stats = sync.sync_all(conn, per_second=args.rate)
    print(f"\n{stats}")
    return 0


def _filters_from_args(args) -> query.JobFilters:
    return query.JobFilters(
        query_text=args.q or "",
        remote_types=["remote"] if args.remote else [],
        max_age_days=args.max_age,
        label_status="unlabeled" if args.unlabeled else None,
        title_contains=args.title,
        location_contains=args.location,
        sort=args.sort,
        limit=args.limit,
    )


def cmd_list(args) -> int:
    f = _filters_from_args(args)

    with db.connect() as conn:
        if args.profile:
            profile = (
                db.default_search_profile(conn)
                if args.profile == "default"
                else db.get_search_profile(conn, int(args.profile))
            )
            if profile is None:
                print("no such search profile -- run `jobagent profile-import` first")
                return 1
            query.apply_search_profile(f, profile)
            print(f"using search profile: {profile['name']}")

        terms = search.expand_terms(f.query_text, db.synonym_map(conn))
        sql, params = query.build_job_query(f, terms)
        rows = conn.execute(sql, params).fetchall()

    if not rows:
        print("no matching jobs -- try widening the filters or run sync first")
        return 0
    for r in rows:
        mark = {"apply": "+", "maybe": "?", "no": "-"}.get(r["verdict"], " ")
        score = f"{r['rank_score']:>7.1f}" if terms else " " * 7
        print(f"{mark} [{r['id']:>5}]{score} {r['company'][:20]:<20} {r['title'][:42]:<42} "
              f"{(r['location'] or '')[:16]:<16} {r['age_days']:>3}d")
        if terms and r["matched_terms"]:
            print(f"{' ' * 16}matched: {', '.join(r['matched_terms'])}")
    print(f"\n{len(rows)} jobs")
    return 0


def cmd_label(args) -> int:
    tags = [t.strip() for t in (args.tags or "").split(",") if t.strip()]
    with db.connect() as conn:
        n = db.label_job(conn, args.job_id, args.verdict, args.reason, args.source, tags)
        conn.commit()
    # n > 1 is the normal case: the same role is posted on several boards.
    print(f"job {args.job_id} -> {args.verdict}  ({n} posting(s) in this role group)")
    return 0


def cmd_backfill(args) -> int:
    """Fill location_bucket / seniority on rows that predate migration 002."""
    with db.connect() as conn:
        n = db.backfill_facets(conn, force=args.force)
    print(f"backfilled {n} jobs")
    return 0


def cmd_seed_synonyms(args) -> int:
    pairs = search.load_synonyms_csv(args.csv)
    with db.connect() as conn:
        n = db.upsert_synonyms(conn, pairs)
        conn.commit()
    print(f"loaded {n} synonym terms from {args.csv}")
    return 0


def cmd_profile_import(args) -> int:
    """Compile profile.yaml into a saved search you can then tune in the UI."""
    prof = profile.load_profile(args.path)
    compiled = profile.to_search_profile(prof)
    with db.connect() as conn:
        pid = db.upsert_search_profile(conn, **compiled)
        conn.commit()
    print(f"search profile {pid}: {compiled['name']}")
    print(f"  terms:    {compiled['query_text'][:100]}...")
    print(f"  excludes: {compiled['exclude_text'][:100]}...")
    print(f"  filters:  {compiled['filters']}")
    return 0


def cmd_stats(args) -> int:
    queries = {
        "companies (resolved)": "select count(*) n from company where ats_provider <> 'unknown'",
        "open jobs": "select count(*) n from job where closed_at is null",
        "closed jobs": "select count(*) n from job where closed_at is not null",
        "distinct roles (deduped)": "select count(distinct dedupe_key) n from job where closed_at is null",
        # first_seen_at is when WE saw it; posted_at is when the ATS says it went up.
        # On a first sync every row is "first seen today", so only posted_at means anything.
        "first seen last 7d": "select count(*) n from job where first_seen_at > now() - interval '7 days'",
        "posted last 7d": "select count(*) n from job where posted_at > now() - interval '7 days'",
        "posted 90d+ (stale)": "select count(*) n from job where closed_at is null and posted_at < now() - interval '90 days'",
        "labeled": "select count(*) n from job_label",
        "labeled: apply": "select count(*) n from job_label where verdict = 'apply'",
        "answer bank entries": "select count(*) n from answer_bank",
        "failed syncs (24h)": "select count(*) n from sync_run where not ok and started_at > now() - interval '1 day'",
    }
    with db.connect() as conn:
        for label, q in queries.items():
            print(f"{label:<26} {conn.execute(q).fetchone()['n']:>7}")

        print("\nby provider:")
        for r in conn.execute(
            """select c.ats_provider p, count(j.id) n
                 from company c left join job j on j.company_id = c.id and j.closed_at is null
                group by 1 order by 2 desc"""
        ).fetchall():
            print(f"  {r['p']:<24} {r['n']:>7}")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="jobagent")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("init", help="apply the schema")
    # A directory, so new migrations apply without anyone remembering a flag.
    s.add_argument("--migration", default="migrations")
    s.set_defaults(fn=cmd_init)

    s = sub.add_parser("detect", help="resolve companies in a CSV to their ATS")
    s.add_argument("csv")
    s.add_argument("--no-probe", action="store_true", help="skip slug guessing")
    s.set_defaults(fn=cmd_detect)

    s = sub.add_parser("sync", help="fetch jobs for every active company")
    s.add_argument("--rate", type=float, default=1.5, help="requests/sec per provider")
    s.set_defaults(fn=cmd_sync)

    s = sub.add_parser("list", help="search and browse stored jobs")
    s.add_argument("--q", help="comma-separated search terms, e.g. 'spring boot, rag'")
    s.add_argument("--profile", help="saved search id, or 'default'")
    s.add_argument("--location")
    s.add_argument("--title")
    s.add_argument("--remote", action="store_true")
    s.add_argument("--max-age", type=int, help="only jobs first seen in the last N days")
    s.add_argument("--unlabeled", action="store_true")
    s.add_argument("--sort", default="relevance", choices=list(query.SORTS))
    s.add_argument("--limit", type=int, default=40)
    s.set_defaults(fn=cmd_list)

    s = sub.add_parser("label", help="record your verdict on a job")
    s.add_argument("job_id", type=int)
    s.add_argument("verdict", choices=["apply", "maybe", "no"])
    s.add_argument("reason", nargs="?", default="")
    s.add_argument("--tags", default="", help="comma-separated, e.g. 'too-senior,wrong-stack'")
    s.add_argument("--source", default="review", choices=["review", "exploration"])
    s.set_defaults(fn=cmd_label)

    s = sub.add_parser("backfill", help="fill location_bucket/seniority on existing jobs")
    s.add_argument("--force", action="store_true", help="recompute every row, not just nulls")
    s.set_defaults(fn=cmd_backfill)

    s = sub.add_parser("seed-synonyms", help="load the curated search alias table")
    s.add_argument("--csv", default="data/synonyms.csv")
    s.set_defaults(fn=cmd_seed_synonyms)

    s = sub.add_parser("profile-import", help="compile profile.yaml into a saved search")
    s.add_argument("--path", default="profile.yaml")
    s.set_defaults(fn=cmd_profile_import)

    s = sub.add_parser("stats", help="sanity check your data")
    s.set_defaults(fn=cmd_stats)

    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
