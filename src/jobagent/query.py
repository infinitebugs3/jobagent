"""Filters -> SQL. The single query builder behind both the CLI and the API.

Two rules hold the whole thing together:

  * **Structured facets are filters. Free text is scoring.** A location goes in
    the WHERE clause and changes *which* jobs come back. A keyword goes in the
    ORDER BY and changes *what order* they come back in. A search that quietly
    mixes the two -- a "filter" that re-ranks, or a keyword that silently
    excludes -- is a search users stop trusting.
  * Nothing here touches a database. Every function returns (sql, params), so
    the builder is testable without Postgres and the CLI and API cannot drift
    apart the way `cmd_list` and a hand-written endpoint inevitably would.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from . import search
from .search import Term

SORTS = ("relevance", "newest", "oldest", "company")

# Dimensions the UI offers as multi-select facets, mapped to their SQL column.
FACETS = {
    "company": "c.name",
    "location_bucket": "j.location_bucket",
    "remote_type": "j.remote_type",
    "seniority": "j.seniority",
    "provider": "c.ats_provider",
}

# Facet dimension -> the JobFilters attribute holding its selected values.
_FACET_ATTR = {
    "company": "companies",
    "location_bucket": "location_buckets",
    "remote_type": "remote_types",
    "seniority": "seniorities",
    "provider": "providers",
}


# The `is null` half is the whole contract of this filter and must never be
# dropped: ~1,900 postings state no bar at all, and reading silence as a
# requirement would delete a quarter of the corpus from every result.
EXPERIENCE_CEILING_SQL = "(j.min_years_experience is null or j.min_years_experience <= %s)"

# Counted like a facet and rendered in the same rail, but kept out of FACETS:
# every dimension in there is matched with `= any(%s)`, and this one is a
# cumulative threshold whose buckets the client sums.
EXPERIENCE_DIMENSION = "min_years_experience"


@dataclass
class JobFilters:
    """Everything the browse, review and digest paths can ask for."""

    # scoring
    query_text: str = ""
    exclude_text: str = ""

    # structured facets (multi-select, OR within a dimension, AND across them)
    companies: list[str] = field(default_factory=list)
    location_buckets: list[str] = field(default_factory=list)
    remote_types: list[str] = field(default_factory=list)
    seniorities: list[str] = field(default_factory=list)
    providers: list[str] = field(default_factory=list)

    # role groups (search.ROLE_GROUPS keys) -- title keyword match, a filter
    roles: list[str] = field(default_factory=list)

    # freshness. posted_within_days is what the ATS claims; max_age_days is
    # when we first saw it, which is the only meaningful one on a fresh DB.
    max_age_days: int | None = None
    posted_within_days: int | None = None

    # The highest stated years-of-experience bar still worth showing. Cumulative
    # rather than an equality facet: 3 means "3 years or fewer". A job that
    # states no bar is never hidden by it -- a quarter of the corpus says
    # nothing about years, and silence is not a requirement.
    max_years_required: int | None = None

    # labelling state: unlabeled | labeled | apply | maybe | no
    label_status: str | None = None

    has_description: bool | None = None
    include_closed: bool = False

    # 1,976 of 10,520 open postings (19%) are the same role listed again on
    # another board or under another location. Showing all of them means a
    # search for one job returns it 31 times.
    collapse_duplicates: bool = True

    # substring filters kept for CLI parity with the day-1 `list` command
    title_contains: str | None = None
    location_contains: str | None = None

    sort: str = "newest"
    limit: int = 40
    offset: int = 0

    def effective_sort(self, terms: list[Term]) -> str:
        """`relevance` is meaningless with nothing to be relevant to.

        Falls back rather than erroring: a saved profile whose terms you just
        cleared should still render a page.
        """
        if self.sort == "relevance" and not terms:
            return "newest"
        return self.sort if self.sort in SORTS else "newest"


# ---------------------------------------------------------------------------
# WHERE clause
# ---------------------------------------------------------------------------
def _where(
    f: JobFilters,
    terms: list[Term] | None = None,
    skip_facet: str | None = None,
    ceiling: bool = True,
) -> tuple[list[str], list]:
    """Build the filter clauses. `skip_facet` omits one dimension.

    That skip is what makes multi-select facet counts correct: the count shown
    next to "Bangalore" has to be computed with every filter applied *except*
    location, otherwise picking one city drives every other city to zero and
    the filter can never be widened.

    `ceiling=False` omits the experience threshold for the same reason in a
    different shape: the row builders need those rows to survive into the CTE
    so they can be counted before being dropped. See `_scored_cte`.
    """
    clauses: list[str] = []
    params: list = []

    if not f.include_closed:
        clauses.append("j.closed_at is null")

    # Recall predicate first: it is the only clause backed by the GIN indexes,
    # and it is what stops the scorer running over the whole table.
    cand_sql, cand_params = search.candidate_clause(terms or [])
    if cand_sql:
        clauses.append(cand_sql)
        params.extend(cand_params)

    def facet(name: str, column: str, values: list[str]) -> None:
        if values and skip_facet != name:
            clauses.append(f"{column} = any(%s)")
            params.append(list(values))

    if f.roles:
        role_sql, role_params = search.role_clause(f.roles)
        if role_sql:
            clauses.append(role_sql)
            params.extend(role_params)

    facet("company", "c.name", f.companies)
    facet("location_bucket", "j.location_bucket", f.location_buckets)
    facet("remote_type", "j.remote_type", f.remote_types)
    facet("seniority", "j.seniority", f.seniorities)
    facet("provider", "c.ats_provider", f.providers)

    # `is not None`, not truthiness: 0 is a real position on the slider and
    # means "entry level only", which the corpus states as '0-2 years'.
    if ceiling and f.max_years_required is not None:
        clauses.append(EXPERIENCE_CEILING_SQL)
        params.append(f.max_years_required)

    if f.max_age_days:
        clauses.append("j.first_seen_at > now() - make_interval(days => %s)")
        params.append(f.max_age_days)
    if f.posted_within_days:
        clauses.append("j.posted_at > now() - make_interval(days => %s)")
        params.append(f.posted_within_days)

    if f.has_description is True:
        clauses.append("j.description_md is not null")
    elif f.has_description is False:
        clauses.append("j.description_md is null")

    if f.label_status == "unlabeled":
        clauses.append("l.job_id is null")
    elif f.label_status == "labeled":
        clauses.append("l.job_id is not null")
    elif f.label_status in ("apply", "maybe", "no"):
        clauses.append("l.verdict = %s")
        params.append(f.label_status)

    if f.title_contains:
        clauses.append("j.title ilike %s")
        params.append(f"%{f.title_contains}%")
    # Day-1 semantics, preserved: a location filter must not hide remote roles,
    # since 'Remote' is exactly the answer to "can I work from Bangalore".
    if f.location_contains:
        clauses.append("(j.location ilike %s or j.remote_type = 'remote')")
        params.append(f"%{f.location_contains}%")

    excl_sql, excl_params = search.exclusion_clause(f.exclude_text)
    if excl_sql:
        clauses.append(excl_sql)
        params.extend(excl_params)

    return clauses or ["true"], params


_PROFILE_LISTS = (
    ("company", "companies"),
    ("location_bucket", "location_buckets"),
    ("remote_type", "remote_types"),
    ("seniority", "seniorities"),
    ("provider", "providers"),
)
_PROFILE_SCALARS = (("max_years_required", "max_years_required"),)


def apply_search_profile(f: JobFilters, profile: dict) -> JobFilters:
    """Layer a saved search under the request's own filters.

    The request always wins. A saved profile is a starting point, not a cage --
    if you explicitly ask for Pune, a profile that lists Bangalore must not
    quietly put Bangalore back.
    """
    f.query_text = f.query_text or (profile.get("query_text") or "")
    f.exclude_text = f.exclude_text or (profile.get("exclude_text") or "")

    stored = profile.get("filters") or {}
    # Split by shape rather than one polymorphic loop. `filters` is untyped
    # jsonb that a human can hand-edit, and the old single loop called
    # list(stored[key]) on whatever it found -- a scalar there became [5] or
    # raised, which is a 500 on a page load, not a validation error.
    for key, attr in _PROFILE_LISTS:
        if not getattr(f, attr) and isinstance(stored.get(key), list):
            setattr(f, attr, list(stored[key]))
    for key, attr in _PROFILE_SCALARS:
        if getattr(f, attr) is None and isinstance(stored.get(key), int):
            setattr(f, attr, int(stored[key]))
    return f


_BASE_FROM = """
      from job j
      join company c on c.id = j.company_id
 left join job_label l on l.job_id = j.id
