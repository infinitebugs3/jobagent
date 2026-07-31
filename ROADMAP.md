# jobagent — long-term roadmap

Companion to the phase-1 implementation plan, which covers only what is being
built right now (search, filters, labeling UI). This document is everything
after that: how the system gets progressively more personal, when alerts land,
and what it realistically takes in calendar time.

Written 2026-08-01. Revisit at each gate.

---

## Where this actually is

**Working today**
- 10,520 open jobs, 50 companies, 6 ATS providers, deduped to 8,544 distinct roles
- Postgres FTS search with synonym expansion, tiered scoring, freshness decay
- `search_profile` compiled from `profile.yaml` — narrows 10,520 → ~970 relevant
- `job_label` with verdicts + `reason_tags`, fanned out per dedupe group

**In progress**
- FastAPI layer, Next.js UI (`/jobs`, `/jobs/[id]`, `/review`, `/searches`)

**Zero rows so far**
- `job_label` — 0 labels
- `job.embedding` — declared `vector(384)`, entirely NULL
- No application tracking, no click tracking, no search logging of any kind

That last line is the whole problem this roadmap solves.

---

## The one principle that makes this timeline honest

**Every personalization technique is gated by signal volume, not by engineering
time.** The code for embedding centroids is an afternoon. Having 150 labels to
compute them from is two months of actually using the thing. Outcome learning —
"which companies actually reply to me" — is three days of code and *six months*
of genuinely job searching.

So the plan is not a build order. It is a **data accumulation schedule**, with
build work slotted into the waiting.

Which produces the single most important sequencing rule:

> **Ship instrumentation months before the model that consumes it.**
>
> If the `application` table lands in the same sprint as reply-rate learning, it
> has zero rows and the model waits a quarter. Ship the tables early and empty.
> They cost a day. Not having them costs a quarter.

---

## The personalization ladder

Each rung needs the rung below it to have produced data. Rungs cannot be
skipped, but they *can* be abandoned — see the decision gates.

| # | Technique | Unlocks at | Build effort | What it does |
|---|---|---|---|---|
| 0 | Profile keyword search | — **built** | done | `profile.yaml` → ranked results. Cold start for every future user too. |
| 1 | **`reason_tags` rule mining** | ~30 labels | 1 day | Count the tags on your `no`s. "6 of 20 tagged `too-senior`" → offer to tighten the seniority filter. No ML. Highest ROI on the ladder and the one everybody skips. |
| 2 | **Filter personalization** | ~20 sessions | 3 days | Reorder facet values by your history, suggest exclusions, sticky defaults, auto-widen on zero results. Needs `search_event`. |
| 3 | **Embedding centroids** | ~150 labels | 1 day* | `score += w·(cos(job, apply_centroid) − cos(job, no_centroid))`. *After phase-2 embeddings exist. |
| 4 | **Logistic regression** | ~200 labels | 2–3 days | ~15 hand-built features (seniority delta, company size, remote, term overlap, age, description length). Interpretable — the weights *are* the explanation. |
| 5 | **Outcome learning** | ~30 applications *with known outcomes* | 2 days code | "You hear back from 50–200 person companies, never from 1000+." Reranks on what converts, not what you clicked. The thing that makes this an agent rather than a job board. |
| 6 | Collaborative signal | multi-user, ~100 users | 1 week | "People who applied to X also applied to Y." Only meaningful once live. |

Rung 1 deserves emphasis. At 30 labels no model is learnable, but *counting* is.
Aggregating reason tags gets you most of the practical benefit of rungs 3–4,
months earlier, with code you can read in one sitting.

---

## Instrumentation to ship early and empty

Two tables, one day of work, and they should land in **September 2026** — long
before anything reads them.

```sql
-- The strongest signal in the system, and currently not captured at all.
-- Also a tracker the user wants on its own merits.
create table application (
    id             bigserial primary key,
    job_id         bigint references job(id),
    dedupe_key     text,
    status         text not null,   -- clicked | applied | screening
                                    -- | interview | offer | rejected | ghosted
    applied_at     timestamptz,
    last_status_at timestamptz,
    notes          text
);

-- Makes filter personalization possible. Without it, rung 2 has nothing.
create table search_event (
    id              bigserial primary key,
    profile_id      bigint references search_profile(id),
    query_text      text,
    filters         jsonb,
    result_count    int,
    clicked_job_ids bigint[],
    at              timestamptz not null default now()
);
```

`application` matters more than it looks. The `apply` verdict records *intent*;
this records *action*, and eventually *outcome*. A job you marked `apply` and
never applied to is a different data point from one you applied to and heard
back on — and only the second teaches anything about the market.

---

## Phases and dates

Effort is focused working days. Calendar assumes a side-project pace of roughly
2–3 focused days a week, which is why the two columns diverge.

### Phase 1 — search + labeling UI · **Aug 2026**

| Work | Effort |
|---|---|
| FastAPI layer (8 endpoints, connection pool, facet caching) | 2–3 d |
| Next.js UI — `/jobs`, `/jobs/[id]`, `/review`, `/searches` | 4–6 d |
| Label 80 dedupe groups, 70/30 review/exploration | 2 h |

**Exit gate: 80 labels.** Nothing downstream is meaningful without them.
Target: **end of August 2026**.

### Phase 1.5 — alerts · **early Sep 2026**

Alerts were originally scheduled inside phase 2. They do not belong there:
they need only the saved profile (built) plus a runtime date window, and the
`search_profile` table is already deliberately stateless so one row can serve
browse, review queue and digest alike.

| Work | Effort |
|---|---|
| `sent_alert` dedup table + `jobagent digest --since 7d` | 1 d |
| Email delivery (deferred until multi-user) | 1 d |

