"use client";

import type { JobRow } from "../lib/types";

export function Chip({
  active,
  onClick,
  children,
  title,
}: {
  active?: boolean;
  onClick?: () => void;
  children: React.ReactNode;
  title?: string;
}) {
  return (
    <button
      type="button"
      title={title}
      onClick={onClick}
      className={`rounded-full border px-3 py-1 text-xs transition-colors ${
        active
          ? "border-accent bg-accent text-white"
          : "border-border bg-surface text-muted hover:text-fg"
      }`}
    >
      {children}
    </button>
  );
}

/** Small facts about a job that fit on one line. */
export function JobMeta({ job, ceiling }: { job: JobRow; ceiling?: string }) {
  const bits = [
    job.location_bucket,
    job.seniority,
    job.remote_type !== "onsite" ? job.remote_type : null,
    job.age_days !== null ? `${job.age_days}d old` : null,
  ].filter(Boolean);
  return (
    <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-muted">
      {bits.map((b, i) => (
        <span key={i} className="after:ml-2 after:content-['·'] last:after:content-['']">
          {b}
        </span>
      ))}
      <ExperienceBadge job={job} ceiling={ceiling} />
    </div>
  );
}

/**
 * What the description asks for, next to the seniority the *title* implies --
 * which is the whole point, since those two disagree on about a third of the
 * postings that pass a junior/mid filter.
 *
 * Roles within two years of the current ceiling are highlighted as a stretch,
 * so "just inside the line" is visible rather than looking identical to a
 * comfortable match. The tooltip is the sentence the number was read from, so
 * a wrong-looking number can always be checked.
 */
export function ExperienceBadge({ job, ceiling }: { job: JobRow; ceiling?: string }) {
  const years = job.min_years_experience;
  if (years === null || years === undefined) return null;
  const limit = ceiling === undefined || ceiling === "" ? null : Number(ceiling);
  const stretch = limit !== null && years > limit - 2;
  return (
    <span
      title={job.min_years_evidence ?? undefined}
      className={`rounded px-1.5 py-0.5 text-[11px] ${
        stretch ? "bg-amber-500/15 text-amber-600 dark:text-amber-400" : "bg-fg/5"
      }`}
    >
      {years}+ yrs
    </span>
  );
}

/**
 * Why this job is on screen. Comes straight from the search's matched_terms, so
 * ranking never has to be taken on faith.
 */
export function MatchedTerms({ terms }: { terms: string[] }) {
  if (!terms?.length) return null;
  return (
    <div className="flex flex-wrap gap-1">
      {terms.map((t) => (
        <span key={t} className="rounded bg-accent/10 px-1.5 py-0.5 text-[11px] text-accent">
          {t}
        </span>
      ))}
    </div>
  );
}

export function VerdictBadge({ verdict }: { verdict: string | null }) {
  if (!verdict) return null;
  const style =
    verdict === "apply"
      ? "bg-emerald-500/15 text-emerald-600 dark:text-emerald-400"
      : verdict === "maybe"
        ? "bg-amber-500/15 text-amber-600 dark:text-amber-400"
        : "bg-rose-500/15 text-rose-600 dark:text-rose-400";
  const label =
    verdict === "apply" ? "Interested" : verdict === "maybe" ? "Maybe" : "Not for me";
  return <span className={`rounded px-1.5 py-0.5 text-[11px] font-medium ${style}`}>{label}</span>;
}
