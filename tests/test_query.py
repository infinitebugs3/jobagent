"""Filter -> SQL builder. No DB: assertions are on the generated SQL and params.

    python -m pytest tests/test_query.py
    python tests/test_query.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jobagent import query, search  # noqa: E402
from jobagent.query import JobFilters  # noqa: E402
from test_search import count_placeholders  # noqa: E402

SYNONYMS = {"k8s": ["kubernetes"], "rag": ["retrieval augmented generation"]}


def terms_for(text: str):
    return search.expand_terms(text, SYNONYMS)


BUILDERS = (
    query.build_job_query,
    query.build_count_query,
    query.build_review_queue_query,
)


# ---------------------------------------------------------------------------
# The invariant. Every builder, with and without every feature turned on.
# ---------------------------------------------------------------------------
def test_every_builder_balances_params_and_placeholders():
    filters = [
        JobFilters(),
        JobFilters(query_text="k8s, rag", sort="relevance"),
        JobFilters(
            query_text="k8s, rag",
            exclude_text="intern, sales",
            companies=["Stripe"],
            location_buckets=["bangalore", "remote"],
            remote_types=["remote"],
            seniorities=["mid"],
            providers=["greenhouse"],
            max_age_days=7,
            posted_within_days=14,
            label_status="unlabeled",
            has_description=True,
            title_contains="engineer",
            location_contains="bangalore",
            sort="relevance",
        ),
    ]
    for f in filters:
        t = terms_for(f.query_text)
        for build in BUILDERS:
            sql, params = build(f, t)
            assert count_placeholders(sql) == len(params), f"{build.__name__}: {sql}"
        sql, params = query.build_facets_query(f, t)
        assert count_placeholders(sql) == len(params), sql
        for dim in query.FACETS:
            sql, params = query.build_facet_query(f, dim, t)
            assert count_placeholders(sql) == len(params), f"{dim}: {sql}"


# ---------------------------------------------------------------------------
# Filters vs scoring: the load-bearing separation
# ---------------------------------------------------------------------------
def test_structured_facets_go_to_where_not_to_ordering():
    f = JobFilters(location_buckets=["bangalore"], seniorities=["mid"])
    sql, params = query.build_job_query(f, [])
    where = sql.split("where", 1)[1].split(")", 1)[0]
    assert "location_bucket = any" in sql and "seniority = any" in sql
    assert ["bangalore"] in params and ["mid"] in params
    assert "order by s.first_seen_at desc" in sql  # no scoring in the ordering


def test_keywords_go_to_ordering_and_gate_zero_scores():
    f = JobFilters(query_text="k8s", sort="relevance")
    sql, _ = query.build_job_query(f, terms_for("k8s"))
    assert "order by (s0) * s.freshness desc" in sql.replace("((s0)", "(s0)")
    assert "where (s0) > 0" in sql  # a keyword search must not return non-matches


def test_exclusions_are_a_hard_filter_on_the_title_only():
    f = JobFilters(exclude_text="intern, recruiter")
    sql, params = query.build_job_query(f, [])
    assert "not (j.title ilike %s or j.title ilike %s)" in sql
    assert "%intern%" in params and "%recruiter%" in params
    assert "description_md ilike" not in sql


def test_recall_predicate_is_present_only_with_terms():
    with_terms, _ = query.build_job_query(JobFilters(query_text="k8s"), terms_for("k8s"))
    without, _ = query.build_job_query(JobFilters(), [])
    assert "search_tsv @@" in with_terms
    assert "search_tsv @@" not in without


# ---------------------------------------------------------------------------
# Sorting
# ---------------------------------------------------------------------------
def test_relevance_falls_back_when_there_is_nothing_to_rank_by():
    assert JobFilters(sort="relevance").effective_sort([]) == "newest"
    assert JobFilters(sort="relevance").effective_sort(terms_for("k8s")) == "relevance"
    assert JobFilters(sort="nonsense").effective_sort([]) == "newest"


def test_every_declared_sort_builds():
    for sort in query.SORTS:
        sql, params = query.build_job_query(JobFilters(query_text="k8s", sort=sort), terms_for("k8s"))
        assert "order by" in sql and count_placeholders(sql) == len(params)


# ---------------------------------------------------------------------------
# Facets
# ---------------------------------------------------------------------------
def test_facet_omits_its_own_dimension_so_it_stays_widenable():
    f = JobFilters(location_buckets=["bangalore"], companies=["Stripe"])
    sql, params = query.build_facet_query(f, "location_bucket")
    assert ["bangalore"] not in params   # its own filter is lifted
    assert ["Stripe"] in params          # every other filter still applies

    sql, params = query.build_facet_query(f, "company")
    assert ["Stripe"] not in params
    assert ["bangalore"] in params


def test_combined_facets_query_covers_every_dimension_once():
    sql, _ = query.build_facets_query(JobFilters(), [])
    assert sql.count("union all") == len(query.FACETS) - 1
    assert "as materialized" in sql  # one scan, then five in-memory aggregations


def test_unknown_facet_is_rejected_loudly():
    try:
        query.build_facet_query(JobFilters(), "salary")
    except ValueError as exc:
        assert "salary" in str(exc)
    else:
        raise AssertionError("expected ValueError for an unknown facet")


# ---------------------------------------------------------------------------
# Saved searches
# ---------------------------------------------------------------------------
def test_saved_profile_fills_gaps_but_never_overrides_the_request():
    profile = {
        "query_text": "backend engineer",
        "exclude_text": "intern",
        "filters": {"location_bucket": ["bangalore"], "seniority": ["mid"]},
    }
    f = JobFilters(query_text="rag", location_buckets=["pune"])
    query.apply_search_profile(f, profile)

    assert f.query_text == "rag"                 # request wins
    assert f.location_buckets == ["pune"]        # request wins
    assert f.exclude_text == "intern"            # gap filled
    assert f.seniorities == ["mid"]              # gap filled


def test_saved_profile_tolerates_an_empty_row():
    f = JobFilters()
    query.apply_search_profile(f, {"query_text": None, "exclude_text": None, "filters": None})
    assert f.query_text == "" and f.exclude_text == "" and f.companies == []


# ---------------------------------------------------------------------------
# Review queue
# ---------------------------------------------------------------------------
def test_review_queue_collapses_duplicate_postings_of_one_role():
    sql, _ = query.build_review_queue_query(JobFilters(), [])
    assert "distinct on (j.dedupe_key)" in sql
    # within a dedupe group, prefer the posting that actually has a body
    assert "(j.description_md is not null) desc" in sql


def test_review_queue_randomises_for_the_exploration_slice():
    ranked, _ = query.build_review_queue_query(JobFilters(query_text="k8s"), terms_for("k8s"))
    explore, _ = query.build_review_queue_query(
        JobFilters(query_text="k8s"), terms_for("k8s"), randomize=True
    )
    assert "order by random()" in explore
    assert "order by random()" not in ranked


# ---------------------------------------------------------------------------
# Pagination
# ---------------------------------------------------------------------------
def test_page_and_total_come_from_a_single_query():
    sql, params = query.build_job_query(JobFilters(limit=20, offset=40), [])
    assert "count(*) over () as total_count" in sql
    assert params[-2:] == [20, 40]


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"  ok  {fn.__name__}")
    print(f"\n{len(fns)} passed")
