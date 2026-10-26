"use client";

import { useState } from "react";
import { api } from "../../lib/api";
import { REASON_TAGS, VERDICT_LABEL, type Verdict } from "../../lib/types";
import { Chip } from "../../components/Chips";

const VERDICTS: Verdict[] = ["apply", "maybe", "no"];
const STYLE: Record<Verdict, string> = {
  apply: "bg-emerald-600 hover:bg-emerald-500",
  maybe: "bg-amber-600 hover:bg-amber-500",
  no: "bg-rose-600 hover:bg-rose-500",
};

export function LabelControl({
  jobId,
  initialVerdict,
  initialTags,
  initialReason,
}: {
  jobId: number;
  initialVerdict: Verdict | null;
  initialTags: string[];
  initialReason: string;
}) {
  const [verdict, setVerdict] = useState<Verdict | null>(initialVerdict);
  const [tags, setTags] = useState<string[]>(initialTags ?? []);
  const [reason, setReason] = useState(initialReason);
  const [status, setStatus] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const save = async (v: Verdict) => {
    setBusy(true);
    const previous = verdict;
    setVerdict(v);
    try {
      const res = await api.label({
        job_id: jobId,
        verdict: v,
        reason: reason.trim(),
        reason_tags: tags,
      });
      setStatus(
        res.postings_labeled > 1
          ? `Saved — also applied to ${res.postings_labeled - 1} duplicate posting(s)`
          : "Saved",
      );
    } catch (e) {
      setVerdict(previous);
      setStatus(`Failed: ${e}`);
    } finally {
      setBusy(false);
    }
  };

  const clear = async () => {
    setBusy(true);
    try {
      await api.unlabel(jobId);
      setVerdict(null);
      setTags([]);
      setReason("");
      setStatus("Cleared");
    } catch (e) {
      setStatus(`Failed: ${e}`);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-3 rounded-lg border border-border bg-surface p-4">
      <h2 className="text-sm font-semibold">Your verdict</h2>

      <div className="space-y-1.5">
        {VERDICTS.map((v) => (
          <button
            key={v}
            disabled={busy}
            onClick={() => void save(v)}
            className={`w-full rounded px-3 py-2 text-sm font-medium text-white disabled:opacity-50 ${
              verdict === v ? STYLE[v] : "bg-slate-500/60 hover:bg-slate-500"
            }`}
          >
            {VERDICT_LABEL[v]}
          </button>
        ))}
      </div>

      <div className="flex flex-wrap gap-1.5">
        {REASON_TAGS.map((t) => (
          <Chip key={t} active={tags.includes(t)} onClick={() =>
            setTags((p) => (p.includes(t) ? p.filter((x) => x !== t) : [...p, t]))
          }>
            {t}
          </Chip>
        ))}
      </div>

      <input
        value={reason}
        onChange={(e) => setReason(e.target.value)}
        placeholder="Optional note"
        className="w-full rounded border border-border bg-bg px-3 py-2 text-sm outline-none focus:border-accent"
      />

      <div className="flex items-center justify-between text-xs">
        <span className="text-muted">{status}</span>
        {verdict && (
          <button onClick={() => void clear()} disabled={busy} className="text-muted hover:text-fg">
            Clear
          </button>
        )}
      </div>
    </div>
  );
}
