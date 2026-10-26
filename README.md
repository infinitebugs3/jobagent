# jobagent — week 1

An ATS-first job search agent. This repo is the phase-1 skeleton: ingestion,
normalization, dedupe, and a labeling loop. No LLM yet, on purpose.

**The week 1 goal:** one command that fills a database with a few thousand live,
deduped postings from companies you'd actually work for — plus a label set that
tells you whether your matching is any good.

---

## Quickstart

```bash
cp .env.example .env
cp profile.example.yaml profile.yaml     # fill this in

docker compose up -d
python -m venv .venv && source .venv/bin/activate
pip install -e .

python -m jobagent.cli init                      # apply schema
python -m jobagent.cli detect data/companies.csv # resolve companies to their ATS
python -m jobagent.cli sync                      # fetch jobs
python -m jobagent.cli stats                     # sanity check
python -m jobagent.cli list --max-age 7 --unlabeled
```

Tests need no database and no network:

```bash
python tests/test_normalize.py
```

---

## The week

### Day 1 — Skeleton and schema
Get `docker compose up -d` and `cli init` working. Read `migrations/001_init.sql`
end to end; it's short and every column earns its place.

Note `answer_bank` is empty. Fill it **by hand** every time you apply to something
manually this week. It's the one table you cannot backfill later, and it's what
makes the agent feel like it knows you six months from now.

### Day 2 — ATS detector
`src/jobagent/ats/detector.py` is the highest-leverage file in the repo. Three
strategies, cheapest first: parse a board URL, scan the careers page HTML, then
probe guessed slugs.

Expect **60–70% resolution**. That's a good hit rate. Misses go to
`data/unresolved.csv` — don't chase the tail, add them manually if you care.

### Day 3 — Fetchers
Three providers, one interface, in `ats/fetchers.py`.

**Before trusting any parser, curl the endpoint.** These URLs drift, and one
minute of curl saves an hour debugging a parser against a 404:

```bash
curl -s 'https://boards-api.greenhouse.io/v1/boards/stripe/jobs?content=true' | head -c 400
curl -s 'https://api.lever.co/v0/postings/lever?mode=json' | head -c 400
curl -s 'https://api.ashbyhq.com/posting-api/job-board/ashby' | head -c 400
```

Adding Workable or SmartRecruiters later = one new function plus a `FETCHERS`
entry. Nothing else changes.

### Day 4 — Normalize, dedupe, upsert
`normalize.py` is pure functions, no IO, fully tested. The dedupe key is the
load-bearing piece: the same role posted on three boards must collapse to one.

Closure detection is free — anything not seen in the last two syncs gets
`closed_at` set. No extra requests.

### Day 5 — Registry curation (the real matching work)
**This is where match quality actually comes from.** If your 200–500 seeded
companies are ones you'd genuinely work at, most of what you scrape is already
relevant and the scorer only has to order it. Seed indiscriminately and no model
recovers it.

Sources: your own target list, Wellfound, YC's directory, the Indian startup
ecosystem if that's your market. `data/companies.csv` has 20 to start.

Also fill in `profile.yaml`. Static YAML is fine for one user in week 1.

### Day 6 — Filters, embeddings, and your first labels
```bash
pip install -e ".[match]"
```
Hard filters first (location, seniority band, comp floor, excluded titles) — plain
SQL, free, and debuggable. Then `all-MiniLM-L6-v2` locally for ordering: encode
the profile once, each job once, cosine similarity.

Then **hand-label 50–80 jobs.** Take some from the top of the ranking and some
from the middle:

```bash
python -m jobagent.cli list --max-age 14 --unlabeled --limit 50
python -m jobagent.cli label 1204 apply "good infra ownership, small team"
python -m jobagent.cli label 1207 no "consultancy"
```

This hour is the highest-value hour of the week. Without it, "is my matcher any
good?" is unanswerable and you'll tune prompts on vibes forever.

### Day 7 — LLM reranker, measured
Score the top ~50 with structured output. Order the fields so evidence commits
before the number — models are badly calibrated on bare 1–10 scores:

```python
class JobScore(BaseModel):
    matched_requirements: list[str]
    missing_requirements: list[str]
    dealbreakers: list[str]
    seniority_fit: Literal["under", "match", "over"]
    score: int                 # 0-100, derived after the above
    one_line_rationale: str
```

Then measure precision@20 against your day-6 labels. You end the week with ranked
jobs **and a number telling you how much to trust the ranking**.

---

## Design rules

**Keep the LLM at the edges.** Ingestion, storage, dedupe, state, and scheduling
are deterministic code that works with the model switched off. The model scores,
drafts, and answers — it never owns control flow. That's what makes this
debuggable, cheap, and testable.

