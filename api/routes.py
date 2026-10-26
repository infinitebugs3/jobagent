"""HTTP surface. Every read goes through jobagent.query, so the API and the CLI
cannot drift apart.
"""

from __future__ import annotations

import random
import time

from fastapi import APIRouter, Depends, HTTPException, Query

from jobagent import db, query, search

from . import deps, schemas

router = APIRouter(prefix="/api")

REVIEW_TARGET = 80  # README day 6: 50-80 hand labels

# Share of the review queue drawn at random rather than from the ranked list.
# This is not a rounding detail. Labelling only what the search ranks highly
# produces an eval set selected by the thing it is meant to evaluate, and a
# profile that silently excludes a whole good category stays invisible forever.
EXPLORATION_RATE = 0.3


# ---------------------------------------------------------------------------
# Filter parsing
# ---------------------------------------------------------------------------
def _filters(
    q: str = "",
    profile_id: int | None = None,
    role: list[str] = Query(default=[]),
    company: list[str] = Query(default=[]),
    location: list[str] = Query(default=[]),
    remote: list[str] = Query(default=[]),
    seniority: list[str] = Query(default=[]),
    provider: list[str] = Query(default=[]),
    max_age_days: int | None = None,
    max_years_required: int | None = None,
    label_status: str | None = None,
    has_description: bool | None = None,
    exclude: str = "",
    sort: str = "relevance",
    page: int = 1,
    page_size: int = 25,
) -> query.JobFilters:
    page = max(1, page)
    page_size = min(max(1, page_size), 100)
    return query.JobFilters(
        query_text=q,
        exclude_text=exclude,
        roles=role,
        companies=company,
        location_buckets=location,
        remote_types=remote,
        seniorities=seniority,
        providers=provider,
        max_age_days=max_age_days,
        max_years_required=max_years_required,
        label_status=label_status,
        has_description=has_description,
        sort=sort,
        limit=page_size,
        offset=(page - 1) * page_size,
    )


def _resolve(c, f: query.JobFilters, profile_id: int | None):
    """Layer a saved search under the request, then expand terms."""
    if profile_id:
        profile = db.get_search_profile(c, profile_id)
        if profile is None:
            raise HTTPException(404, f"no search profile {profile_id}")
        query.apply_search_profile(f, profile)
    return deps.expand(c, f.query_text)


def _cache_key(f: query.JobFilters) -> tuple:
    d = dict(f.__dict__)
    for volatile in ("limit", "offset", "sort"):
        d.pop(volatile, None)
    return tuple(sorted((k, tuple(v) if isinstance(v, list) else v) for k, v in d.items()))


# ---------------------------------------------------------------------------
# Jobs
# ---------------------------------------------------------------------------
def _rows(records) -> list[schemas.JobRow]:
    out = []
    for r in records:
        d = dict(r)
        d["reason_tags"] = d.get("reason_tags") or []
        d["matched_terms"] = d.get("matched_terms") or []
        out.append(schemas.JobRow(**{k: v for k, v in d.items() if k in schemas.JobRow.model_fields}))
    return out


@router.get("/jobs", response_model=schemas.JobPage)
def list_jobs(
    f: query.JobFilters = Depends(_filters),
    profile_id: int | None = None,
):
    started = time.perf_counter()
    with deps.conn() as c:
        terms = _resolve(c, f, profile_id)
        rows = c.execute(*query.build_job_query(f, terms)).fetchall()

    # total_count rides along on the same scan (window function), so an empty
    # page is the only case that needs to be reasoned about separately.
    total = rows[0]["total_count"] if rows else 0
    # Rides along the same way. On an empty page there is no row to read it
    # from -- the client keeps showing the ceiling it asked for, so the filter
    # is still visible even when it has hidden everything.
    hidden = rows[0]["hidden_by_experience"] if rows else 0
    return schemas.JobPage(
        items=_rows(rows),
        total=total,
        hidden_by_experience=hidden,
        page=f.offset // f.limit + 1,
        page_size=f.limit,
        terms=[t.raw for t in terms],
        took_ms=int((time.perf_counter() - started) * 1000),
    )


