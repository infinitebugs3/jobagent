"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { API, api } from "../lib/api";
import type { JobRow, SearchProfile } from "../lib/types";

const FACET_FIELDS: [string, string][] = [
  ["location_bucket", "Locations"],
  ["seniority", "Seniority"],
  ["remote_type", "Work type"],
  ["company", "Companies"],
];

type Preview = { total: number; terms: string[]; sample: JobRow[] };

/**
 * `filters` is untyped jsonb: mostly lists of facet values, but scalars live
 * there too (max_years_required, suggested_max_years). These text boxes only
 * edit the list dimensions in FACET_FIELDS, so anything else is left alone
 * rather than coerced into a comma-separated string and written back wrong.
 */
const asList = (v: string[] | number | undefined): string[] => (Array.isArray(v) ? v : []);

export default function SearchesPage() {
  const [profiles, setProfiles] = useState<SearchProfile[]>([]);
  const [editing, setEditing] = useState<SearchProfile | null>(null);
  const [preview, setPreview] = useState<Preview | null>(null);
  const [status, setStatus] = useState<string | null>(null);

  const reload = () => api.profiles().then(setProfiles).catch((e) => setStatus(String(e)));
  useEffect(() => { reload(); }, []);

  // Preview the *saved* state, not the draft: the endpoint reads the stored row.
  // Save, then look — which also makes it obvious that saving is what counts.
  useEffect(() => {
    if (!editing?.id) return setPreview(null);
    fetch(`${API}/api/search-profiles/${editing.id}/preview`, { cache: "no-store" })
      .then((r) => r.json())
      .then(setPreview)
      .catch(() => setPreview(null));
  }, [editing?.id, status]);

  const blank = (): SearchProfile => ({
    id: 0,
    name: "",
    query_text: "",
    exclude_text: "",
    filters: {},
    is_default: false,
  });

  const save = async () => {
    if (!editing) return;
    try {
      const saved = await api.saveProfile({
        ...editing,
        id: editing.id || undefined,
      });
      setStatus(`Saved “${saved.name}”`);
      setEditing(saved);
      reload();
    } catch (e) {
      setStatus(String(e));
    }
  };

  const setFilter = (key: string, csv: string) => {
    if (!editing) return;
    const values = csv.split(",").map((s) => s.trim()).filter(Boolean);
    const next = { ...editing.filters };
    if (values.length) next[key] = values;
    else delete next[key];
    setEditing({ ...editing, filters: next });
  };

  return (
    <div className="grid gap-6 lg:grid-cols-[18rem_1fr]">
      <aside className="space-y-2">
        <div className="flex items-center justify-between">
          <h1 className="text-lg font-semibold">Saved searches</h1>
          <button
            onClick={() => setEditing(blank())}
            className="rounded bg-accent px-2 py-1 text-xs font-medium text-white"
          >
            New
          </button>
        </div>
        <ul className="space-y-1">
          {profiles.map((p) => (
            <li key={p.id}>
              <button
                onClick={() => setEditing(p)}
                className={`w-full rounded border px-3 py-2 text-left text-sm ${
                  editing?.id === p.id
                    ? "border-accent bg-accent/10"
                    : "border-border bg-surface hover:border-accent/50"
                }`}
              >
                <span className="font-medium">{p.name}</span>
                {p.is_default && <span className="ml-2 text-xs text-muted">default</span>}
                <span className="mt-0.5 block truncate text-xs text-muted">
                  {p.query_text || "no terms"}
                </span>
              </button>
            </li>
          ))}
        </ul>
        {profiles.length === 0 && (
          <p className="text-xs text-muted">
            None yet. Run <code>jobagent profile-import</code> to build one from profile.yaml.
          </p>
        )}
      </aside>

      {editing ? (
        <section className="space-y-4">
          <Field label="Name">
            <input
              value={editing.name}
              onChange={(e) => setEditing({ ...editing, name: e.target.value })}
              className="w-full rounded border border-border bg-surface px-3 py-2 text-sm outline-none focus:border-accent"
            />
          </Field>

          <Field
            label="Search terms"
            hint="Comma separated. Each term is scored separately, and a job matching more of them ranks higher."
          >
            <textarea
              rows={3}
              value={editing.query_text}
              onChange={(e) => setEditing({ ...editing, query_text: e.target.value })}
              className="w-full rounded border border-border bg-surface px-3 py-2 text-sm outline-none focus:border-accent"
            />
          </Field>

          <Field
            label="Exclude titles"
            hint="Hard filter on the title only — a job matching any of these never appears."
          >
            <textarea
              rows={2}
              value={editing.exclude_text}
              onChange={(e) => setEditing({ ...editing, exclude_text: e.target.value })}
              className="w-full rounded border border-border bg-surface px-3 py-2 text-sm outline-none focus:border-accent"
            />
          </Field>

          <div className="grid gap-3 sm:grid-cols-2">
            {FACET_FIELDS.map(([key, label]) => (
              <Field key={key} label={label}>
                <input
                  value={asList(editing.filters[key]).join(", ")}
                  onChange={(e) => setFilter(key, e.target.value)}
                  placeholder="comma separated"
                  className="w-full rounded border border-border bg-surface px-3 py-2 text-sm outline-none focus:border-accent"
                />
              </Field>
            ))}
          </div>

          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={editing.is_default}
              onChange={(e) => setEditing({ ...editing, is_default: e.target.checked })}
            />
            Use as my default search
          </label>

          <div className="flex items-center gap-3">
            <button
              onClick={() => void save()}
              className="rounded bg-accent px-4 py-2 text-sm font-medium text-white hover:opacity-90"
            >
              Save
            </button>
            {editing.id > 0 && (
              <Link
                href={`/jobs?profile_id=${editing.id}&sort=relevance`}
                className="text-sm text-accent hover:underline"
              >
                Browse with this search →
              </Link>
            )}
            <span className="text-xs text-muted">{status}</span>
          </div>

          {preview && (
            <div className="rounded-lg border border-border bg-surface p-4">
              <p className="text-sm">
                <span className="font-medium">{preview.total.toLocaleString()}</span> jobs match
                <span className="text-muted"> · {preview.terms.length} terms after synonyms</span>
              </p>
              <ul className="mt-2 space-y-1 text-sm">
                {preview.sample.map((j) => (
                  <li key={j.id} className="flex justify-between gap-3">
                    <Link href={`/jobs/${j.id}`} className="truncate hover:text-accent">
                      {j.title} <span className="text-muted">— {j.company}</span>
                    </Link>
                    <span className="shrink-0 text-xs tabular-nums text-muted">
                      {j.rank_score.toFixed(0)}
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </section>
      ) : (
        <section className="rounded-lg border border-border bg-surface p-8 text-sm text-muted">
          Pick a saved search to edit, or create one. A saved search is terms plus filters — it
          drives both the browse page and the review queue.
        </section>
      )}
    </div>
  );
}

function Field({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: React.ReactNode;
}) {
  return (
    <div>
      <label className="mb-1 block text-xs font-semibold uppercase tracking-wide text-muted">
        {label}
      </label>
      {children}
      {hint && <p className="mt-1 text-xs text-muted">{hint}</p>}
    </div>
  );
}
