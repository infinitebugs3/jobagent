import type {
  Facets,
  JobDetail,
  JobPage,
  ReviewProgress,
  RoleGroup,
  SearchProfile,
  Verdict,
} from "./types";

export const API = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

async function get<T>(path: string, params?: URLSearchParams): Promise<T> {
  const qs = params?.toString();
  // no-store throughout: the whole point of the tool is that it reflects what
  // is in the database right now, and a stale label is a wasted review.
  const res = await fetch(`${API}${path}${qs ? `?${qs}` : ""}`, { cache: "no-store" });
  if (!res.ok) throw new Error(`${path} -> ${res.status} ${await res.text()}`);
  return res.json();
}

/** Filter state lives in the URL, so every view is shareable and back works. */
export type Filters = {
  q?: string;
  profile_id?: string;
  role?: string[];
  company?: string[];
  location?: string[];
  remote?: string[];
  seniority?: string[];
  provider?: string[];
  max_age_days?: string;
  max_years_required?: string;
  label_status?: string;
  has_description?: string;
  sort?: string;
  page?: string;
  page_size?: string;
};

const MULTI = ["role", "company", "location", "remote", "seniority", "provider"] as const;

export function toParams(f: Filters): URLSearchParams {
  const p = new URLSearchParams();
  for (const key of MULTI) (f[key] ?? []).forEach((v) => p.append(key, v));
  for (const key of [
    "q",
    "profile_id",
    "max_age_days",
    "max_years_required",
    "label_status",
    "has_description",
    "sort",
    "page",
    "page_size",
  ] as const) {
    const v = f[key];
    // Explicitly not `if (v)`. "0" is falsy, and max_years_required="0" is a
    // real ask -- entry level only -- that truthiness would turn into no
    // filter at all.
    if (v !== undefined && v !== "") p.set(key, String(v));
  }
  return p;
}

/** searchParams -> Filters. Next gives a string for one value, array for many. */
export function fromSearchParams(sp: Record<string, string | string[] | undefined>): Filters {
  const arr = (v: string | string[] | undefined) =>
    v === undefined ? [] : Array.isArray(v) ? v : [v];
  const one = (v: string | string[] | undefined) => (Array.isArray(v) ? v[0] : v);
  return {
    q: one(sp.q),
    profile_id: one(sp.profile_id),
    role: arr(sp.role),
    company: arr(sp.company),
    location: arr(sp.location),
    remote: arr(sp.remote),
    seniority: arr(sp.seniority),
    provider: arr(sp.provider),
    max_age_days: one(sp.max_age_days),
    max_years_required: one(sp.max_years_required),
    label_status: one(sp.label_status),
    has_description: one(sp.has_description),
    sort: one(sp.sort),
    page: one(sp.page),
    page_size: one(sp.page_size),
  };
}

export const api = {
  jobs: (f: Filters) => get<JobPage>("/api/jobs", toParams(f)),
  job: (id: number) => get<JobDetail>(`/api/jobs/${id}`),
  facets: (f: Filters) => get<Facets>("/api/facets", toParams(f)),
  roles: () => get<RoleGroup[]>("/api/roles"),
  profiles: () => get<SearchProfile[]>("/api/search-profiles"),
  progress: () => get<ReviewProgress>("/api/review/progress"),
  queue: (f: Filters, explore?: boolean) => {
    const p = toParams(f);
    if (explore !== undefined) p.set("explore", String(explore));
    return get<JobPage>("/api/review/queue", p);
  },

  label: async (body: {
    job_id: number;
    verdict: Verdict;
    reason?: string;
    reason_tags?: string[];
    source?: string;
  }) => {
    const res = await fetch(`${API}/api/labels`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!res.ok) throw new Error(await res.text());
    return res.json() as Promise<{ postings_labeled: number }>;
  },

  unlabel: async (jobId: number) => {
    const res = await fetch(`${API}/api/labels/${jobId}`, { method: "DELETE" });
    if (!res.ok) throw new Error(await res.text());
    return res.json();
  },

  saveProfile: async (p: Omit<SearchProfile, "id"> & { id?: number }) => {
    const { id, ...body } = p;
    const res = await fetch(
      id ? `${API}/api/search-profiles/${id}` : `${API}/api/search-profiles`,
      {
        method: id ? "PATCH" : "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(body),
      },
    );
    if (!res.ok) throw new Error(await res.text());
    return res.json() as Promise<SearchProfile>;
  },
};