@router.get("/facets", response_model=schemas.Facets)
def get_facets(f: query.JobFilters = Depends(_filters), profile_id: int | None = None):
    with deps.conn() as c:
        terms = _resolve(c, f, profile_id)
        key = _cache_key(f)
        cached = deps.facets_cache.get(key)
        if cached is None:
            rows = c.execute(*query.build_facets_query(f, terms)).fetchall()
            # Seeded from FACETS plus the experience buckets, which are counted
            # like a facet but are not one -- see query.EXPERIENCE_DIMENSION.
            grouped: dict[str, list] = {d: [] for d in query.FACETS}
            grouped[query.EXPERIENCE_DIMENSION] = []
            for r in rows:
                grouped[r["dimension"]].append(schemas.FacetValue(value=r["value"], n=r["n"]))
            cached = deps.facets_cache.set(key, grouped)
    return schemas.Facets(**cached)


@router.get("/review/queue", response_model=schemas.JobPage)
def review_queue(
    f: query.JobFilters = Depends(_filters),
    profile_id: int | None = None,
    explore: bool | None = None,
):
    """One card per role, unlabeled, ranked by the active search.

    `explore` forces the random slice; leaving it unset lets the server draw it
    at EXPLORATION_RATE, so the caller does not have to remember to.
    """
    f.label_status = "unlabeled"
    randomize = random.random() < EXPLORATION_RATE if explore is None else explore

    started = time.perf_counter()
    with deps.conn() as c:
        terms = _resolve(c, f, profile_id)
        rows = c.execute(*query.build_review_queue_query(f, terms, randomize=randomize)).fetchall()

    items = _rows(rows)
    # The client echoes this back as job_label.source, which is what keeps the
    # eval set and the exploration set distinguishable later.
    for it in items:
        it.label_source = "exploration" if randomize else "review"
    return schemas.JobPage(
        items=items,
        total=len(items),
        # Counted across the whole unlabeled queue, not this page of it -- the
        # answer to "how much of my 80-label budget is the ceiling saving me".
        hidden_by_experience=rows[0]["hidden_by_experience"] if rows else 0,
        page=1,
        page_size=f.limit,
        terms=[t.raw for t in terms],
        took_ms=int((time.perf_counter() - started) * 1000),
    )


@router.get("/search-profiles/{profile_id}/preview", response_model=schemas.ProfilePreview)
def preview_profile(profile_id: int):
    f = query.JobFilters(sort="relevance", limit=5)
    with deps.conn() as c:
        terms = _resolve(c, f, profile_id)
        rows = c.execute(*query.build_job_query(f, terms)).fetchall()
    return schemas.ProfilePreview(
        total=rows[0]["total_count"] if rows else 0,
        terms=[t.raw for t in terms],
        sample=_rows(rows),
    )


@router.get("/jobs/{job_id}", response_model=schemas.JobDetail)
def get_job(job_id: int):
    with deps.conn() as c:
        row = c.execute(
            """
            select j.*, (j.description_md is not null) as has_description,
                   c.id as company_id, c.name as company, c.ats_provider as provider,
                   c.careers_url,
                   l.verdict, l.reason, l.reason_tags, l.source as label_source,
                   date_part('day', now() - j.first_seen_at)::int as age_days
              from job j
              join company c on c.id = j.company_id
         left join job_label l on l.job_id = j.id
             where j.id = %s
            """,
            (job_id,),
        ).fetchone()
    if row is None:
        raise HTTPException(404, f"no job {job_id}")
    row["reason_tags"] = row.get("reason_tags") or []
    return schemas.JobDetail(**row)


# ---------------------------------------------------------------------------
# Facets and roles
# ---------------------------------------------------------------------------
@router.get("/roles", response_model=list[schemas.RoleGroup])
def list_roles():
    """The role filter's vocabulary, served rather than hardcoded in the client
    so both ends can never disagree about what 'Backend' means."""
    return [
        schemas.RoleGroup(slug=slug, label=group["label"])
        for slug, group in search.ROLE_GROUPS.items()
    ]