For one user, a terminal digest is enough. Email only matters once it's live —
at which point it becomes the main retention mechanic, because nobody opens a
job board daily and everybody reads email.

### Phase 2 — semantic match · **Sep–Oct 2026**

| Work | Effort |
|---|---|
| MiniLM embedding backfill over 10.5k jobs | 1 d |
| Hybrid score: `w₁·keyword + w₂·cosine + w₃·freshness` | 1–2 d |
| LLM reranker over top ~50, structured `JobScore` output | 2 d |
| **Calibrate weights against the phase-1 labels** | 1 d |
| Rung 1 — reason-tag rule mining | 1 d |
| Ship `application` + `search_event` tables, empty | 1 d |

**Gate: measure `precision@20` against `source='review'` labels.** Compare
against keyword-only as the baseline. See decision gates below.

### Phase 2a — personalization · **Nov 2026 – Feb 2027**

Gated by label and session accumulation, not by code. Expect ~20–30 new labels
a week if the tool is genuinely in use.

| Rung | Expected window |
|---|---|
| Rung 2 — filter personalization (~20 sessions) | Nov 2026 |
| Rung 3 — embedding centroids (~150 labels) | Dec 2026 |
| Rung 4 — logistic regression (~200 labels) | Jan–Feb 2027 |

### Phase 2b — outcome learning · **Q2 2027**

Needs ~30 applications with *resolved* outcomes. Application → first reply is
typically 1–3 weeks; reply → resolution another 2–6. So even applying steadily
from October, a usable outcome set doesn't exist before **March–April 2027**.

This is not a schedule that can be compressed by working harder. It can only be
compressed by having shipped `application` in September.

### Phase 3 — public aggregator · **H2 2027, conditional**

Only worth starting once your own loop demonstrably works — meaning phase 2b
has changed a decision you made. Rough shape:

| Work | Effort | Notes |
|---|---|---|
| Multi-user auth, per-user profiles | 1 w | `profile.yaml` → `user_profile` table |
| Job function taxonomy, skill/tech tags | 1–2 w | classifier + NER over title/description |
| Experience range, employment type parsing | 1 w | regex + LLM fallback |
| Salary parsing | 3 d | **<15% coverage expected** — ships as a toggle, not a filter |
| Company size / industry enrichment | 1 w | external source; `company.size_bucket` is mostly empty |
| SEO'd SSR job pages | 1 w | the growth channel |
| Rung 6 — collaborative signal | 1 w | needs ~100 active users |

**Coverage rule, enforced:** a facet ships as a real filter only above ~60%
coverage. Below that it ships as a positive-only toggle ("has salary listed")
that never silently drops the 85% with unknown values. A filter that hides most
of the inventory is worse than no filter.

**Elasticsearch:** revisit only past ~1M documents or if FTS p95 exceeds 300ms.
Not before. `query.py` keeps its filter model backend-agnostic so the migration
stays possible.

---

## Calendar summary

| Window | Milestone | Gated by |
|---|---|---|
| Aug 2026 | Phase 1 shipped, 80 labels | engineering |
| early Sep 2026 | Digest alerts working | engineering |
| Sep 2026 | `application` + `search_event` shipped **empty** | engineering |
| Sep–Oct 2026 | Embeddings, reranker, precision@20 measured | labels from Aug |
| Nov 2026 | Filter personalization | ~20 sessions |
| Dec 2026 | Embedding centroids | ~150 labels |
| Jan–Feb 2027 | Logistic regression | ~200 labels |
| Mar–Apr 2027 | Outcome learning | ~30 resolved applications |
| H2 2027 | Public aggregator | phase 2b proving value |

---

## Decision gates — when to stop climbing

A ladder rung is worth building only if the rung below it left something on the
table. Check, don't assume:

- **After phase 2:** if keyword-only `precision@20` is already above ~70%,
  embeddings are not the bottleneck — **registry curation is**. Spend the time
  adding 200 more companies instead. The README is right that match quality is
  mostly decided by which companies are in the registry.
- **Before rung 4:** if centroids move `precision@20` by less than ~5 points,
  a logistic regression over the same signal will not save it. Skip to outcome
  learning.
- **Before rung 6:** collaborative filtering below ~100 active users is noise
  wearing a lab coat.
- **Any rung:** if you cannot state in one sentence why a job was ranked up,
  do not ship that rung.

---

## Guardrails

Three failure modes, each with a fix that has to be designed in rather than
patched on.

**Filter bubble.** Learn only from what you saw, show only what you ranked, and
the system narrows forever until it recommends four jobs from one company. The
30% exploration slice in the review queue is the fix, and it must stay
**permanent** — not just during the initial labeling push. It is not only an
eval-integrity device.

**Taste drift.** Preferences change as you interview. Time-decay training
weights so a label from six months ago cannot outvote last week's. Cheap
version: inject the 15–20 most recent labels as few-shot examples in the
reranker prompt.

**Silent personalization.** Every learned adjustment states itself — "ranked up:
similar to 3 roles you saved" — the way `matched_terms` already does for
keywords. And every filter suggestion is a button, never an automatic change. A
filter that moves on its own is a filter users stop trusting, and trust is the
only thing a recommender actually has.

---

## Explicitly not planned

Recorded so they don't get rediscovered as good ideas later:

- **Auto-apply.** Application quality collapses and ATS bans follow. Human in
  the loop, always.
- **LinkedIn / aggregator scraping.** ToS risk, and ATS boards are the primary
  source anyway. If a source needs proxies, that's a signal, not a problem to
  solve.
- **Agent framework.** Single-shot structured extraction. The model never owns
  control flow; ingest, storage, dedupe and scheduling stay deterministic and
  keep working with the model switched off.
- **Salary as a first-class filter.** Coverage will not support it. Revisit only
  if measured coverage passes 60%.
