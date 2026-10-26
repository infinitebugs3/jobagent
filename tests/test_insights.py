"""Labels -> a proposed ceiling. No DB: the rules are pure functions on rows.

    python -m pytest tests/test_insights.py
    python tests/test_insights.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jobagent import insights  # noqa: E402


def role(min_years=None, verdict="no", tags=(), title="A Role"):
    return {"min_years": min_years, "verdict": verdict, "reason_tags": list(tags), "title": title}


def padding(n, **kw):
    """Labelled roles that carry no signal, just volume."""
    return [role(**kw) for _ in range(n)]


def test_no_suggestion_before_there_is_evidence():
    rows = [role(8, "no", ["too-senior"]) for _ in range(12)]
    assert insights.suggest_experience_ceiling(rows, 1.5) is None


def test_no_suggestion_when_the_rejections_are_not_about_seniority():
    rows = padding(30, verdict="no", tags=["wrong-stack"])
    assert insights.suggest_experience_ceiling(rows, 1.5) is None


def test_ceiling_suggestion_reads_too_senior_labels():
    rows = [role(y, "no", ["too-senior"]) for y in (5, 6, 8, 8, 10, 12)] + padding(20)
    out = insights.suggest_experience_ceiling(rows, 1.5)
    assert out is not None
    assert out["direction"] == "tighten"
    assert out["ceiling"] == 4        # one below the lowest rejected bar
    assert out["supporting"] == 6


def test_ceiling_never_drops_below_the_users_own_experience():
    """Rejecting a lot of 2-year roles does not mean you want a 0-year one."""
    rows = [role(2, "no", ["too-senior"]) for _ in range(6)] + padding(20)
    out = insights.suggest_experience_ceiling(rows, 4.0)
    assert out["ceiling"] == 4


def test_ceiling_suggestion_refuses_to_hide_roles_you_liked():
    """The half that matters. A ceiling that fits every rejection but deletes a
    role already marked Interested is not an improvement -- and a loop that
    only ever narrows converges on an empty queue."""
    rows = (
        [role(6, "no", ["too-senior"]) for _ in range(6)]
        + [role(8, "apply", title="The one you want")]
        + padding(20)
    )
    out = insights.suggest_experience_ceiling(rows, 1.5)
    assert out["ceiling"] == 8                       # lifted from 5 to keep it
    assert len(out["conflicts"]) == 1
    assert out["conflicts"][0]["title"] == "The one you want"


def test_widening_is_proposed_when_liked_roles_pile_up_at_the_ceiling():
    """Symmetric on purpose: the filter starts at Any, so the ratchet has to
    turn both ways or it only ever removes things from view."""
    rows = [role(y, "apply") for y in (4, 5, 5)] + padding(20)
    out = insights.suggest_experience_ceiling(rows, 1.5, current=5)
    assert out is not None
    assert out["direction"] == "widen"
    assert out["ceiling"] == 7


def test_a_ceiling_already_tighter_than_the_proposal_is_left_alone():
    rows = [role(y, "no", ["too-senior"]) for y in (8, 9, 10, 10, 12)] + padding(20)
    assert insights.suggest_experience_ceiling(rows, 1.5, current=3) is None


def test_audit_counts_extraction_misses():
    rows = [
        role(None, "no", ["too-senior"], title="No bar found"),
        role(2, "no", ["too-senior"], title="Inside the ceiling"),
        role(9, "apply", title="Wanted but above"),
    ] + padding(10)
    out = insights.audit_extraction(rows, current=3)
    assert [r["title"] for r in out["recall_misses"]] == ["No bar found"]
    assert [r["title"] for r in out["inside_the_ceiling"]] == ["Inside the ceiling"]
    assert [r["title"] for r in out["wanted_above_the_ceiling"]] == ["Wanted but above"]


def test_audit_without_a_ceiling_reports_only_what_it_can():
    rows = [role(None, "no", ["too-senior"]), role(9, "apply")] + padding(10)
    out = insights.audit_extraction(rows)
    assert len(out["recall_misses"]) == 1
    assert out["inside_the_ceiling"] == [] and out["wanted_above_the_ceiling"] == []


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"  ok  {fn.__name__}")
    print(f"\n{len(fns)} passed")
