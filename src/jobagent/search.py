"""Keyword search: term explosion, synonym expansion, tiered scoring.

Ported from the TenderDekho search shape (Elasticsearch) onto Postgres. The
ideas that carry over:

  * one user term explodes into several weighted clauses, and an *exact* hit is
    given a score no amount of fuzzy matching can reach. TenderDekho does this
    with a `constant_score` boost of 1000; here it is a plain CASE returning
    `W_EXACT`. Same property: exact always sorts above approximate.
  * terms are OR'd and their scores summed, so a job matching three of your
    five terms outranks one matching a single term.
  * structured facets are filters (no scoring), free text is scoring. Mixing
    the two is what makes a search feel arbitrary.

What does *not* carry over is Elasticsearch. At 10.5k documents a GIN index on
a generated tsvector answers in single-digit milliseconds and costs nothing to
operate. Revisit past ~1M docs.

Everything here is a pure function returning (sql, params). No IO, so all of it
is unit-testable without a database.
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from pathlib import Path

# Scoring tiers. The gap between them is the point: no quantity of tier-3
# matches can outweigh one tier-1 match, so "Senior Backend Engineer" always
# beats a job that merely mentions backends somewhere in its description.
W_EXACT = 100.0   # the term appears verbatim in the title
W_FTS = 10.0      # stemmed full-text hit, weighted A=title > B=location > C=body
W_FUZZY = 3.0     # trigram similarity -- typos and partial tokens

# Below this, trigram similarity is noise. 0.45 catches 'kuberentes' (0.47
# against a real Kubernetes title) while rejecting unrelated words.
#
# This same number is pushed into `pg_trgm.word_similarity_threshold` by
# db.connect(), because the `<%` operator used for index-backed candidate
# selection reads its cutoff from that GUC. If the two ever disagree, the
# recall predicate stops being a superset of the score predicate and matches
# vanish silently -- the worst kind of search bug.
FUZZY_FLOOR = 0.45

# ts_rank_cd weights are {D, C, B, A}. Title (A) dominates by design.
TS_WEIGHTS = "{0.1, 0.2, 0.4, 1.0}"

# Freshness multiplier. README is explicit that a 70% match posted yesterday
# beats a 90% match from six weeks ago. The floor of 0.4 means age *discounts*
# relevance but never erases it -- a perfect match from last quarter should
# still be reachable, just not top of the page.
FRESHNESS_FLOOR = 0.4
FRESHNESS_HALFLIFE_DAYS = 30.0

MAX_TERMS = 12  # a query with more terms than this is a profile, not a search


# ---------------------------------------------------------------------------
# Role groups
# ---------------------------------------------------------------------------
# The simplest role filter that needs no new pipeline: match the title against a
# curated keyword group. Titles are always present (unlike descriptions, missing
# on 28% of rows) and they are what a role actually *is*.
#
# This is a FILTER, not a scoring term -- picking "Backend" narrows the result
# set, it does not merely reorder it. Keeping that distinction is why the search
# stays predictable.
#
# Groups deliberately overlap ('Platform Engineer' is both backend and infra).
# A job matching any selected group is kept; there is no attempt at exclusive
# classification, which is the thing that needs a real classifier (phase 3).
ROLE_GROUPS: dict[str, dict] = {
    "backend": {
        "label": "Backend",
        "patterns": ["backend", "back end", "back-end", "server side", "api engineer",
                     "distributed systems", "software engineer", "software development engineer",
                     "member of technical staff"],
    },
    "ai_ml": {
        "label": "AI / ML",
        "patterns": ["machine learning", "ml", "ai", "applied ai", "artificial intelligence",
                     "llm", "genai", "generative", "nlp", "deep learning",
                     "research engineer", "computer vision", "data scientist"],
    },
    "infra": {
        "label": "Infra / Platform",
        "patterns": ["infrastructure", "platform engineer", "devops", "sre",
                     "site reliability", "cloud engineer", "kubernetes", "systems engineer"],
    },
    "data": {
        "label": "Data",
        "patterns": ["data engineer", "data platform", "analytics engineer", "etl",
                     "data infrastructure", "database engineer"],
    },
    "frontend": {
        "label": "Frontend",
        "patterns": ["frontend", "front end", "front-end", "ui engineer", "web engineer",
                     "react", "javascript engineer"],
    },
    "fullstack": {
        "label": "Full stack",
        "patterns": ["full stack", "fullstack", "full-stack"],
    },
    "mobile": {
        "label": "Mobile",
        "patterns": ["android", "ios", "mobile engineer", "react native", "flutter"],
    },
    "security": {
        "label": "Security",
        "patterns": ["security engineer", "appsec", "infosec", "cryptography",
                     "security architect"],
    },
    "qa": {
        "label": "QA / Test",
        "patterns": ["qa engineer", "test engineer", "sdet", "quality engineer",
                     "automation engineer"],
    },
    "management": {
        "label": "Management",
        "patterns": ["engineering manager", "director", "head of", "vp", "principal engineer",
                     "staff engineer", "tech lead", "technical lead"],
    },
}

# Punctuation to spaces, then single-space padded. Turns 'Engineer-ML, Sr.'
# into ' engineer ml sr ' so patterns can anchor on word starts.
_ROLE_TITLE = "(' ' || regexp_replace(lower({alias}.title), '[^a-z0-9]+', ' ', 'g') || ' ')"


def role_clause(roles: list[str], alias: str = "j") -> tuple[str, list]:
    """Hard filter: keep jobs whose title matches any pattern of any chosen role.

    Patterns match at a **word start** and may run on: ' llm' matches "LLM",
    "LLMs" and "LLMOps" but not "Fu*lfilLM*ent". That distinction is not
    hypothetical -- a naive `%llm%` pulled every Amazon fulfilment-centre role
    into the AI/ML filter, which is exactly the kind of quiet wrongness that
    makes people stop trusting a filter.

    A trailing boundary is deliberately not required, so 'ai engineer' still
    matches "AI Engineering".
    """
    patterns: list[str] = []
    for slug in roles:
        group = ROLE_GROUPS.get(slug)
        if group:
            patterns.extend(group["patterns"])
    if not patterns:
        return "", []

    title = _ROLE_TITLE.format(alias=alias)
    parts = [f"{title} like %s" for _ in patterns]
    return "(" + " or ".join(parts) + ")", [f"% {p}%" for p in patterns]


@dataclass
class Term:
    """One user-supplied term plus everything it should also match."""

    raw: str
    variants: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        # The raw term always searches for itself; synonyms are additive.
        seen = dict.fromkeys([self.raw] + self.variants)
        self.variants = list(seen)


# ---------------------------------------------------------------------------
# Parsing and expansion
# ---------------------------------------------------------------------------
def parse_terms(query_text: str) -> list[str]:
    """Split on commas. Each chunk is one term, and a multi-word chunk stays a
    phrase.

    Commas rather than spaces, because 'spring boot' has to survive as a unit.
    The UI says so in the placeholder, and profile-import emits commas.
    """
    if not query_text:
        return []
    chunks = (c.strip().lower() for c in query_text.split(","))
    # dict.fromkeys dedupes while preserving the order you typed them in
    return list(dict.fromkeys(c for c in chunks if c))[:MAX_TERMS]


def expand_terms(query_text: str, synonyms: dict[str, list[str]] | None = None) -> list[Term]:
    """Explode each term into its synonym variants.

    Done server-side, unlike TenderDekho, which explodes in the frontend and
    stores the result -- meaning every saved search froze the synonym table as
    it was on the day it was saved. Expanding at query time means fixing an
    alias fixes every existing saved search at once.
    """
    synonyms = synonyms or {}
    return [Term(raw=t, variants=list(synonyms.get(t, []))) for t in parse_terms(query_text)]


def load_synonyms_csv(path: str = "data/synonyms.csv") -> list[tuple[str, list[str]]]:
    """Read the curated alias file. `#` comments and blank rows are skipped."""
    fh = Path(path)
    if not fh.is_file():
        raise FileNotFoundError(f"{path} not found")
    out: list[tuple[str, list[str]]] = []
    lines = [
        ln for ln in fh.read_text(encoding="utf-8").splitlines()
        if ln.strip() and not ln.lstrip().startswith("#")
    ]
    for row in csv.DictReader(lines):
        term = (row.get("term") or "").strip().lower()
        raw = (row.get("expansions") or "").strip().lower()
        if not term or not raw:
            continue
        out.append((term, [e.strip() for e in raw.split("|") if e.strip()]))
    return out


# ---------------------------------------------------------------------------
# SQL generation
# ---------------------------------------------------------------------------
_ERE_META = set(r".^$*+?()[]{}|\\")


def _ere_escape(text: str) -> str:
    """Escape POSIX ERE metacharacters. 'c++' and '.net' are real search terms."""
    return "".join("\\" + ch if ch in _ERE_META else ch for ch in text)


def word_start_pattern(text: str) -> str:
    r"""`\m` anchors at a word start, so a term matches whole words and their
    suffixes but never the middle of another word.

    Without this, `%rag%` matches "Beve**rag**e" and a Food & Beverage Team
    Leader scores 100 on a search for RAG -- outranking every genuine hit,
    because the exact tier is the highest one there is. Postgres indexes `~*`
    with gin_trgm_ops just as it does ILIKE, so the anchor is free.
    """
    return r"\m" + _ere_escape(text)


def _websearch_query(term: Term) -> str:
    """Build a websearch_to_tsquery string OR-ing every variant.

    websearch_to_tsquery is used rather than to_tsquery precisely because it
    cannot raise on malformed input -- a user typing `c++ (` gets no results,
    not a 500.
    """
    return " OR ".join(f'"{v}"' for v in term.variants)


def term_score_sql(term: Term, alias: str = "j") -> tuple[str, list]:
    """Score expression for one term. Tiers, best-of -- not summed.

    `greatest` rather than a sum so the weights stay interpretable: an exact
    title hit is worth exactly W_EXACT, never W_EXACT + W_FTS + W_FUZZY. Two
    jobs that both nail the same term then tie, and freshness breaks the tie.
    """
    params: list = []

    exact_parts = []
    for variant in term.variants:
        exact_parts.append(f"{alias}.title ~* %s")
        params.append(word_start_pattern(variant))
    exact = f"case when ({' or '.join(exact_parts)}) then {W_EXACT} else 0.0 end"

    # Gate the rank behind the match test. `@@` is an indexable boolean;
    # ts_rank_cd has to walk the whole tsvector, and a job description tsvector
    # runs to thousands of lexemes. Most candidates fail most terms, so the CASE
    # skips the expensive half almost every time -- this one change takes the
    # default profile view from ~1.6s to well under 200ms.
    ws = _websearch_query(term)
    fts = (
        f"case when {alias}.search_tsv @@ websearch_to_tsquery('english', %s) "
        f"then {W_FTS} * ts_rank_cd('{TS_WEIGHTS}', {alias}.search_tsv, "
        f"websearch_to_tsquery('english', %s)) else 0.0 end"
    )
    params.extend([ws, ws])

    # word_similarity, not similarity: plain similarity compares the term to the
    # *whole* title, so 'kubernetes' against 'Platform Engineer - Kubernetes'
    # scores ~0.3 and the tier never fires. word_similarity scores the term
    # against the best-matching span, which is the question actually being asked.
    #
    # Computed against the raw term only: a synonym list exists for recall, and
    # fuzzing a synonym of a synonym is just noise.
    # `<%` rather than comparing word_similarity() to FUZZY_FLOOR by hand: it is
    # the same operator the recall predicate uses, reading the same GUC, so the
    # two cannot drift apart. It is also cheaper in the common false case.
    fuzzy = (
        f"case when %s <%% {alias}.title "
        f"then {W_FUZZY} * word_similarity(%s, {alias}.title) else 0.0 end"
    )
    params.extend([term.raw, term.raw])

    return f"greatest({exact}, {fts}, {fuzzy})", params


def candidate_clause(terms: list[Term], alias: str = "j") -> tuple[str, list]:
    """Index-backed recall predicate: "could this row score above zero?"

    Without this the planner has no choice but to compute three tiers of scoring
    for all 10,520 rows and sort the result -- a ~700ms sequential scan. Every
    branch here is answerable from an index (GIN on search_tsv, GIN trigram on
    title), so the planner turns the whole thing into a bitmap OR and scores
    only the survivors.

    This must stay a strict *superset* of what `term_score_sql` scores above
    zero. It is allowed to let through rows that end up scoring 0 -- the outer
    `where score > 0` removes them. It must never exclude a row that would have
    scored.
    """
    if not terms:
        return "", []

    parts, params = [], []
    for term in terms:
        parts.append(f"{alias}.search_tsv @@ websearch_to_tsquery('english', %s)")
        params.append(_websearch_query(term))
        for variant in term.variants:
            parts.append(f"{alias}.title ~* %s")
            params.append(word_start_pattern(variant))
        # `<%` reads its cutoff from pg_trgm.word_similarity_threshold, which
        # db.connect() pins to FUZZY_FLOOR. %% is a literal percent for psycopg.
        parts.append(f"%s <%% {alias}.title")
        params.append(term.raw)

    return "(" + " or ".join(parts) + ")", params


def freshness_sql(alias: str = "j") -> str:
    """Exponential decay on age, floored so old-but-perfect stays reachable.

    posted_at is what the ATS claims; first_seen_at is when we actually saw it.
    posted_at is preferred and first_seen_at is the fallback, because on a first
    sync every row looks 'first seen today' and freshness would be meaningless.
    """
    age = (
        f"greatest(0, extract(epoch from now() - "
        f"coalesce({alias}.posted_at, {alias}.first_seen_at)) / 86400.0)"
    )
    return f"({FRESHNESS_FLOOR} + {1 - FRESHNESS_FLOOR} * exp(-({age}) / {FRESHNESS_HALFLIFE_DAYS}))"


def scoring_columns(terms: list[Term], alias: str = "j") -> tuple[list[str], list]:
    """Per-term score columns `s0..sN`, for the inner select of the search CTE."""
    cols, params = [], []
    for i, term in enumerate(terms):
        sql, p = term_score_sql(term, alias)
        cols.append(f"{sql} as s{i}")
        params.extend(p)
    return cols, params


def total_score_expr(terms: list[Term]) -> str:
    """Sum across terms: 3-of-5 outranks 1-of-5."""
    if not terms:
        return "0.0"
    return "(" + " + ".join(f"s{i}" for i in range(len(terms))) + ")"


def matched_terms_expr(terms: list[Term]) -> tuple[str, list]:
    """Which terms fired, as text[].

    This is TenderDekho's `matchReasons[]` for free -- the UI renders it as
    chips so "why is this job here" never needs a second round trip.
    """
    if not terms:
        return "'{}'::text[]", []
    cases, params = [], []
    for i, term in enumerate(terms):
        cases.append(f"case when s{i} > 0 then %s end")
        params.append(term.raw)
    return f"array_remove(array[{', '.join(cases)}], null)", params


def exclusion_clause(exclude_text: str, alias: str = "j") -> tuple[str, list]:
    """Hard must-not on the title. Cheapest, highest-precision quality lever.

    Applied to the title only, never the description: 'we are not a consultancy'
    in a body paragraph should not disqualify a job from the word 'consultant'.
    """
    terms = parse_terms(exclude_text)
    if not terms:
        return "", []
    # Word-anchored for the same reason as the exact tier, and the stakes are
    # higher: an exclusion is a hard filter, so `sales` matching "Whole*sale*s"
    # deletes jobs from the result set with nothing on screen to explain it.
    parts = [f"{alias}.title ~* %s" for _ in terms]
    return f"not ({' or '.join(parts)})", [word_start_pattern(t) for t in terms]