# ---------------------------------------------------------------------------
# Labels
# ---------------------------------------------------------------------------
@router.post("/labels", response_model=schemas.LabelOut)
def create_label(payload: schemas.LabelIn):
    with deps.conn() as c:
        try:
            n = db.label_job(
                c,
                payload.job_id,
                payload.verdict,
                payload.reason,
                payload.source,
                payload.reason_tags,
            )
        except ValueError as exc:
            raise HTTPException(404, str(exc))
        c.commit()
    return schemas.LabelOut(job_id=payload.job_id, verdict=payload.verdict, postings_labeled=n)


@router.delete("/labels/{job_id}")
def delete_label(job_id: int):
    with deps.conn() as c:
        n = db.unlabel_job(c, job_id)
        c.commit()
    return {"job_id": job_id, "postings_cleared": n}


@router.get("/review/progress", response_model=schemas.ReviewProgress)
def review_progress():
    with deps.conn() as c:
        # Count distinct roles, not postings: labelling one job writes a row for
        # every duplicate posting of it, so counting rows would inflate progress.
        row = c.execute(
            """
            select count(*) as labeled,
                   count(*) filter (where verdict = 'apply')      as apply,
                   count(*) filter (where verdict = 'maybe')      as maybe,
                   count(*) filter (where verdict = 'no')         as no,
                   count(*) filter (where source  = 'review')     as review,
                   count(*) filter (where source  = 'exploration') as exploration
              from (select distinct on (coalesce(l.dedupe_key, l.job_id::text))
                           l.verdict, l.source
                      from job_label l
                     order by coalesce(l.dedupe_key, l.job_id::text)) t
            """
        ).fetchone()
    return schemas.ReviewProgress(target=REVIEW_TARGET, **row)


# ---------------------------------------------------------------------------
# Saved searches
# ---------------------------------------------------------------------------
@router.get("/search-profiles", response_model=list[schemas.SearchProfileOut])
def list_profiles():
    with deps.conn() as c:
        return [schemas.SearchProfileOut(**r) for r in db.list_search_profiles(c)]


@router.post("/search-profiles", response_model=schemas.SearchProfileOut)
def create_profile(payload: schemas.SearchProfileIn):
    with deps.conn() as c:
        pid = db.upsert_search_profile(c, **payload.model_dump())
        c.commit()
    deps.invalidate_caches()
    return schemas.SearchProfileOut(id=pid, **payload.model_dump())


@router.patch("/search-profiles/{profile_id}", response_model=schemas.SearchProfileOut)
def update_profile(profile_id: int, payload: schemas.SearchProfileIn):
    with deps.conn() as c:
        if db.get_search_profile(c, profile_id) is None:
            raise HTTPException(404, f"no search profile {profile_id}")
        db.upsert_search_profile(c, profile_id=profile_id, **payload.model_dump())
        c.commit()
    deps.invalidate_caches()
    return schemas.SearchProfileOut(id=profile_id, **payload.model_dump())


@router.delete("/search-profiles/{profile_id}")
def remove_profile(profile_id: int):
    with deps.conn() as c:
        n = db.delete_search_profile(c, profile_id)
        c.commit()
    deps.invalidate_caches()
    if not n:
        raise HTTPException(404, f"no search profile {profile_id}")
    return {"deleted": profile_id}


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------
@router.get("/stats")
def stats():
    queries = {
        "companies": "select count(*) n from company where ats_provider <> 'unknown'",
        "open_jobs": "select count(*) n from job where closed_at is null",
        "distinct_roles": "select count(distinct dedupe_key) n from job where closed_at is null",
        "with_description": "select count(*) n from job where closed_at is null and description_md is not null",
        "posted_last_7d": "select count(*) n from job where posted_at > now() - interval '7 days'",
        "labeled_postings": "select count(*) n from job_label",
        "labeled_roles": "select count(distinct coalesce(dedupe_key, job_id::text)) n from job_label",
    }
    out = {}
    with deps.conn() as c:
        for key, sql in queries.items():
            out[key] = c.execute(sql).fetchone()["n"]
        out["by_provider"] = [
            dict(r)
            for r in c.execute(
                """select c.ats_provider as provider, count(j.id) as n
                     from company c left join job j
                       on j.company_id = c.id and j.closed_at is null
                    group by 1 order by 2 desc"""
            ).fetchall()
        ]
    return out
