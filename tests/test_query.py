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
            max_years_required=5,
            sort="relevance",
        ),
        # 0 is a real slider position ('entry level only'), and every truthiness
        # check between here and the URL would silently drop it.
        JobFilters(max_years_required=0),
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
    assert "order by t.first_seen_at desc" in sql  # no scoring in the ordering


def test_keywords_go_to_ordering_and_gate_zero_scores():
    f = JobFilters(query_text="k8s", sort="relevance")
    sql, _ = query.build_job_query(f, terms_for("k8s"))
    cte, outer = sql.split("\n),\n", 1)  # the CTE body, then everything after it
    assert "order by t.rank_score desc" in outer
    assert "where (s0) > 0" in outer  # a keyword search must not return non-matches
    # Scoring belongs in the outer ordering, never in the inner filtering:
    # the CTE decides *which* rows exist, the outer decides their order.
    assert "s0" not in cte.split(" where ", 1)[1]


def test_exclusions_are_a_hard_filter_on_the_title_only():
    f = JobFilters(exclude_text="intern, recruiter")
    sql, params = query.build_job_query(f, [])
    assert "not (j.title ~* %s or j.title ~* %s)" in sql
    # word-anchored: excluding "sales" must not delete "Wholesales"
    assert "\\mintern" in params and "\\mrecruiter" in params
    # Titles only. "we are not a consultancy" in a body paragraph must not
    # disqualify a job from the exclusion term "consultant".
    excl, _ = search.exclusion_clause("intern, recruiter")
    assert "description_md" not in excl


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
# Experience ceiling
# ---------------------------------------------------------------------------
def test_experience_ceiling_never_hides_jobs_that_state_no_bar():
    """A quarter of the corpus states no bar. Reading silence as a requirement
    would delete all of it from every result at every slider position.

    It appears in two forms, and both have to stay null-safe. The row builders
    carry it as a flag to be negated later ('is not null AND > n', so NULL is
    false and the row survives); everything going through _where states it
    positively ('is null OR <= n').
    """
    for build in BUILDERS:
        sql, _ = build(JobFilters(max_years_required=3), [])
        assert "min_years_experience is not null and" in sql, build.__name__

    sql, _ = query.build_facet_query(JobFilters(max_years_required=3), "seniority")
    assert "min_years_experience is null or" in sql
    sql, _ = query.build_facets_query(JobFilters(max_years_required=3), [])
    assert "min_years_experience is null or" in sql


def test_experience_ceiling_is_a_filter_not_a_ranking_tweak():
    """It changes which roles come back, never what order they arrive in --
    the separation the whole query layer is built on.

    rsplit, not split: the first `order by` in the SQL is the distinct-on tail
    inside the CTE. The outer ordering is the last one.
    """
    for sort in ("newest", "relevance"):
        sql, _ = query.build_job_query(JobFilters(max_years_required=3, sort=sort), [])
        assert "over_experience_bar" not in sql.rsplit("order by", 1)[1]
        assert "over_experience_bar" in sql


def test_hidden_count_is_taken_above_the_gate():
    """A window function is evaluated after its own level's WHERE. Counting the
    hidden rows alongside the clause that drops them reports zero -- always,
    and silently. The count has to live one level up.
    """
    for build in (query.build_job_query, query.build_review_queue_query):
        sql, _ = build(JobFilters(max_years_required=3), [])
        counted = sql.index("filter (where s.over_experience_bar)")
        dropped = sql.index("where not t.over_experience_bar")
        assert counted < dropped, build.__name__


def test_experience_ceiling_of_zero_is_not_dropped():
    """'Entry level only' is a real ask. Every truthiness check between the
    slider and the SQL is a place where 0 silently becomes 'no filter'."""
    sql, params = query.build_job_query(JobFilters(max_years_required=0), [])
    assert 0 in params
    assert "min_years_experience >" in sql

    sql, params = query.build_facet_query(JobFilters(max_years_required=0), "seniority")
    assert 0 in params
    assert "min_years_experience <=" in sql


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
    # The five equality facets plus the experience buckets.
    assert sql.count("union all") == len(query.FACETS)
    assert "as materialized" in sql  # one scan, then six in-memory aggregations


def test_experience_buckets_are_counted_without_their_own_ceiling():
    """The counts under the slider have to be computed as if the slider were
    off, or every bucket above the current position reads zero and the filter
    can never be widened from its own numbers.

    The five equality facets must still respect it, or the rail disagrees with
    the header total.
    """
    sql, params = query.build_facets_query(JobFilters(max_years_required=3), [])
    arms = sql.split("union all")
    experience = [a for a in arms if "coalesce(min_years_experience" in a]
    assert len(experience) == 1
    assert "min_years_experience <=" not in experience[0]
    for arm in arms:
        if arm not in experience and "select %s::text" in arm:
            assert "min_years_experience <=" in arm, arm
    # ...and never inside `base`, which every arm reads from.
    assert "min_years_experience <=" not in sql.split("union all")[0].split(")")[0]


def test_experience_buckets_keep_the_jobs_that_state_no_bar():
    """Those roles are visible at every slider position, so the count that says
    so has to survive the `is not null` guard the other dimensions use."""
    sql, _ = query.build_facets_query(JobFilters(), [])
    experience = [a for a in sql.split("union all") if "coalesce(min_years_experience" in a][0]
    assert "'none'" in experience
    assert "min_years_experience is not null" not in experience


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


def test_saved_profile_merges_a_scalar_filter():
    f = JobFilters()
    query.apply_search_profile(f, {"filters": {"max_years_required": 5}})
    assert f.max_years_required == 5

    explicit = JobFilters(max_years_required=2)
    query.apply_search_profile(explicit, {"filters": {"max_years_required": 5}})
    assert explicit.max_years_required == 2   # request wins, as everywhere else


def test_saved_profile_ignores_a_filter_of_the_wrong_shape():
    """`filters` is untyped jsonb a human can hand-edit. A scalar under a list
    key used to reach list(...) and 500 a page load; it has to be inert."""
    f = JobFilters()
    query.apply_search_profile(f, {"filters": {"seniority": 3, "max_years_required": ["5"]}})
    assert f.seniorities == []
    assert f.max_years_required is None


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
    assert "random()" in explore
    assert "random()" not in ranked
    # descriptions first in both: a card with no JD cannot be judged either way
    assert explore.count("has_description desc") and ranked.count("has_description desc")


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
