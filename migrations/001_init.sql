-- Week 1 schema. Deliberately small: no LLM tables yet, no application state machine.
-- Add pgvector only when you actually store embeddings (day 6).

create extension if not exists vector;

-- ---------------------------------------------------------------
-- Registry: the highest-leverage table in the system.
-- Curate this and most of your match quality is already decided.
-- ---------------------------------------------------------------
create table if not exists company (
    id            bigserial primary key,
    name          text        not null,
    ats_provider  text        not null,   -- greenhouse | lever | ashby | unknown
    ats_token     text        not null,   -- the board slug used by that ATS
    careers_url   text,
    size_bucket   text,                   -- '1-50' | '51-200' | '201-1000' | '1000+'
    notes         text,
    active        boolean     not null default true,
    last_synced_at timestamptz,
    created_at    timestamptz not null default now(),
    unique (ats_provider, ats_token)
);

-- ---------------------------------------------------------------
-- Jobs. external_id is the ATS's own id, so upserts are idempotent.
-- ---------------------------------------------------------------
create table if not exists job (
    id             bigserial primary key,
    company_id     bigint      not null references company(id) on delete cascade,
    external_id    text        not null,
    title          text        not null,
    location       text,
    remote_type    text,                  -- remote | hybrid | onsite | unknown
    description_md text,
    apply_url      text        not null,
    dedupe_key     text        not null,
    posted_at      timestamptz,
    first_seen_at  timestamptz not null default now(),
    last_seen_at   timestamptz not null default now(),
    closed_at      timestamptz,
    embedding      vector(384),           -- all-MiniLM-L6-v2, filled on day 6
    unique (company_id, external_id)
);

create index if not exists job_dedupe_idx    on job (dedupe_key);
create index if not exists job_open_idx      on job (last_seen_at) where closed_at is null;
create index if not exists job_first_seen_idx on job (first_seen_at desc);

-- ---------------------------------------------------------------
-- Your own verdicts. This is your eval set AND your training signal.
-- Label from day 1 or you will never know if matching works.
-- ---------------------------------------------------------------
create table if not exists job_label (
    job_id     bigint primary key references job(id) on delete cascade,
    verdict    text        not null check (verdict in ('apply', 'maybe', 'no')),
    reason     text,
    source     text        not null default 'review',  -- review | exploration
    labeled_at timestamptz not null default now()
);

-- ---------------------------------------------------------------
-- Screening questions you have answered. Empty in week 1 -- fill it
-- by hand every time you apply manually. Cannot be backfilled later.
-- ---------------------------------------------------------------
create table if not exists answer_bank (
    id            bigserial primary key,
    question_text text        not null,
    question_hash text        not null unique,
    answer_text   text        not null,
    field_type    text,                   -- text | textarea | select | boolean | number
    times_used    int         not null default 0,
    approved_at   timestamptz not null default now()
);

-- ---------------------------------------------------------------
-- Observability. Without this you cannot tell a broken fetcher from
-- a company that simply has no open roles.
-- ---------------------------------------------------------------
create table if not exists sync_run (
    id            bigserial primary key,
    company_id    bigint references company(id) on delete set null,
    provider      text,
    started_at    timestamptz not null default now(),
    finished_at   timestamptz,
    jobs_seen     int  not null default 0,
    jobs_inserted int  not null default 0,
    jobs_updated  int  not null default 0,
    ok            boolean not null default false,
    error         text
);

create index if not exists sync_run_recent_idx on sync_run (started_at desc);
