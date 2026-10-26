"use client";

import { useEffect, useState } from "react";
import type { FacetValue } from "../lib/types";

/** Highest bar the slider can express before it means "no ceiling". */
export const MAX_YEARS = 15;
const ANY = MAX_YEARS + 1;

/**
 * How many roles survive a given ceiling.
 *
 * Cumulative, not per-bucket: picking 3 means "3 years or fewer", so it is the
 * sum of every bucket at or below it. The 'none' bucket is added at every
 * position including 0 -- those postings state no requirement and are never
 * hidden, which is the invariant this whole filter rests on.
 */
export function shownAtCeiling(buckets: FacetValue[], ceiling: number): number {
  return buckets.reduce((n, b) => {
    if (b.value === "none") return n + b.n;
    return Number(b.value) <= ceiling ? n + b.n : n;
  }, 0);
}

export function ExperienceSlider({
  value,
  buckets,
  suggested,
  onCommit,
  compact = false,
}: {
  value: string | undefined;
  buckets: FacetValue[] | null;
  suggested?: number | null;
  onCommit: (next: string | undefined) => void;
  compact?: boolean;
}) {
  const fromUrl = value === undefined || value === "" ? ANY : Number(value);
  const [pos, setPos] = useState(fromUrl);

  // The URL is the source of truth; the thumb is a draft of it.
  useEffect(() => setPos(fromUrl), [fromUrl]);

  // Committing on every `change` would push a router navigation per step of the
  // drag -- a dozen refetches to land on one number. The thumb moves locally
  // and the URL is only written when the interaction ends.
  const commit = () => onCommit(pos === ANY ? undefined : String(pos));

  const noneCount = buckets?.find((b) => b.value === "none")?.n ?? 0;
  const total = buckets ? shownAtCeiling(buckets, MAX_YEARS + 1) : null;
  const shown = buckets ? shownAtCeiling(buckets, pos === ANY ? MAX_YEARS + 1 : pos) : null;

  return (
    <div className={compact ? "space-y-1" : "space-y-1.5"}>
      <div className="flex items-baseline justify-between gap-2">
        <span className={pos === ANY ? "text-muted" : "font-medium text-accent"}>
          {pos === ANY ? "Any experience" : pos === 0 ? "Entry level only" : `Up to ${pos} yrs`}
        </span>
        {shown !== null && (
          <span className="shrink-0 text-xs tabular-nums text-muted opacity-70">
            {shown.toLocaleString()}
            {total !== null && pos === ANY ? "" : ` of ${total?.toLocaleString()}`}
          </span>
        )}
      </div>

      <input
        type="range"
        min={0}
        max={ANY}
        step={1}
        value={pos}
        aria-label="Maximum years of experience a role may ask for"
        onChange={(e) => setPos(Number(e.target.value))}
        onPointerUp={commit}
        onKeyUp={commit}
        onTouchEnd={commit}
        className="w-full accent-[var(--accent)]"
      />

      <div className="flex justify-between text-[11px] text-muted opacity-70">
        <span>0</span>
        <span>Any</span>
      </div>

      {!compact && (
        <>
          {suggested != null && pos !== suggested && (
            <p className="text-[11px] text-muted">
              your profile suggests {suggested} ·{" "}
              <button
                type="button"
                onClick={() => onCommit(String(suggested))}
                className="text-accent hover:underline"
              >
                apply
              </button>
            </p>
          )}
          {noneCount > 0 && (
            <p className="text-[11px] text-muted opacity-70">
              {noneCount.toLocaleString()} state no requirement and are always shown
            </p>
          )}
        </>
      )}
    </div>
  );
}
