import Link from "next/link";
import { notFound } from "next/navigation";
import { api } from "../../lib/api";
import { JobDescription } from "../../components/JobDescription";
import { LabelControl } from "./LabelControl";

export const dynamic = "force-dynamic";

export default async function JobDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const job = await api.job(Number(id)).catch(() => null);
  if (!job) notFound();

  const facts: [string, string | null][] = [
    ["Company", job.company],
    ["Location", job.location],
    ["Bucket", job.location_bucket],
    ["Seniority", job.seniority],
    // From the description, unlike Seniority above, which is read off the
    // title. Where they disagree, this one is the real bar.
    ["Experience", job.min_years_experience !== null ? `${job.min_years_experience}+ yrs` : null],
    ["Work type", job.remote_type],
    ["Posted", job.posted_at ? new Date(job.posted_at).toLocaleDateString() : null],
    ["First seen", job.first_seen_at ? new Date(job.first_seen_at).toLocaleDateString() : null],
    ["Board", job.provider],
  ];

  return (
    <div className="grid gap-6 lg:grid-cols-[1fr_18rem]">
      <article className="min-w-0 space-y-4">
        <Link href="/jobs" className="text-sm text-muted hover:text-fg">
          ← Back to browse
        </Link>
        <header className="space-y-2">
          <h1 className="text-2xl font-semibold leading-tight">{job.title}</h1>
          <p className="text-muted">
            {job.company}
            {job.location ? ` — ${job.location}` : ""}
          </p>
          {job.matched_terms.length > 0 && (
            <div className="flex flex-wrap gap-1">
              {job.matched_terms.map((t) => (
                <span key={t} className="rounded bg-accent/10 px-1.5 py-0.5 text-xs text-accent">
                  {t}
                </span>
              ))}
            </div>
          )}
        </header>

        <a
          href={job.apply_url}
          target="_blank"
          rel="noreferrer"
          className="inline-block rounded bg-accent px-4 py-2 text-sm font-medium text-white hover:opacity-90"
        >
          Open the posting →
        </a>

        <div className="rounded-lg border border-border bg-surface p-5">
          {job.description_md ? (
            <JobDescription text={job.description_md} />
          ) : (
            <p className="text-sm text-muted">
              This board does not publish descriptions in its API. Open the posting to read it.
            </p>
          )}
        </div>
      </article>

      <aside className="space-y-5">
        <LabelControl
          jobId={job.id}
          initialVerdict={job.verdict}
          initialTags={job.reason_tags}
          initialReason={job.reason ?? ""}
        />

        <dl className="space-y-1.5 text-sm">
          {facts
            .filter(([, v]) => v)
            .map(([k, v]) => (
              <div key={k} className="flex justify-between gap-3">
                <dt className="text-muted">{k}</dt>
                <dd className="text-right">{v}</dd>
              </div>
            ))}
        </dl>

        {job.careers_url && (
          <a
            href={job.careers_url}
            target="_blank"
            rel="noreferrer"
            className="block text-sm text-accent hover:underline"
          >
            All {job.company} roles →
          </a>
        )}
      </aside>
    </div>
  );
}
