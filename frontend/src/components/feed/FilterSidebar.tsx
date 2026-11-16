"use client";

import { useId, useState } from "react";

import type { CategoryMeta, FilterSpec } from "@/lib/api";
import { useFeedParams } from "@/lib/hooks";

const COLLAPSED_OPTIONS = 6;
const SEARCHABLE_FROM = 12;

const SINCE_OPTIONS = [
  { value: "", label: "Any time" },
  { value: "24h", label: "Past 24 hours" },
  { value: "7d", label: "Past week" },
  { value: "30d", label: "Past month" },
];

const SALARY_OPTIONS = [
  { value: "", label: "Any" },
  { value: "1000", label: "$1k+" },
  { value: "2000", label: "$2k+" },
  { value: "3000", label: "$3k+" },
  { value: "4000", label: "$4k+" },
];

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  const id = useId();
  return (
    <section aria-labelledby={id} className="border-t border-line py-4 first:border-t-0 first:pt-0">
      <h3 id={id} className="mb-2 text-sm font-semibold">
        {title}
      </h3>
      {children}
    </section>
  );
}

function Segmented({
  name,
  options,
  value,
  onChange,
}: {
  name: string;
  options: { value: string; label: string }[];
  value: string;
  onChange: (value: string) => void;
}) {
  return (
    <div role="radiogroup" aria-label={name} className="flex flex-wrap gap-1">
      {options.map((o) => (
        <button
          key={o.value}
          type="button"
          role="radio"
          aria-checked={value === o.value}
          onClick={() => onChange(o.value)}
          className={`rounded-md border px-2 py-1 text-sm tabular ${
            value === o.value
              ? "border-jade bg-jade text-white dark:text-paper"
              : "border-line text-muted hover:border-faint hover:text-ink"
          }`}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}

function RadioList({
  name,
  options,
  value,
  onChange,
}: {
  name: string;
  options: { value: string; label: string; count?: number | null }[];
  value: string;
  onChange: (value: string) => void;
}) {
  return (
    <div className="space-y-0.5">
      {options.map((o) => (
        <label key={o.value} className="flex cursor-pointer items-center gap-2.5 py-1 text-sm">
          <input
            type="radio"
            name={name}
            checked={value === o.value}
            onChange={() => onChange(o.value)}
            className="size-4 accent-[var(--jade)]"
          />
          <span className="flex-1">{o.label}</span>
          {o.count != null && <span className="text-xs text-faint tabular">{o.count}</span>}
        </label>
      ))}
    </div>
  );
}

function MultiSelect({ spec, selected }: { spec: FilterSpec; selected: string[] }) {
  const { toggle } = useFeedParams();
  const [expanded, setExpanded] = useState(false);
  const [query, setQuery] = useState("");

  const needle = query.trim().toLowerCase();
  const matching = needle
    ? spec.options.filter((o) => o.label.toLowerCase().includes(needle) || o.value.includes(needle))
    : spec.options;
  // Selected options stay visible even when the list is collapsed.
  const visible = expanded || needle
    ? matching
    : [
        ...matching.slice(0, COLLAPSED_OPTIONS),
        ...matching.slice(COLLAPSED_OPTIONS).filter((o) => selected.includes(o.value)),
      ];

  return (
    <div>
      {spec.options.length >= SEARCHABLE_FROM && (
        <input
          type="search"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder={`Find a ${spec.label.toLowerCase()}`}
          aria-label={`Find a ${spec.label.toLowerCase()}`}
          className="mb-2 w-full rounded-md border border-line bg-surface px-2.5 py-1.5 text-sm placeholder:text-faint"
        />
      )}
      <div className="space-y-0.5">
        {visible.map((o) => {
          const checked = selected.includes(o.value);
          return (
            <label
              key={o.value}
              className={`flex cursor-pointer items-center gap-2.5 py-1 text-sm ${
                o.count === 0 && !checked ? "text-faint" : ""
              }`}
            >
              <input
                type="checkbox"
                checked={checked}
                onChange={() => toggle(spec.key, o.value)}
                className="size-4 rounded accent-[var(--jade)]"
              />
              <span className="min-w-0 flex-1 truncate" title={o.label}>
                {o.label}
              </span>
              {o.count != null && <span className="text-xs text-faint tabular">{o.count}</span>}
            </label>
          );
        })}
        {needle && matching.length === 0 && <p className="py-1 text-sm text-faint">No match for “{query}”.</p>}
      </div>
      {!needle && matching.length > COLLAPSED_OPTIONS && (
        <button
          type="button"
          onClick={() => setExpanded((v) => !v)}
          className="mt-1 text-sm font-medium text-jade hover:underline"
        >
          {expanded ? "Show fewer" : `Show all ${matching.length}`}
        </button>
      )}
    </div>
  );
}

export function FilterSidebar({ category }: { category: CategoryMeta | undefined }) {
  const { params, set } = useFeedParams();

  return (
    <div>
      <Section title="Show">
        <Segmented
          name="Show"
          options={[
            { value: "", label: "All posts" },
            { value: "true", label: "Saved" },
          ]}
          value={params.get("saved") ?? ""}
          onChange={(v) => set("saved", v)}
        />
      </Section>

      <Section title="Posted">
        <RadioList name="since" options={SINCE_OPTIONS} value={params.get("since") ?? ""} onChange={(v) => set("since", v)} />
      </Section>

      <Section title={`Minimum ${(category?.price_label ?? "salary").toLowerCase()}`}>
        <Segmented
          name="Minimum salary"
          options={SALARY_OPTIONS}
          value={params.get("salary_min_usd") ?? ""}
          onChange={(v) => set("salary_min_usd", v)}
        />
        <p className="mt-2 text-xs text-faint">Per month, VND converted to USD. Hides negotiable posts.</p>
      </Section>

      {category?.filters.map((spec) => (
        <Section key={spec.key} title={spec.label}>
          {spec.type === "select" ? (
            <RadioList
              name={spec.key}
              options={[{ value: "", label: "Any" }, ...spec.options]}
              value={params.get(spec.key) ?? ""}
              onChange={(v) => set(spec.key, v)}
            />
          ) : spec.type === "boolean" ? (
            <label className="flex items-center gap-2.5 text-sm">
              <input
                type="checkbox"
                checked={params.get(spec.key) === "true"}
                onChange={(e) => set(spec.key, e.target.checked ? "true" : null)}
                className="size-4 accent-[var(--jade)]"
              />
              Only {spec.label.toLowerCase()}
            </label>
          ) : (
            <MultiSelect spec={spec} selected={params.getAll(spec.key)} />
          )}
        </Section>
      ))}
    </div>
  );
}