"""

_ROW_COLUMNS = """
           j.id, j.title, j.location, j.location_bucket, j.seniority,
           j.remote_type, j.apply_url, j.dedupe_key, j.posted_at,
           j.first_seen_at, j.closed_at,
           j.min_years_experience, j.min_years_evidence,
           (j.description_md is not null) as has_description,
           c.id as company_id, c.name as company, c.ats_provider as provider,
           l.verdict, l.reason, l.reason_tags, l.source as label_source,
           date_part('day', now() - j.first_seen_at)::int as age_days
"""


def _scored_cte(f: JobFilters, terms: list[Term], columns: str = _ROW_COLUMNS) -> tuple[str, list]:
    """The inner select: rows, per-term scores, and a freshness multiplier."""
    params: list = []
    score_cols, score_params = search.scoring_columns(terms)
    params.extend(score_params)

    select_list = columns.rstrip()
    if score_cols:
        select_list += ",\n           " + ",\n           ".join(score_cols)
    select_list += f",\n           {search.freshness_sql()} as freshness"

    # The experience ceiling rides along as a flag instead of being filtered in
    # the WHERE, so the outer query can hide these rows *and* count them in the
    # same pass. Filtering here would make "N roles hidden" cost a second query.
    # Always emitted, so every builder downstream has one shape to reason about.
    if f.max_years_required is not None:
        select_list += (
            ",\n           (j.min_years_experience is not null"
            " and j.min_years_experience > %s) as over_experience_bar"
        )
        params.append(f.max_years_required)
    else:
        select_list += ",\n           false as over_experience_bar"

    clauses, where_params = _where(f, terms, ceiling=False)
    params.extend(where_params)

    # `distinct on` must lead its ORDER BY with the grouping key, so the choice
    # of *which* posting represents the role is made here, not in the outer
    # ordering: prefer one with a description, then the most recently seen.
    distinct = "distinct on (j.dedupe_key) " if f.collapse_duplicates else ""
    tail = (
        "\n     order by j.dedupe_key, (j.description_md is not null) desc, j.first_seen_at desc"
        if f.collapse_duplicates
        else ""
    )
    sql = f"select {distinct}{select_list}\n{_BASE_FROM}     where {' and '.join(clauses)}{tail}"
    return sql, params


def build_job_query(f: JobFilters, terms: list[Term] | None = None) -> tuple[str, list]:
    """Full result page: filtered, scored, explained, sorted, paginated."""
    terms = terms or []
    params: list = []

    cte_sql, cte_params = _scored_cte(f, terms)
    params.extend(cte_params)

    total = search.total_score_expr(terms)
    matched_sql, matched_params = search.matched_terms_expr(terms)

    # count(*) over () rides along on the same scan: window functions run before
    # LIMIT, so the page and its total come back from one query. Running a
    # separate count doubles the scoring work for a number shown in small grey
    # text next to the results.
    #
    # `hidden_by_experience` has to be taken one level *above* the row it
    # describes, because a window function is evaluated after its own query
    # level's WHERE -- counting it alongside the gate that removes those rows
    # would report zero, always and silently. `scored` is already materialized,
    # so the extra level costs no additional scan.
    tallied_cols = (
        f"s.*, {total} as score, "
        f"round(({total} * s.freshness)::numeric, 4) as rank_score, "
        f"{matched_sql} as matched_terms, "
        f"count(*) filter (where s.over_experience_bar) over () as hidden_by_experience"
    )
    params.extend(matched_params)

    # At least one term must fire. This is the `must(bool.should([...]))`
    # semantic: a keyword search that returns non-matching jobs is not a search.
    having = f"\n where {total} > 0" if terms else ""

    order = {
        "relevance": "order by t.rank_score desc, t.first_seen_at desc",
        "newest": "order by t.first_seen_at desc",
        "oldest": "order by t.first_seen_at asc",
        "company": "order by t.company asc, t.first_seen_at desc",
    }[f.effective_sort(terms)]

    sql = (
        f"with scored as materialized (\n{cte_sql}\n),\n"
        f"     tallied as (\n"
        f"       select {tallied_cols}\n         from scored s{having}\n     )\n"
        f"select t.*, count(*) over () as total_count\n"
        f"  from tallied t\n where not t.over_experience_bar\n {order}\n limit %s offset %s"
    )
    params.extend([f.limit, f.offset])
    return sql, params


def build_count_query(f: JobFilters, terms: list[Term] | None = None) -> tuple[str, list]:
    """Total matches, for pagination. Same filters, same score gate."""
    terms = terms or []
    cte_sql, params = _scored_cte(f, terms, columns="\n           j.id")
    total = search.total_score_expr(terms)
    gate = [f"{total} > 0"] if terms else []
    gate.append("not s.over_experience_bar")
    sql = (
        f"with scored as materialized (\n{cte_sql}\n)\n"
        f"select count(*) as n from scored s where {' and '.join(gate)}"
    )
    return sql, params


def build_facet_query(
    f: JobFilters, dimension: str, terms: list[Term] | None = None, limit: int = 200
) -> tuple[str, list]:
    """Value + count for one facet dimension, with that dimension's own filter
    lifted so the list stays widenable.

    Counts here use the recall predicate, not the exact score gate, so they can
    read slightly high while a keyword search is active. That is the right way
    to be wrong: a facet that overcounts invites a click, one that undercounts
    hides inventory.
    """
    if dimension not in FACETS:
        raise ValueError(f"unknown facet {dimension!r}; expected one of {sorted(FACETS)}")
    column = FACETS[dimension]

    clauses, params = _where(f, terms, skip_facet=dimension)
    sql = f"""
        select {column} as value, count(*) as n
        {_BASE_FROM}
         where {' and '.join(clauses)} and {column} is not null
         group by 1
         order by n desc, value asc
         limit %s
    """
    params.append(limit)
    return sql, params


def build_facets_query(f: JobFilters, terms: list[Term] | None = None):
    """Every facet dimension in one query.

    Five independent `build_facet_query` calls means five scans, five evaluations
    of the recall predicate, and five passes of scoring-adjacent work -- 2.5s on
    this corpus with a 12-term profile, which is most of a page load spent on
    the numbers in the filter rail.

    Instead: apply the expensive, dimension-independent clauses once into a
    narrow materialized CTE, then aggregate that small in-memory relation six
    times. Each branch still omits its own dimension's filter, so counts stay
    widenable.

    The experience ceiling is deliberately kept *out* of `base` and re-applied
    per branch. Inside `base` it would zero every bucket above the current
    slider position, and a filter you cannot widen from its own counts is the
    same trap `skip_facet` exists to avoid. The five equality arms still get it,
    so the rail and the header still agree.
    """
    terms = terms or []
    base_f = JobFilters(
        **{**f.__dict__, **{a: [] for a in _FACET_ATTR.values()}, "max_years_required": None}
    )
    clauses, params = _where(base_f, terms)

    # Collapse here too, or every facet count reads ~19% high against a result
    # total that was collapsed -- the filter rail and the header would disagree.
    distinct = "distinct on (j.dedupe_key) " if f.collapse_duplicates else ""
    tail = (
        "\n         order by j.dedupe_key, (j.description_md is not null) desc, j.first_seen_at desc"
        if f.collapse_duplicates
        else ""
    )
    sql = f"""
    with base as materialized (
        select {distinct}c.name as company, j.location_bucket, j.remote_type,
               j.seniority, c.ats_provider as provider, j.min_years_experience
        {_BASE_FROM}
         where {' and '.join(clauses)}{tail}
    )"""

    # Inside `base` the prefixes are gone, so the ceiling has to be restated
    # against the bare column name.
    ceiling_sql = "(min_years_experience is null or min_years_experience <= %s)"

    branches = []
    for dimension in FACETS:
        column = dimension  # inside `base` the prefixes are gone
        others, other_params = [], []
        for name, attr in _FACET_ATTR.items():
            values = getattr(f, attr)
            if values and name != dimension:
                others.append(f"{name} = any(%s)")
                other_params.append(list(values))
        if f.max_years_required is not None:
            others.append(ceiling_sql)
            other_params.append(f.max_years_required)
        where = " and ".join(others + [f"{column} is not null"])
        branches.append(
            # ::text or Postgres cannot infer the parameter's type in a UNION arm
            f"select %s::text as dimension, {column} as value, count(*) as n "
            f"from base where {where} group by 2"
        )
        params.append(dimension)
        params.extend(other_params)

    # The experience buckets. Not in FACETS: those are equality dimensions, and
    # this one is a cumulative threshold the client sums up. 'none' is carried
    # as a real row rather than dropped by an `is not null` guard -- those roles
    # are never hidden at any position, and saying so is the point.
    others, other_params = [], []
    for name, attr in _FACET_ATTR.items():
        values = getattr(f, attr)
        if values:
            others.append(f"{name} = any(%s)")
            other_params.append(list(values))
    branches.append(
        f"select %s::text as dimension, "
        f"coalesce(min_years_experience::text, 'none') as value, count(*) as n "
        f"from base{' where ' + ' and '.join(others) if others else ''} group by 2"
    )
    params.append(EXPERIENCE_DIMENSION)
    params.extend(other_params)

    sql += "\n" + "\nunion all\n".join(branches) + "\norder by dimension, n desc, value asc"
    return sql, params


def build_review_queue_query(
    f: JobFilters, terms: list[Term] | None = None, randomize: bool = False
) -> tuple[str, list]:
    """The labelling queue: one card per *role*, not per posting.

    Two things make this different from the browse query.

    `distinct on (dedupe_key)` collapses the ~2,000 duplicate postings, so you
    never burn a label on a job you already judged under another board's URL.
    Within a group it prefers the row that actually has a description -- 28% of
    postings have no body, and labelling those is guesswork, not judgement.

    `randomize` powers the exploration slice. Labelling only what the search
    ranks highly produces an eval set selected by the very thing it is meant to
    evaluate; the random slice is what tells you which good jobs your profile
    is silently missing.
    """
    terms = terms or []
    params: list = []

    f.collapse_duplicates = True  # non-negotiable here, whatever the caller asked
    cte_sql, cte_params = _scored_cte(f, terms)
    params.extend(cte_params)

    total = search.total_score_expr(terms)
    matched_sql, matched_params = search.matched_terms_expr(terms)

    # Same shape as the browse page, and for the same reason: the count of what
    # the ceiling removed has to be taken a level above the gate that removes
    # it, or the window function runs after the WHERE and reports zero forever.
    tallied_cols = (
        f"s.*, {total} as score, "
        f"round(({total} * s.freshness)::numeric, 4) as rank_score, "
        f"{matched_sql} as matched_terms, "
        f"count(*) filter (where s.over_experience_bar) over () as hidden_by_experience"
    )
    params.extend(matched_params)

    having = f"\n where {total} > 0" if terms else ""
    # Descriptions first, ahead of relevance. A card with no job description is
    # nearly worthless as a label whatever it scores -- you would be guessing,
    # and a guessed label is worse than no label because it silently corrupts
    # the eval set. 7,595 of 10,520 postings have a body; label those first.
    if randomize:
        order = "order by t.has_description desc, random()"
    elif terms:
        order = "order by t.has_description desc, t.rank_score desc, t.first_seen_at desc"
    else:
        order = "order by t.has_description desc, t.first_seen_at desc"

    sql = (
        f"with scored as materialized (\n{cte_sql}\n),\n"
        f"     tallied as (\n"
        f"       select {tallied_cols}\n         from scored s{having}\n     )\n"
        f"select t.*\n  from tallied t\n where not t.over_experience_bar\n"
        f" {order}\n limit %s offset %s"
    )
    params.extend([f.limit, f.offset])
    return sql, params
