"""These run in under a second with no DB and no network. Run them constantly."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jobagent.normalize import (  # noqa: E402
    dedupe_key,
    detect_remote_type,
    location_bucket,
    normalize_company,
    normalize_title,
    question_hash,
    seniority_of,
)


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
