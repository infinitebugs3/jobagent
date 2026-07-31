"""Search term handling and SQL generation. No DB, no network -- run it anywhere.

    python -m pytest tests/test_search.py
    python tests/test_search.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jobagent import search  # noqa: E402
from jobagent.search import Term  # noqa: E402

# A literal percent is written %% and must not be counted as a placeholder.
_PLACEHOLDER = re.compile(r"(?<!%)%s")


def count_placeholders(sql: str) -> int:
    return len(_PLACEHOLDER.findall(sql))


# ---------------------------------------------------------------------------
# Term parsing
# ---------------------------------------------------------------------------
def test_parse_terms_splits_on_commas_only():
    # Spaces must not split, or 'spring boot' stops being a phrase.
    assert search.parse_terms("spring boot, kubernetes") == ["spring boot", "kubernetes"]


def test_parse_terms_normalises_and_dedupes():
    assert search.parse_terms("  Java , java,  , JAVA ") == ["java"]
    assert search.parse_terms("") == []
    assert search.parse_terms(None or "") == []


def test_parse_terms_caps_runaway_queries():
    many = ", ".join(f"term{i}" for i in range(50))
    assert len(search.parse_terms(many)) == search.MAX_TERMS


def test_term_always_searches_for_itself():
    t = Term(raw="k8s", variants=["kubernetes"])
    assert t.variants[0] == "k8s"
    assert "kubernetes" in t.variants


def test_term_dedupes_a_self_referential_synonym():
    t = Term(raw="go", variants=["go", "golang"])
    assert t.variants == ["go", "golang"]


def test_expand_terms_applies_synonyms():
    terms = search.expand_terms("k8s, java", {"k8s": ["kubernetes"]})
    assert [t.raw for t in terms] == ["k8s", "java"]
    assert terms[0].variants == ["k8s", "kubernetes"]
    assert terms[1].variants == ["java"]  # no entry -> just itself


# ---------------------------------------------------------------------------
# The invariant that breaks everything when violated
# ---------------------------------------------------------------------------
def test_term_score_sql_params_match_placeholders():
    for term in (Term("java"), Term("k8s", ["kubernetes", "kube"])):
        sql, params = search.term_score_sql(term)
        assert count_placeholders(sql) == len(params), sql


def test_scoring_columns_params_match_placeholders():
    terms = search.expand_terms("k8s, rag, java", {"k8s": ["kubernetes"], "rag": ["retrieval"]})
    cols, params = search.scoring_columns(terms)
    assert count_placeholders(" ".join(cols)) == len(params)


def test_candidate_clause_params_match_placeholders():
    terms = search.expand_terms("k8s, rag", {"k8s": ["kubernetes"]})
    sql, params = search.candidate_clause(terms)
    assert count_placeholders(sql) == len(params)


def test_matched_terms_and_exclusion_params_match():
    terms = search.expand_terms("a, b, c", {})
    sql, params = search.matched_terms_expr(terms)
    assert count_placeholders(sql) == len(params) == 3
    sql, params = search.exclusion_clause("intern, sales")
    assert count_placeholders(sql) == len(params) == 2


# ---------------------------------------------------------------------------
# Scoring shape
# ---------------------------------------------------------------------------
def test_exact_tier_outranks_everything_else():
    """An exact title hit must be unreachable by any number of weaker matches.

    This is the whole ranking contract. W_FTS is multiplied by a ts_rank_cd that
    is bounded well below 1 in practice, and W_FUZZY by a similarity <= 1, so
    the ceiling of the two weak tiers has to stay under the exact weight.
    """
    assert search.W_EXACT > search.W_FTS + search.W_FUZZY


def test_fuzzy_floor_matches_the_indexed_operator():
    """The score threshold and the `<%` GUC db.connect() sets must be identical.

    If FUZZY_FLOOR drifts above the GUC the recall predicate silently stops
    being a superset and matching jobs vanish with no error.
    """
    sql, _ = search.term_score_sql(Term("kubernetes"))
    assert "<%" in sql  # scoring uses the operator, not a hand-written compare
    cand, _ = search.candidate_clause([Term("kubernetes")])
    assert "<%" in cand


def test_score_uses_greatest_not_a_sum():
    """Tiers are best-of. Summing them would make the weights uninterpretable."""
    sql, _ = search.term_score_sql(Term("java"))
    assert sql.startswith("greatest(")


def test_fts_rank_is_gated_behind_the_match_test():
    """ts_rank_cd walks the whole tsvector; @@ is indexable. Gate or it crawls."""
    sql, _ = search.term_score_sql(Term("java"))
    assert sql.index("@@") < sql.index("ts_rank_cd")


def test_candidate_clause_covers_every_scoring_tier():
    """Recall must be a superset of scoring, or rows disappear silently."""
    term = Term("k8s", ["kubernetes"])
    cand, _ = search.candidate_clause([term])
    assert "search_tsv @@" in cand              # FTS tier
    assert cand.count("title ilike") == 2       # exact tier, one per variant
    assert "<%" in cand                         # fuzzy tier


def test_candidate_clause_is_empty_without_terms():
    assert search.candidate_clause([]) == ("", [])


def test_websearch_query_ors_variants_as_phrases():
    q = search._websearch_query(Term("k8s", ["kubernetes"]))
    assert q == '"k8s" OR "kubernetes"'


def test_freshness_decays_but_never_to_zero():
    sql = search.freshness_sql()
    assert str(search.FRESHNESS_FLOOR) in sql
    assert "exp(" in sql
    # posted_at preferred, first_seen_at as the fallback
    assert "coalesce(j.posted_at, j.first_seen_at)" in sql


# ---------------------------------------------------------------------------
# Synonym file
# ---------------------------------------------------------------------------
def test_load_synonyms_csv_parses_the_real_file():
    pairs = search.load_synonyms_csv(
        str(Path(__file__).resolve().parents[1] / "data" / "synonyms.csv")
    )
    table = dict(pairs)
    assert "kubernetes" in table["k8s"]
    assert "large language model" in table["llm"]
    # comment lines must not become terms
    assert not any(t.startswith("#") for t in table)
    assert all(t == t.lower().strip() for t in table)


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"  ok  {fn.__name__}")
    print(f"\n{len(fns)} passed")
