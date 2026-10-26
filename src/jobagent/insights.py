"""Turn labels back into filter settings.

ROADMAP rung 1: at ~30 labels no model is learnable, but *counting* is. This is
that rung for the experience ceiling -- "6 of 20 roles you tagged too-senior
state a bar of 5+, so try 4" -- plus the audit that uses the same labels to
check whether the extractor is any good.

Pure functions over rows of labels, and one thin reader to fetch them. Nothing
here writes: it prints a recommendation and the JSON to paste. The README's
whole posture is that the model never owns control flow, and a filter that
retunes itself behind the user's back is the same mistake in a smaller box.
"""

from __future__ import annotations

import math

# Below this there is nothing to count, only noise to overfit to.
MIN_LABELS_FOR_A_SUGGESTION = 20
# One person having a bad afternoon is not evidence about a corpus.
MIN_TOO_SENIOR = 5
# How many liked roles have to be pressed against the ceiling before it reads
# as the ceiling being too tight rather than as coincidence.
MIN_AT_THE_CEILING = 3

TOO_SENIOR = "too-senior"
WANTED = ("apply", "maybe")


def _bar(row: dict) -> int | None:
    return row.get("min_years")


def _tagged_too_senior(rows: list[dict]) -> list[dict]:
    return [
        r
        for r in rows
        if r.get("verdict") == "no"
        and TOO_SENIOR in (r.get("reason_tags") or [])
        and _bar(r) is not None
    ]


def _wanted(rows: list[dict]) -> list[dict]:
    return [r for r in rows if r.get("verdict") in WANTED and _bar(r) is not None]


def suggest_experience_ceiling(
    rows: list[dict],
    years_experience: float = 0.0,
    current: int | None = None,
) -> dict | None:
    """Propose a ceiling from the labels, in whichever direction they point.

    `rows` is one entry per labelled *role* (not posting), each with
    `min_years`, `verdict` and `reason_tags`.

    Symmetric on purpose. The filter starts at Any and nothing is hidden until
    the user moves it, so the first useful suggestion is almost always
    "tighten" -- but once a ceiling is set, liked roles piling up against it are
    evidence to widen, and a tighten-only rule would be a ratchet that only ever
    narrows what the user gets to see.

    Returns None when there is nothing to say, which is most of the time.
    """
    if len(rows) < MIN_LABELS_FOR_A_SUGGESTION:
        return None

    floor = math.ceil(years_experience)
    too_senior = _tagged_too_senior(rows)
    wanted = _wanted(rows)

    if len(too_senior) >= MIN_TOO_SENIOR:
        lowest_rejected = min(_bar(r) for r in too_senior)
        candidate = max(floor, lowest_rejected - 1)

        # The half that matters. A ceiling that would have hidden something
        # already marked Interested is not a better ceiling, however well it
        # fits the rejections -- so those roles raise the proposal rather than
        # being counted as acceptable losses. Same instinct as the exploration
        # slice: a loop that only ever narrows converges on nothing.
        conflicts = [r for r in wanted if _bar(r) > candidate]
        if conflicts:
            candidate = max(_bar(r) for r in conflicts)

        if current is None or candidate < current:
            return {
                "direction": "tighten",
                "ceiling": candidate,
                "from_ceiling": current,
                "supporting": len(too_senior),
                "conflicts": [
                    {"title": r.get("title"), "min_years": _bar(r), "verdict": r.get("verdict")}
                    for r in conflicts
                ],
                "rationale": _tighten_rationale(too_senior, lowest_rejected, conflicts, candidate),
            }

    if current is not None:
        at_ceiling = [r for r in wanted if _bar(r) >= current - 1]
        if len(at_ceiling) >= MIN_AT_THE_CEILING and len(too_senior) < MIN_TOO_SENIOR:
            return {
                "direction": "widen",
                "ceiling": current + 2,
                "from_ceiling": current,
                "supporting": len(at_ceiling),
                "conflicts": [],
                "rationale": (
                    f"{len(at_ceiling)} roles you liked state a bar within one year of your "
                    f"ceiling of {current}, and only {len(too_senior)} of your rejections were "
                    f"tagged {TOO_SENIOR}. The good ones are pressed against the limit, which "
                    f"usually means more of them sit just above it."
                ),
            }

    return None


def _tighten_rationale(too_senior, lowest_rejected, conflicts, candidate) -> str:
    text = (
        f"{len(too_senior)} roles you tagged {TOO_SENIOR} state a bar, the lowest of them "
        f"{lowest_rejected}."
    )
    if conflicts:
        highest = max(_bar(r) for r in conflicts)
        text += (
            f" Held at {candidate} rather than lower: {len(conflicts)} role(s) you marked "
            f"Interested state up to {highest}, and hiding those would be a worse trade."
        )
    return text


def audit_extraction(rows: list[dict], current: int | None = None) -> dict:
    """Check the extractor against the labels, which cost nothing extra.

    Three signals, each pointing somewhere different:

    * roles judged too senior where no bar was extracted -> the regex missed
      one it should have caught, and `backfill --dry-run --force` is the loop
    * roles judged too senior whose bar is inside the ceiling -> either a
      misread, or the user judging on the title rather than the years
    * roles marked Interested above the ceiling -> the ceiling is too tight,
      independent of whether the extraction was right
    """
    too_senior_all = [
        r
        for r in rows
        if r.get("verdict") == "no" and TOO_SENIOR in (r.get("reason_tags") or [])
    ]
    missed = [r for r in too_senior_all if _bar(r) is None]
    inside = (
        [r for r in too_senior_all if _bar(r) is not None and _bar(r) <= current]
        if current is not None
        else []
    )
    wanted_above = (
        [r for r in _wanted(rows) if _bar(r) > current] if current is not None else []
    )
    return {
        "labelled_roles": len(rows),
        "too_senior": len(too_senior_all),
        "recall_misses": missed,
        "inside_the_ceiling": inside,
        "wanted_above_the_ceiling": wanted_above,
    }


def labelled_roles(conn) -> list[dict]:
    """One row per labelled role. `label_job` fans a verdict across every
    posting in a dedupe group, so counting postings would weight a role by how
    many boards happened to carry it."""
    return conn.execute(
        """
        select distinct on (j.dedupe_key)
               j.title,
               j.min_years_experience as min_years,
               l.verdict,
               l.reason_tags
          from job_label l
          join job j on j.id = l.job_id
         order by j.dedupe_key, j.id
        """
    ).fetchall()
