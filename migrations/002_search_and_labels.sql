-- Phase 0: make the facets filterable, make the corpus searchable, and give
-- labels somewhere to record *why*.
--
-- Everything here is `if not exists` so `jobagent init` stays re-runnable.

create extension if not exists pg_trgm;

-- ---------------------------------------------------------------
-- Facets that already exist as pure functions in normalize.py but
-- were only ever hashed into dedupe_key. You cannot filter on a hash.
-- ---------------------------------------------------------------
alter table job
    add column if not exists location_bucket text,
    add column if not exists seniority       text;

-- ---------------------------------------------------------------
-- Full-text search vector.
--
-- to_tsvector(regconfig, text), setweight, coalesce and tsvector-|| are all
-- IMMUTABLE, so this can be a *generated* column: no trigger to forget, no
-- backfill job to re-run, and every future sync indexes itself.
--
-- Weights: A=title (what you actually search for), B=location, C=description
-- (huge, noisy, still worth matching but must never outrank a title hit).
-- ---------------------------------------------------------------
alter table job
    add column if not exists search_tsv tsvector
    generated always as (
        setweight(to_tsvector('english', coalesce(title, '')), 'A') ||
        setweight(to_tsvector('english', coalesce(location, '')), 'B') ||
        setweight(to_tsvector('english', coalesce(description_md, '')), 'C')
    ) stored;

create index if not exists job_search_tsv_idx      on job using gin (search_tsv);
-- trigram index carries the fuzzy tier: typos and partial tokens ('kuberentes').
create index if not exists job_title_trgm_idx      on job using gin (title gin_trgm_ops);
create index if not exists job_location_bucket_idx on job (location_bucket);
create index if not exists job_seniority_idx       on job (seniority);
create index if not exists job_company_idx         on job (company_id);
create index if not exists job_posted_idx          on job (posted_at desc nulls last);

-- ---------------------------------------------------------------
-- Saved searches. Two representations of the same intent, side by side:
--   query_text -- exactly what you typed, so it stays editable and debuggable
--   filters    -- the compiled structured part, so the UI can round-trip it
-- Keeping both means a UI change never orphans a saved search.
--
-- Deliberately stateless: no last_run_at, no date window. Freshness is a
-- runtime argument, so one row serves browse, review queue and digest alike.
-- ---------------------------------------------------------------
create table if not exists search_profile (
    id           bigserial primary key,
    name         text        not null,
    query_text   text        not null default '',
    exclude_text text        not null default '',
    filters      jsonb       not null default '{}'::jsonb,
    is_default   boolean     not null default false,
    active       boolean     not null default true,
    created_at   timestamptz not null default now(),
    updated_at   timestamptz not null default now()
);

-- At most one default profile, enforced by the database rather than by hope.
create unique index if not exists search_profile_one_default_idx
    on search_profile ((true)) where is_default;

-- ---------------------------------------------------------------
-- Synonyms. Small, curated and actually loaded -- the alternative is an
-- 11k-line analyzer file that nobody wires up and everybody assumes works.
-- ---------------------------------------------------------------
create table if not exists search_synonym (
    term       text   primary key,
    expansions text[] not null
);

-- ---------------------------------------------------------------
-- Labels: keep verdict/source/PK exactly as day 1 left them so the existing
-- CLI and stats queries keep working. Add the *why*.
-- ---------------------------------------------------------------
alter table job_label
    add column if not exists reason_tags text[]      not null default '{}',
    -- denormalized so a verdict can be counted per role, not per duplicate row
    add column if not exists dedupe_key  text,
    add column if not exists updated_at  timestamptz not null default now();

create index if not exists job_label_dedupe_idx on job_label (dedupe_key);
