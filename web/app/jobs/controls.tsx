"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { toParams, type Filters } from "../lib/api";
import type { Facets, FacetValue, RoleGroup, SearchProfile } from "../lib/types";
import { Chip } from "../components/Chips";
import { ExperienceSlider } from "../components/ExperienceSlider";

/**
 * The equality dimensions -- the ones matched with `= any(...)` and rendered as
 * checkbox lists. min_years_experience is counted alongside them and shown in
 * the same rail, but it is a cumulative threshold, so it is deliberately not
 * one of these.
 */
type EqualityFacet = Exclude<keyof Facets, "min_years_experience">;

const FACET_LABEL: Record<EqualityFacet, string> = {
  location_bucket: "Location",
  seniority: "Seniority",
  remote_type: "Work type",
  company: "Company",
  provider: "Job board",
};

/** Which Filters key each facet dimension writes to. */
const FACET_KEY: Record<EqualityFacet, keyof Filters> = {
  location_bucket: "location",
  seniority: "seniority",
  remote_type: "remote",
  company: "company",
  provider: "provider",
};

const AGE_OPTIONS: [string, string][] = [
  ["1", "24 hours"],
  ["7", "7 days"],
  ["14", "14 days"],
  ["30", "30 days"],
];

const LABEL_OPTIONS: [string, string][] = [
  ["unlabeled", "Not yet judged"],
  ["apply", "Interested"],
  ["maybe", "Maybe"],
  ["no", "Not for me"],
];

export function SearchBar({
  filters,
  roles,
  profiles,
}: {
  filters: Filters;
  roles: RoleGroup[];
  profiles: SearchProfile[];
}) {
  const router = useRouter();
  const [q, setQ] = useState(filters.q ?? "");

  // Every navigation resets to page 1. Landing on page 12 of a query that now
  // has 3 pages is a blank screen with no explanation.
  const go = (patch: Partial<Filters>) =>
    router.push(`/jobs?${toParams({ ...filters, ...patch, page: "1" })}`);

  const toggleRole = (slug: string) => {
    const active = filters.role ?? [];
    go({ role: active.includes(slug) ? active.filter((r) => r !== slug) : [...active, slug] });
  };

  return (
    <div className="space-y-3">
      <form
        onSubmit={(e) => {
          e.preventDefault();
          go({ q, sort: q ? "relevance" : "newest" });
        }}
        className="flex flex-wrap gap-2"
      >
        <input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="Search terms, comma separated — spring boot, rag, kubernetes"
          className="min-w-64 flex-1 rounded border border-border bg-surface px-3 py-2 text-sm outline-none focus:border-accent"
        />
        <select
          value={filters.profile_id ?? ""}
          onChange={(e) => go({ profile_id: e.target.value || undefined })}
          className="rounded border border-border bg-surface px-2 py-2 text-sm"
        >
          <option value="">No saved search</option>
          {profiles.map((p) => (
            <option key={p.id} value={String(p.id)}>
              {p.name}
            </option>
          ))}
        </select>
        <button
          type="submit"
          className="rounded bg-accent px-4 py-2 text-sm font-medium text-white hover:opacity-90"
        >
          Search
        </button>
      </form>

      <div className="flex flex-wrap items-center gap-1.5">
        <span className="mr-1 text-xs font-medium text-muted">Role</span>
        {roles.map((r) => (
          <Chip
            key={r.slug}
            active={(filters.role ?? []).includes(r.slug)}
            onClick={() => toggleRole(r.slug)}
          >
            {r.label}
          </Chip>
        ))}
      </div>
    </div>
  );
}

