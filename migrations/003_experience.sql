-- The years-of-experience bar, which exists only in prose.
--
-- No ATS exposes it as a field, so `seniority_of()` -- which reads the title --
-- calls 'Software Development Engineer II' a mid role while its description
-- asks for 3+ years. Roughly a third of the postings that pass a [junior, mid]
-- filter demand 4+ years.
--
-- Everything here is `if not exists` so `jobagent init` stays re-runnable.

-- ---------------------------------------------------------------
-- Deliberately NOT a generated column, despite search_tsv in 002.
-- The extraction is a regex with enough judgement in it (nested clauses,
-- boilerplate, benefit windows) that it has to live in normalize.py where it
-- is unit-testable in a second with no Postgres running.
--
-- min_years_evidence is the sentence the figure came from. It costs a few
-- bytes and turns "why does this say 5+" -- in the UI, and in an accuracy
-- audit after every regex edit -- into a plain SELECT.
-- ---------------------------------------------------------------
alter table job
    add column if not exists min_years_experience int,
    add column if not exists min_years_evidence   text;

-- No index on purpose. The predicate is
--     (min_years_experience is null or min_years_experience <= N)
-- which passes ~85% of rows, against a set already narrowed by closed_at and
-- the facets. A btree would never be chosen. Add one when EXPLAIN asks.
