export type Verdict = "apply" | "maybe" | "no";

/**
 * Stored verdicts stay apply/maybe/no to match the day-1 database constraint.
 * The UI speaks in terms of interest, which is what you are actually judging
 * at review time -- whether to spend an hour on an application.
 */
export const VERDICT_LABEL: Record<Verdict, string> = {
  apply: "Interested",
  maybe: "Maybe",
  no: "Not for me",
};

export const VERDICT_KEY: Record<Verdict, string> = { apply: "a", maybe: "m", no: "n" };

export type JobRow = {
  id: number;
  title: string;
  company: string;
  company_id: number;
  location: string | null;
  location_bucket: string | null;
  seniority: string | null;
  remote_type: string | null;
  provider: string | null;
  apply_url: string;
  dedupe_key: string;
  posted_at: string | null;
  first_seen_at: string | null;
  age_days: number | null;
  has_description: boolean;
  /** Years the description asks for. null means it says nothing, not zero. */
  min_years_experience: number | null;
  /** The sentence that number was read from, for the tooltip. */
  min_years_evidence: string | null;
  score: number;
  rank_score: number;
  matched_terms: string[];
  verdict: Verdict | null;
  reason: string | null;
  reason_tags: string[];
  label_source: string | null;
};

export type JobDetail = JobRow & {
  description_md: string | null;
  careers_url: string | null;
};

export type JobPage = {
  items: JobRow[];
  total: number;
  /** Roles the experience ceiling removed. 0 when the slider is at Any. */
  hidden_by_experience: number;
  page: number;
  page_size: number;
  terms: string[];
  took_ms: number;
};

export type FacetValue = { value: string; n: number };
export type Facets = {
  company: FacetValue[];
  location_bucket: FacetValue[];
  remote_type: FacetValue[];
  seniority: FacetValue[];
  provider: FacetValue[];
  /** One row per stated bar, plus a "none" row. Cumulative, not selectable. */
  min_years_experience: FacetValue[];
};

export type RoleGroup = { slug: string; label: string; n: number };

export type SearchProfile = {
  id: number;
  name: string;
  query_text: string;
  exclude_text: string;
  /** Mostly lists of facet values; max_years_required is a scalar. */
  filters: Record<string, string[] | number>;
  is_default: boolean;
};

export type ReviewProgress = {
  target: number;
  labeled: number;
  apply: number;
  maybe: number;
  no: number;
  review: number;
  exploration: number;
};

/**
 * One-click reasons. Free text cannot be aggregated; these can, and rung 1 of
 * the personalization ladder is nothing more than counting them.
 */
export const REASON_TAGS = [
  "great-fit",
  "interesting-domain",
  "good-company",
  "too-senior",
  "too-junior",
  "wrong-stack",
  "wrong-role",
  "bad-location",
  "low-comp",
  "big-corp",
  "vague-jd",
] as const;

/** Which verdicts each tag makes sense for, so the chip row stays short. */
export const POSITIVE_TAGS = new Set(["great-fit", "interesting-domain", "good-company"]);