**No agent framework yet.** Phase 1 has almost no LLM in it. When LLM calls
arrive they're single-shot structured extraction — use Instructor or PydanticAI,
not LangChain. Revisit LangGraph when the apply executor's state handling
actually hurts.

**Be polite to free endpoints.** Default is 1.5 req/sec per provider, sequential.
These are free public APIs; hammering them is how they stop being free public
APIs. If you ever need proxies to make something work, you've left personal-use
territory — treat that as a signal, not a problem to solve.

**Freshness is worth as much as fit.** A 70% match posted yesterday beats a 90%
match from six weeks ago. `first_seen_at` is already stored — weight it.

---

## Week 2 — search, filters, and the labeling UI

Week 1 fills a database. Week 2 makes it usable: 10,520 postings is far too many
to browse, and `cli list` shows no description, so labeling from it is guesswork.

```bash
pip install -e ".[api]"

jobagent init                 # applies every migration in migrations/
jobagent backfill             # location_bucket + seniority on existing rows
jobagent seed-synonyms        # curated aliases: k8s -> kubernetes
jobagent profile-import       # profile.yaml -> a saved search

uvicorn api.main:app --port 8000       # terminal 1
npm --prefix web run dev               # terminal 2 -> localhost:3000
```

The CLI gained search too, so none of this requires a browser:

```bash
jobagent list --q "spring boot, rag" --sort relevance
jobagent list --profile default --max-age 7
jobagent label 4211 apply "strong infra ownership" --tags great-fit,good-company
```

**Search.** Terms are comma-separated and scored in tiers: an exact title match
(100) can never be outranked by any number of full-text (10 × rank) or fuzzy
(3 × similarity) hits. Scores sum across terms, so 3-of-5 beats 1-of-5, then
decay with age — a 70% match from yesterday really does beat a 90% match from
six weeks ago. Every row comes back with `matched_terms`, so "why is this here"
is always answerable.

**Filters vs. scoring.** Structured facets (role, location, seniority, company,
work type, board) go in the `WHERE` clause and change *which* jobs come back.
Keywords go in the `ORDER BY` and change *what order*. Mixing the two is what
makes a search feel arbitrary.

**Role filter.** Curated title keyword groups — the simplest thing that needs no
classifier. Matching is anchored at word starts: `llm` matches "LLMOps" but not
"Fu*lfilLM*ent", which a naive `%llm%` happily returned.

**Labeling.** `/review` is keyboard-driven — <kbd>a</kbd> interested,
<kbd>m</kbd> maybe, <kbd>n</kbd> not for me, <kbd>1</kbd>–<kbd>9</kbd> reason
tags, <kbd>u</kbd> undo. A verdict applies to the whole dedupe group, so the
same role on three boards is judged once. 30% of the queue is drawn at random
and stored as `source='exploration'`: labeling only what the ranker surfaced
would produce an eval set selected by the thing it is meant to evaluate.

Duplicate postings are collapsed everywhere (10,520 → 8,544 roles), so one job
never appears 31 times.

## What comes next (not this week)

- Dynamic profile store: structured facts + document corpus + answer bank, replacing `profile.yaml`
- Few-shot personalization: inject your 15–20 most recent labels into the reranker prompt
- Logistic regression over ~15 hand-built features, once you pass ~100 labels
- Exploration slots: 3–5 jobs/day sampled from rank 100–300, or the system narrows to nothing
- Grounded resume tailoring with a verification pass (no fabrication, structurally enforced)
- Apply executor: per-ATS Playwright adapters, human-in-the-loop for anything ambiguous
- Recruiter drafts sent through your own OAuth mailbox, never a shared server

## Layout

```
migrations/001_init.sql     schema — read this first
migrations/002_*.sql        facets, tsvector, saved searches, reason tags
data/companies.csv          registry seed
data/synonyms.csv           curated search aliases
profile.example.yaml        copy to profile.yaml
ROADMAP.md                  long-term plan + realistic timeline
src/jobagent/
  models.py                 RawPosting, Profile
  normalize.py              pure functions, dedupe key      <- fully tested
  search.py                 terms, synonyms, scoring tiers, role groups
  query.py                  filters -> SQL, shared by CLI and API
  profile.py                profile.yaml -> saved search
  db.py                     upserts, closure detection, labels
  sync.py                   rate-limited sync loop
  cli.py                    init / detect / sync / list / label / stats
                            / backfill / seed-synonyms / profile-import
  ats/
    detector.py             careers URL -> (provider, token)
    fetchers.py             greenhouse | lever | ashby | ...
api/                        FastAPI over query.py — no SQL of its own
web/                        Next.js: /review, /jobs, /jobs/[id], /searches
tests/                      no DB, no network, 44 tests in <1s
```
