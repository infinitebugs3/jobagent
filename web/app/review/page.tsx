"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { api, type Filters } from "../lib/api";
import {
  POSITIVE_TAGS,
  REASON_TAGS,
  VERDICT_LABEL,
  type JobDetail,
  type JobRow,
  type ReviewProgress,
  type RoleGroup,
  type SearchProfile,
  type Verdict,
} from "../lib/types";
import { Chip, JobMeta, MatchedTerms } from "../components/Chips";
import { JobDescription } from "../components/JobDescription";
import { ExperienceSlider } from "../components/ExperienceSlider";

const VERDICTS: Verdict[] = ["apply", "maybe", "no"];
const VERDICT_STYLE: Record<Verdict, string> = {
  apply: "bg-emerald-600 hover:bg-emerald-500",
  maybe: "bg-amber-600 hover:bg-amber-500",
  no: "bg-rose-600 hover:bg-rose-500",
};

const QUEUE_SIZE = 12;
const REFILL_AT = 3; // top up before the deck runs out, so there is never a spinner

type Card = JobRow & { description_md?: string | null };

export default function ReviewPage() {
  const [queue, setQueue] = useState<Card[]>([]);
  const [detail, setDetail] = useState<JobDetail | null>(null);
  const [progress, setProgress] = useState<ReviewProgress | null>(null);
  const [roles, setRoles] = useState<RoleGroup[]>([]);
  const [profiles, setProfiles] = useState<SearchProfile[]>([]);

  const [activeRoles, setActiveRoles] = useState<string[]>([]);
  const [profileId, setProfileId] = useState<string>("");
  // The ceiling matters most here: this is the screen where the 80-label
  // budget is actually spent, and a third of the untouched queue asks for
  // more years than a junior/mid filter implies.
  const [maxYears, setMaxYears] = useState<string | undefined>(undefined);
  const [hiddenByExperience, setHiddenByExperience] = useState(0);
  const [tags, setTags] = useState<string[]>([]);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [lastLabeled, setLastLabeled] = useState<number | null>(null);

  const noteRef = useRef<HTMLInputElement>(null);
  const current = queue[0];

  const filters = useCallback(
    (): Filters => ({
      role: activeRoles,
      profile_id: profileId || undefined,
      max_years_required: maxYears,
      sort: profileId ? "relevance" : "newest",
      page_size: String(QUEUE_SIZE),
    }),
    [activeRoles, profileId, maxYears],
  );

  // ---- loading -----------------------------------------------------------
  const loadQueue = useCallback(
    async (replace: boolean) => {
      try {
        const page = await api.queue(filters());
        setHiddenByExperience(page.hidden_by_experience);
        setQueue((prev) => {
          const kept = replace ? [] : prev;
          const seen = new Set(kept.map((j) => j.dedupe_key));
          // dedupe across refills: two fetches can legitimately return the same
          // role, and showing it twice wastes a review.
          return [...kept, ...page.items.filter((j) => !seen.has(j.dedupe_key))];
        });
        setError(null);
      } catch (e) {
        setError(String(e));
      }
    },
    [filters],
  );

  useEffect(() => {
    api.roles().then(setRoles).catch(() => {});
    api.profiles().then(setProfiles).catch(() => {});
  }, []);

  useEffect(() => {
    void loadQueue(true);
    api.progress().then(setProgress).catch(() => {});
  }, [loadQueue]);

  useEffect(() => {
    if (queue.length > 0 && queue.length <= REFILL_AT) void loadQueue(false);
  }, [queue.length, loadQueue]);

  // Full text for the top card only; the rest are just headers until promoted.
  // Keyed on the id rather than the object so a queue refill that leaves the
  // same job on top does not refetch it and wipe the tags mid-judgement.
  const currentId = current?.id;
  useEffect(() => {
    setDetail(null);
    setTags([]);
    setNote("");
    if (currentId === undefined) return;
    let cancelled = false;
    api
      .job(currentId)
      .then((d) => !cancelled && setDetail(d))
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [currentId]);

  // ---- actions -----------------------------------------------------------
  const advance = () => setQueue((q) => q.slice(1));

  const submit = useCallback(
    async (verdict: Verdict) => {
      if (!current || busy) return;
      setBusy(true);
      const job = current;
      // Optimistic: the card leaves immediately. At ~40 labels an hour, waiting
      // on a round trip between every judgement is most of the session.
      advance();
      try {
        await api.label({
          job_id: job.id,
          verdict,
          reason: note.trim(),
          reason_tags: tags,
          source: job.label_source === "exploration" ? "exploration" : "review",
        });
        setLastLabeled(job.id);
        api.progress().then(setProgress).catch(() => {});
      } catch (e) {
        setError(`could not save: ${e}`);
        setQueue((q) => [job, ...q]); // put it back rather than lose the judgement
      } finally {
        setBusy(false);
      }
    },
    [current, busy, note, tags],
  );

  const undo = useCallback(async () => {
    if (lastLabeled === null) return;
    try {
      await api.unlabel(lastLabeled);
      setLastLabeled(null);
      await loadQueue(true);
      api.progress().then(setProgress).catch(() => {});
    } catch (e) {
      setError(String(e));
    }
  }, [lastLabeled, loadQueue]);

  const toggleTag = (t: string) =>
    setTags((prev) => (prev.includes(t) ? prev.filter((x) => x !== t) : [...prev, t]));

  const toggleRole = (slug: string) =>
    setActiveRoles((prev) =>
      prev.includes(slug) ? prev.filter((r) => r !== slug) : [...prev, slug],
    );

  // ---- keyboard ----------------------------------------------------------
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const el = e.target as HTMLElement;
      // Never hijack typing. The note field is where reasoning gets written.
      if (el.tagName === "INPUT" || el.tagName === "TEXTAREA" || el.tagName === "SELECT") {
        if (e.key === "Escape") el.blur();
        return;
      }
      if (e.metaKey || e.ctrlKey || e.altKey) return;

      const key = e.key.toLowerCase();
      if (key === "a") return void submit("apply");
      if (key === "m") return void submit("maybe");
      if (key === "n") return void submit("no");
      if (key === "s") return advance();
      if (key === "u") return void undo();
      if (key === "o" && current) return void window.open(current.apply_url, "_blank");
      if (key === "r") return noteRef.current?.focus();
      const digit = Number(e.key);
      if (digit >= 1 && digit <= 9 && REASON_TAGS[digit - 1]) {
        e.preventDefault();
        toggleTag(REASON_TAGS[digit - 1]);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [submit, undo, current]);

  const visibleTags = REASON_TAGS;

  return (
    <div className="space-y-5">
      {/* progress + filters */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-baseline gap-3">
          <h1 className="text-lg font-semibold">Review</h1>
          {progress && (
            <p className="text-sm text-muted">
              <span className="font-medium text-fg">
                {progress.labeled} / {progress.target}
              </span>{" "}
              roles · {progress.apply} interested · {progress.maybe} maybe · {progress.no} no ·{" "}
              <span title="random slice, so the eval set is not selected by the ranker">
                {progress.exploration} explore
              </span>
              {hiddenByExperience > 0 && (
                <>
                  {" · "}
                  <span title="Roles asking for more years than the slider allows. They are not gone, just not in this deck.">
                    {hiddenByExperience.toLocaleString()} too senior
                  </span>
                </>
              )}
            </p>
          )}
        </div>
        <select
          value={profileId}
          onChange={(e) => setProfileId(e.target.value)}
          className="rounded border border-border bg-surface px-2 py-1 text-sm"
        >
          <option value="">All jobs (newest first)</option>
          {profiles.map((p) => (
            <option key={p.id} value={String(p.id)}>
              {p.name}
            </option>
          ))}
        </select>
      </div>

      <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
        <div className="flex flex-wrap gap-1.5">
          {roles.map((r) => (
            <Chip key={r.slug} active={activeRoles.includes(r.slug)} onClick={() => toggleRole(r.slug)}>
              {r.label}
            </Chip>
          ))}
          {activeRoles.length > 0 && (
            <button
              onClick={() => setActiveRoles([])}
              className="px-2 text-xs text-muted underline hover:text-fg"
            >
              clear
            </button>
          )}
        </div>
        {/* The queue endpoint returns no facets, so there are no per-rung
            counts here -- the number worth showing on this screen is how many
            roles the ceiling is keeping out of the deck, which is in the
            progress line above. */}
        <div className="w-52 text-sm">
          <ExperienceSlider value={maxYears} buckets={null} onCommit={setMaxYears} compact />
        </div>
      </div>

      {error && (
        <p className="rounded border border-rose-500/40 bg-rose-500/10 px-3 py-2 text-sm text-rose-500">
          {error}
        </p>
      )}

      {!current ? (
        <div className="rounded-lg border border-border bg-surface p-10 text-center text-muted">
          <p className="font-medium text-fg">Nothing left in this queue.</p>
          <p className="mt-1 text-sm">
            Clear a role filter, switch saved search, or run <code>jobagent sync</code>.
          </p>
        </div>
      ) : (
        <div className="grid gap-5 lg:grid-cols-[1fr_20rem]">
          {/* the card */}
          <article className="rounded-lg border border-border bg-surface">
            <header className="space-y-2 border-b border-border p-5">
              <div className="flex items-start justify-between gap-4">
                <div>
                  <h2 className="text-xl font-semibold leading-snug">{current.title}</h2>
                  <p className="mt-0.5 text-sm text-muted">
                    {current.company}
                    {current.location ? ` — ${current.location}` : ""}
                  </p>
                </div>
                {current.label_source === "exploration" && (
                  <span
                    title="Drawn at random, not from your ranked results. This is what stops the search narrowing onto itself."
                    className="shrink-0 rounded bg-violet-500/15 px-2 py-0.5 text-[11px] text-violet-500"
                  >
                    exploration
                  </span>
                )}
              </div>
              <JobMeta job={current} ceiling={maxYears} />
              <MatchedTerms terms={current.matched_terms} />
            </header>

            <div className="max-h-[52vh] overflow-y-auto p-5">
              {detail?.description_md ? (
                <JobDescription text={detail.description_md} />
              ) : detail ? (
                <p className="text-sm text-muted">
                  This board publishes no description. Judge on title and company, or press{" "}
                  <kbd>s</kbd> to skip.
                </p>
              ) : (
                <p className="text-sm text-muted">Loading…</p>
              )}
            </div>

            <footer className="flex items-center justify-between border-t border-border px-5 py-3">
              <a
                href={current.apply_url}
                target="_blank"
                rel="noreferrer"
                className="text-sm text-accent hover:underline"
              >
                Open posting <kbd>o</kbd>
              </a>
              <Link href={`/jobs/${current.id}`} className="text-xs text-muted hover:text-fg">
                permalink
              </Link>
            </footer>
          </article>

          {/* the judgement */}
          <aside className="space-y-4">
            <div className="space-y-2">
              {VERDICTS.map((v) => (
                <button
                  key={v}
                  disabled={busy}
                  onClick={() => void submit(v)}
                  className={`flex w-full items-center justify-between rounded-lg px-4 py-3 text-sm font-medium text-white disabled:opacity-50 ${VERDICT_STYLE[v]}`}
                >
                  {VERDICT_LABEL[v]}
                  <kbd className="!border-white/30 !bg-white/15 !text-white">
                    {v === "apply" ? "a" : v === "maybe" ? "m" : "n"}
                  </kbd>
                </button>
              ))}
            </div>

            <div>
              <p className="mb-1.5 text-xs font-medium text-muted">
                Why? <span className="font-normal">press 1–9</span>
              </p>
              <div className="flex flex-wrap gap-1.5">
                {visibleTags.map((t, i) => (
                  <Chip
                    key={t}
                    active={tags.includes(t)}
                    onClick={() => toggleTag(t)}
                    title={`${i + 1} — ${POSITIVE_TAGS.has(t) ? "positive" : "negative"}`}
                  >
                    <span className="mr-1 opacity-50">{i + 1}</span>
                    {t}
                  </Chip>
                ))}
              </div>
            </div>

            <input
              ref={noteRef}
              value={note}
              onChange={(e) => setNote(e.target.value)}
              placeholder="Optional note (r to focus, Esc to leave)"
              className="w-full rounded border border-border bg-bg px-3 py-2 text-sm outline-none focus:border-accent"
            />

            <div className="flex items-center justify-between text-xs text-muted">
              <button onClick={advance} className="hover:text-fg">
                Skip <kbd>s</kbd>
              </button>
              <button
                onClick={() => void undo()}
                disabled={lastLabeled === null}
                className="hover:text-fg disabled:opacity-40"
              >
                Undo last <kbd>u</kbd>
              </button>
            </div>

            <p className="text-[11px] leading-relaxed text-muted">
              A verdict applies to the whole role, so duplicate postings on other boards are
              labelled too and never come back.
            </p>
          </aside>
        </div>
      )}
    </div>
  );
}
