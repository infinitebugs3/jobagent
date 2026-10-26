"""These run in under a second with no DB and no network. Run them constantly."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jobagent.normalize import (  # noqa: E402
    dedupe_key,
    detect_remote_type,
    location_bucket,
    min_years_experience,
    normalize_company,
    normalize_title,
    question_hash,
    seniority_of,
)


def bar(text):
    """Just the number -- the evidence snippet is asserted separately."""
    found = min_years_experience(text)
    return found[0] if found else None


def test_company_suffixes_stripped():
    assert normalize_company("Acme Technologies Pvt. Ltd.") == "acme technologies"
    assert normalize_company("Acme Technologies") == "acme technologies"


def test_title_noise_stripped():
    variants = [
        "Senior Backend Engineer (Remote) - Req 12345",
        "Backend Engineer II",
        "Sr. Backend Engineer | Bangalore",
    ]
    normalized = {normalize_title(v) for v in variants}
    assert normalized == {"backend engineer"}, normalized


def test_seniority_survives_separately():
    assert seniority_of("Senior Backend Engineer") == "senior"
    assert seniority_of("Staff Engineer") == "staff+"
    assert seniority_of("Backend Engineer") == "mid"
    assert seniority_of("Engineering Intern") == "intern"


def test_location_aliases_collapse():
    assert location_bucket("Bengaluru, KA") == "bangalore"
    assert location_bucket("Bangalore, India") == "bangalore"
    assert location_bucket("Gurugram") == "delhi ncr"
    assert location_bucket("Noida, UP") == "delhi ncr"
    assert location_bucket("Remote - Anywhere") == "remote"


def test_dedupe_collapses_cross_board_duplicates():
    """Same role, same seniority band, three boards -> one key.

    Company suffix, req id, bracketed aside, trailing segment and the
    'Sr.'/'Senior' spelling all have to wash out.
    """
    a = dedupe_key("Acme Technologies Pvt Ltd", "Senior Backend Engineer (Remote) - Req 12345", "Bengaluru, KA")
    b = dedupe_key("Acme Technologies", "Sr. Backend Engineer | Bangalore", "Bangalore, India")
    assert a == b


def test_dedupe_collapses_cosmetic_level_suffixes():
    """'Engineer II' is not a real band change -- seniority_of calls both 'mid'."""
    assert dedupe_key("Acme", "Backend Engineer II", "Bangalore") == \
           dedupe_key("Acme", "Backend Engineer", "Bangalore")


def test_dedupe_separates_seniority_bands():
    """Senior vs Staff at one company is two applications, not one.
    Regression guard: these used to collapse into a single row."""
    senior = dedupe_key("Temporal", "Senior Software Engineer, Observability", "Remote")
    staff = dedupe_key("Temporal", "Staff Software Engineer, Observability", "Remote")
    mid = dedupe_key("Temporal", "Software Engineer, Observability", "Remote")
    assert len({senior, staff, mid}) == 3


def test_dedupe_separates_real_differences():
    backend = dedupe_key("Acme", "Backend Engineer", "Bangalore")
    frontend = dedupe_key("Acme", "Frontend Engineer", "Bangalore")
    other_city = dedupe_key("Acme", "Backend Engineer", "Mumbai")
    assert len({backend, frontend, other_city}) == 3


def test_remote_detection():
    assert detect_remote_type("Remote - India") == "remote"
    assert detect_remote_type("Hybrid - Bangalore") == "hybrid"
    assert detect_remote_type("Bangalore Office") == "onsite"
    assert detect_remote_type(None) == "unknown"


def test_min_years_reads_the_common_phrasings():
    assert bar("3+ years of experience") == 3
    assert bar("at least 3 years of experience") == 3
    assert bar("minimum of 3 years of experience") == 3
    assert bar("3 to 5 years of experience") == 3
    assert bar("3-5 years of experience") == 3
    # The bar is the low end of a range, whichever dash the board happened to
    # emit. A dash not in the class reads '1‐2 years' as a bare 2 and rounds
    # the requirement up, hiding a job the user could actually get.
    assert bar("2–12+ years of industry software engineering experience") == 2
    assert bar("1‐2 years’ experience in business development") == 1
    assert bar("1‑2 years of experience") == 1
    assert bar("5−8 years of experience") == 5
    # No literal 'of experience' anywhere -- these are real postings.
    assert bar("6+ years leading complex sales cycles") == 6
    assert bar("15+ years' experience") == 15


def test_min_years_reads_written_numbers():
    assert bar("five years of professional experience") == 5
    assert bar("six (6) years of experience in software") == 6


def test_min_years_ignores_company_boilerplate():
    assert bar("For 30+ years, our clients have trusted us") is None
    assert bar("the first database provider to IPO in over 20 years") is None
    assert bar("we handle billions of transactions each year") is None


def test_min_years_ignores_temporal_windows_and_benefits():
    assert bar("closed enterprise deals in the last 3 years") is None
    assert bar("scaling to $1b in the next 5 years") is None
    assert bar("paid month off after 4 years & every 2 years thereafter") is None


def test_min_years_takes_the_bar_not_the_nested_clause():
    """The whole reason aggregation is per-segment rather than global.

    A global MIN reads these as 1-year and 5-year roles. Both are real
    postings, and both are exactly the seniority this filter exists to catch.
    """
    assert bar("Minimum of 15 years professional experience, with at least "
               "1 year in management") == 15
    assert bar("10+ years of software engineering experience, including 5+ "
               "years of distributed systems work") == 10


def test_min_years_takes_the_lowest_of_independent_requirements():
    """Separate requirements, though -- lowest wins. Basic vs preferred
    qualifications arrive concatenated, so the basic bar has to survive."""
    assert bar("5+ years of experience with distributed systems.\n"
               "3+ years of professional software development experience") == 3


def test_min_years_is_none_when_unstated():
    assert bar("We are a fast growing team building payments infrastructure.") is None
    assert bar("") is None
    assert bar(None) is None


def test_min_years_rejects_implausible_values():
    # Real artifact: stripping markup glues '8-10 years' into '810 years'.
    assert bar("810 years of experience") is None
    assert bar("1971 years of experience") is None
    # But entry-level phrasing must survive, and zero is not 'unstated'.
    assert bar("0-2 years of experience") == 0


def test_min_years_returns_the_evidence():
    """The snippet is what makes the audit a SELECT and the UI tooltip honest."""
    found = min_years_experience("Requirements\n3+ years of professional experience")
    assert found is not None
    assert found[0] == 3
    assert "3+ years of professional experience" in found[1]


def test_question_hash_ignores_cosmetics():
    a = question_hash("Why do you want to work here?")
    b = question_hash("why do you want to work here? *")
    assert a == b


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"  ok  {fn.__name__}")
    print(f"\n{len(fns)} passed")