export function FilterRail({
  filters,
  facets,
  suggestedYears,
}: {
  filters: Filters;
  facets: Facets | null;
  suggestedYears?: number | null;
}) {
  const router = useRouter();
  const go = (patch: Partial<Filters>) =>
    router.push(`/jobs?${toParams({ ...filters, ...patch, page: "1" })}`);

  const toggle = (key: keyof Filters, value: string) => {
    const active = (filters[key] as string[] | undefined) ?? [];
    go({
      [key]: active.includes(value) ? active.filter((v) => v !== value) : [...active, value],
    } as Partial<Filters>);
  };

  const activeCount =
    (["role", "company", "location", "remote", "seniority", "provider"] as const).reduce(
      (n, k) => n + ((filters[k] ?? []).length as number),
      0,
    ) +
    (filters.max_age_days ? 1 : 0) +
    (filters.label_status ? 1 : 0) +
    // `!== undefined`, not truthiness: "0" is a real ceiling.
    (filters.max_years_required !== undefined ? 1 : 0);

  return (
    <aside className="space-y-5 text-sm">
      {activeCount > 0 && (
        <button
          onClick={() => router.push("/jobs")}
          className="text-xs text-accent hover:underline"
        >
          Clear all {activeCount} filters
        </button>
      )}

      <Group title="Judged">
        <div className="space-y-1">
          {LABEL_OPTIONS.map(([value, label]) => (
            <Radio
              key={value}
              checked={filters.label_status === value}
              label={label}
              onClick={() =>
                go({ label_status: filters.label_status === value ? undefined : value })
              }
            />
          ))}
        </div>
      </Group>

      <Group title="Posted within">
        <div className="space-y-1">
          {AGE_OPTIONS.map(([value, label]) => (
            <Radio
              key={value}
              checked={filters.max_age_days === value}
              label={label}
              onClick={() =>
                go({ max_age_days: filters.max_age_days === value ? undefined : value })
              }
            />
          ))}
        </div>
      </Group>

      <Group title="Experience asked for">
        <ExperienceSlider
          value={filters.max_years_required}
          buckets={facets?.min_years_experience ?? null}
          suggested={suggestedYears}
          onCommit={(next) => go({ max_years_required: next })}
        />
      </Group>

      {facets &&
        (Object.keys(FACET_LABEL) as EqualityFacet[]).map((dim) => {
          const values = facets[dim] ?? [];
          if (values.length === 0) return null;
          const key = FACET_KEY[dim];
          const active = (filters[key] as string[] | undefined) ?? [];
          return (
            <Group key={dim} title={FACET_LABEL[dim]}>
              <FacetList
                values={values}
                active={active}
                onToggle={(v) => toggle(key, v)}
              />
            </Group>
          );
        })}
    </aside>
  );
}

function Group({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div>
      <h3 className="mb-1.5 text-xs font-semibold uppercase tracking-wide text-muted">{title}</h3>
      {children}
    </div>
  );
}

function Radio({
  checked,
  label,
  onClick,
}: {
  checked: boolean;
  label: string;
  onClick: () => void;
}) {
  return (
    <button
      onClick={onClick}
      className={`block w-full text-left text-sm ${checked ? "font-medium text-accent" : "text-muted hover:text-fg"}`}
    >
      {checked ? "● " : "○ "}
      {label}
    </button>
  );
}

function FacetList({
  values,
  active,
  onToggle,
}: {
  values: FacetValue[];
  active: string[];
  onToggle: (v: string) => void;
}) {
  const [expanded, setExpanded] = useState(false);
  // Long tails are real: 600+ location buckets. Show the head, keep selected
  // values visible even when they fall outside it, and let the rest be opened.
  const head = expanded ? values : values.slice(0, 8);
  const shown = [
    ...head,
    ...values.filter((v) => active.includes(v.value) && !head.includes(v)),
  ];
  return (
    <div className="space-y-1">
      {shown.map((v) => (
        <button
          key={v.value}
          onClick={() => onToggle(v.value)}
          className="flex w-full items-baseline justify-between gap-2 text-left"
        >
          <span
            className={`truncate ${active.includes(v.value) ? "font-medium text-accent" : "text-muted hover:text-fg"}`}
          >
            {active.includes(v.value) ? "☑ " : "☐ "}
            {v.value}
          </span>
          <span className="shrink-0 text-xs tabular-nums text-muted opacity-70">{v.n}</span>
        </button>
      ))}
      {values.length > 8 && (
        <button
          onClick={() => setExpanded((e) => !e)}
          className="text-xs text-accent hover:underline"
        >
          {expanded ? "Show less" : `Show all ${values.length}`}
        </button>
      )}
    </div>
  );
}
