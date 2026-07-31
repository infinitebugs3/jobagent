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
data/companies.csv          registry seed
profile.example.yaml        copy to profile.yaml
src/jobagent/
  models.py                 RawPosting, Profile
  normalize.py              pure functions, dedupe key      <- fully tested
  db.py                     upserts, closure detection, labels
  sync.py                   rate-limited sync loop
  cli.py                    init / detect / sync / list / label / stats
  ats/
    detector.py             careers URL -> (provider, token)
    fetchers.py             greenhouse | lever | ashby
tests/test_normalize.py     no DB, no network, runs in <1s
```
