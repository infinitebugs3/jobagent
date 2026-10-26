import Link from "next/link";
import { api, fromSearchParams, toParams, type Filters } from "../lib/api";
import type { Facets, JobPage } from "../lib/types";
import { ExperienceBadge } from "../components/Chips";
import { FilterRail, SearchBar } from "./controls";

export const dynamic = "force-dynamic";

/**
 * Browse is a server component reading straight from searchParams, so every
 * view is a shareable URL, the back button works, and phase 3 gets SSR for the
 * public job pages without a rewrite.
 */
export default async function JobsPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const sp = await searchParams;
  const filters = fromSearchParams(sp);
  if (!filters.sort) filters.sort = filters.q || filters.profile_id ? "relevance" : "newest";
  if (!filters.page_size) filters.page_size = "25";

  const [roles, profiles] = await Promise.all([
    api.roles().catch(() => []),
    api.profiles().catch(() => []),
  ]);

  // Advisory only -- compiled from profile.yaml's years_experience and never
  // applied on its own. Nothing is hidden until the slider is moved.
  const suggested = profiles.find((p) => p.is_default)?.filters?.suggested_max_years;
  const suggestedYears = typeof suggested === "number" ? suggested : null;

  let page: JobPage | null = null;
  let facets: Facets | null = null;
  let error: string | null = null;
  try {
    [page, facets] = await Promise.all([api.jobs(filters), api.facets(filters)]);
  } catch (e) {
    error = String(e);
  }

  const current = Number(filters.page ?? 1);
  const pageSize = Number(filters.page_size);
  const lastPage = page ? Math.max(1, Math.ceil(page.total / pageSize)) : 1;
  const href = (patch: Partial<Filters>) => `/jobs?${toParams({ ...filters, ...patch })}`;

  return (
    <div className="space-y-5">
      <SearchBar filters={filters} roles={roles} profiles={profiles} />

      {error && (
        <p className="rounded border border-rose-500/40 bg-rose-500/10 px-3 py-2 text-sm text-rose-500">
          {error} — is the API running on {process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000"}?
        </p>
      )}

      {page && (
        <div className="grid gap-6 lg:grid-cols-[15rem_1fr]">
          <FilterRail filters={filters} facets={facets} suggestedYears={suggestedYears} />

          <section className="space-y-3">
            <div className="flex items-center justify-between text-sm text-muted">
              <p>
                <span className="font-medium text-fg">{page.total.toLocaleString()}</span> jobs
                {page.terms.length > 0 && (
                  <> · matching {page.terms.slice(0, 4).join(", ")}
                    {page.terms.length > 4 ? ` +${page.terms.length - 4}` : ""}</>
                )}
                <span className="ml-2 opacity-60">{page.took_ms}ms</span>
              </p>
              <SortLinks filters={filters} />
            </div>

            {/* Nothing else in this app tells you what a filter took away.
                Dedupe drops ~19% and the exclusion list drops more, both
                silently; a ceiling you cannot see the cost of is one you
                cannot judge. */}
            {page.hidden_by_experience > 0 && (
              <p className="rounded-lg border border-border bg-surface px-3 py-2 text-sm text-muted">
                <span className="font-medium text-fg">
                  {page.hidden_by_experience.toLocaleString()}
                </span>{" "}
                {page.hidden_by_experience === 1 ? "role asks" : "roles ask"} for more experience
                than you selected.{" "}
                <Link href={href({ max_years_required: undefined })} className="text-accent hover:underline">
                  show them
                </Link>
              </p>
            )}

            {page.items.length === 0 ? (
              <p className="rounded-lg border border-border bg-surface p-8 text-center text-sm text-muted">
                Nothing matches. Drop a filter, or widen the search terms.
              </p>
            ) : (
              <ul className="space-y-2">
                {page.items.map((job) => (
                  <li
                    key={job.id}
                    className="rounded-lg border border-border bg-surface p-4 transition-colors hover:border-accent/50"
                  >
                    <div className="flex items-start justify-between gap-4">
                      <div className="min-w-0">
                        <Link
                          href={`/jobs/${job.id}`}
                          className="font-medium leading-snug hover:text-accent"
                        >
                          {job.title}
                        </Link>
                        <p className="mt-0.5 truncate text-sm text-muted">
                          {job.company}
                          {job.location ? ` — ${job.location}` : ""}
                        </p>
                        <div className="mt-1.5 flex flex-wrap items-center gap-2 text-xs text-muted">
                          {[job.location_bucket, job.seniority, job.remote_type]
                            .filter(Boolean)
                            .map((b, i) => (
                              <span key={i}>{b}</span>
                            ))}
                          {job.age_days !== null && <span>{job.age_days}d</span>}
                          <ExperienceBadge job={job} ceiling={filters.max_years_required} />
                        </div>
                        {job.matched_terms.length > 0 && (
                          <div className="mt-2 flex flex-wrap gap-1">
                            {job.matched_terms.map((t) => (
                              <span
                                key={t}
                                className="rounded bg-accent/10 px-1.5 py-0.5 text-[11px] text-accent"
                              >
                                {t}
                              </span>
                            ))}
                          </div>
                        )}
                      </div>
                      <div className="shrink-0 space-y-1 text-right">
                        {job.rank_score > 0 && (
                          <p className="text-xs tabular-nums text-muted">
                            {job.rank_score.toFixed(0)}
                          </p>
                        )}
                        {job.verdict && (
                          <span
                            className={`block rounded px-1.5 py-0.5 text-[11px] font-medium ${
                              job.verdict === "apply"
                                ? "bg-emerald-500/15 text-emerald-600 dark:text-emerald-400"
                                : job.verdict === "maybe"
                                  ? "bg-amber-500/15 text-amber-600 dark:text-amber-400"
                                  : "bg-rose-500/15 text-rose-600 dark:text-rose-400"
                            }`}
                          >
                            {job.verdict === "apply"
                              ? "Interested"
                              : job.verdict === "maybe"
                                ? "Maybe"
                                : "Not for me"}
                          </span>
                        )}
                      </div>
                    </div>
                  </li>
                ))}
              </ul>
            )}

            {lastPage > 1 && (
              <nav className="flex items-center justify-between pt-2 text-sm">
                {current > 1 ? (
                  <Link href={href({ page: String(current - 1) })} className="text-accent hover:underline">
                    ← Previous
                  </Link>
                ) : (
                  <span />
                )}
                <span className="text-muted">
                  Page {current} of {lastPage.toLocaleString()}
                </span>
                {current < lastPage ? (
                  <Link href={href({ page: String(current + 1) })} className="text-accent hover:underline">
                    Next →
                  </Link>
                ) : (
                  <span />
                )}
              </nav>
            )}
          </section>
        </div>
      )}
    </div>
  );
}

function SortLinks({ filters }: { filters: Filters }) {
  const options: [string, string][] = [
    ["relevance", "Relevance"],
    ["newest", "Newest"],
    ["company", "Company"],
  ];
  const searching = Boolean(filters.q || filters.profile_id);
  return (
    <div className="flex gap-3">
      {options.map(([value, label]) => {
        // Relevance with nothing to be relevant to is meaningless; the server
        // silently falls back, so don't offer it as if it did something.
        if (value === "relevance" && !searching) return null;
        const active = (filters.sort ?? "newest") === value;
        return (
          <Link
            key={value}
            href={`/jobs?${toParams({ ...filters, sort: value, page: "1" })}`}
            className={active ? "font-medium text-fg" : "hover:text-fg"}
          >
            {label}
          </Link>
        );
      })}
    </div>
  );
}
