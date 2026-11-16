"use client";

import { useInfiniteQuery } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";

import { SearchIcon, SlidersIcon, XIcon } from "@/components/icons";
import { PayScale } from "@/components/PayRail";
import { api, type CategoryMeta, type PostItem } from "@/lib/api";
import { FEED_BASE_PARAMS, useCategories, useDebounced, useFeedParams, usePostUpdate, usePreviousVisit } from "@/lib/hooks";

import { FilterSidebar } from "./FilterSidebar";
import { PostRow } from "./PostRow";

const PAGE_SIZE = "20";

const FIXED_CHIP_LABELS: Record<string, (v: string) => string> = {
  saved: () => "Saved",
  since: (v) => ({ "24h": "Past 24 hours", "7d": "Past week", "30d": "Past month" })[v] ?? v,
  salary_min_usd: (v) => `$${Number(v) / 1000}k+`,
};

function SearchBox({ placeholder }: { placeholder: string }) {
  const { params, set } = useFeedParams();
  const urlValue = params.get("q") ?? "";
  const [value, setValue] = useState(urlValue);
  const debounced = useDebounced(value, 300);

  // Keep the box in sync when the URL changes elsewhere (back button, clear).
  const lastPushed = useRef(urlValue);
  useEffect(() => {
    if (urlValue !== lastPushed.current) {
      lastPushed.current = urlValue;
      setValue(urlValue);
    }
  }, [urlValue]);

  useEffect(() => {
    const next = debounced.trim();
    if (next !== (lastPushed.current ?? "").trim()) {
      lastPushed.current = next;
      set("q", next || null);
    }
  }, [debounced, set]);

  return (
    <div className="relative">
      <SearchIcon className="pointer-events-none absolute left-4 top-1/2 size-6 -translate-y-1/2 text-faint" />
      <input
        type="search"
        value={value}
        onChange={(e) => setValue(e.target.value)}
        placeholder={placeholder}
        aria-label="Search posts"
        className="h-14 w-full rounded-xl border border-line bg-surface pl-13 pr-4 text-[17px] font-medium tracking-tight placeholder:font-normal placeholder:text-faint focus:border-jade focus:outline-none sm:h-16 sm:text-[22px] placeholder:text-[15px] sm:placeholder:text-[22px]"
      />
    </div>
  );
}

function CategoryTabs({ categories, active }: { categories: CategoryMeta[]; active: string }) {
  const { update } = useFeedParams();
  if (categories.length < 2) return null;
  return (
    <nav aria-label="Categories" className="mb-4 flex gap-5 border-b border-line">
      {categories.map((c) => (
        <button
          key={c.key}
          type="button"
          aria-current={c.key === active ? "page" : undefined}
          onClick={() =>
            update((p) => {
              const q = p.get("q");
              for (const key of [...p.keys()]) p.delete(key);
              if (c.key !== "job") p.set("category", c.key);
              if (q) p.set("q", q);
            })
          }
          className={`-mb-px border-b-2 pb-2 text-sm font-medium ${
            c.key === active ? "border-jade text-ink" : "border-transparent text-muted hover:text-ink"
          }`}
        >
          {c.label} <span className="text-faint tabular">{c.post_count}</span>
        </button>
      ))}
    </nav>
  );
}

function ActiveFilters({ category }: { category: CategoryMeta | undefined }) {
  const { params, update, clearFilters } = useFeedParams();
  const chips: { key: string; value: string; label: string }[] = [];
  for (const [key, value] of params.entries()) {
    if (FEED_BASE_PARAMS.includes(key)) continue;
    const spec = category?.filters.find((f) => f.key === key);
    const label =
      FIXED_CHIP_LABELS[key]?.(value) ?? spec?.options.find((o) => o.value === value)?.label ?? value;
    chips.push({ key, value, label });
  }
  if (chips.length === 0) return null;

  const remove = (key: string, value: string) =>
    update((p) => {
      const rest = p.getAll(key).filter((v) => v !== value);
      p.delete(key);
      rest.forEach((v) => p.append(key, v));
    });

  return (
    <div className="flex flex-wrap items-center gap-1.5">
      {chips.map((c) => (
        <button
          key={`${c.key}=${c.value}`}
          type="button"
          onClick={() => remove(c.key, c.value)}
          className="flex items-center gap-1 rounded-full border border-line bg-surface py-0.5 pl-2.5 pr-1.5 text-sm hover:border-faint"
          aria-label={`Remove filter ${c.label}`}
        >
          {c.label}
          <XIcon className="size-3.5 text-faint" />
        </button>
      ))}
      <button type="button" onClick={clearFilters} className="ml-1 text-sm font-medium text-jade hover:underline">
        Clear filters
      </button>
    </div>
  );
}

function SortControl() {
  const { params, set } = useFeedParams();
  const hasQuery = Boolean(params.get("q"));
  const current = params.get("sort") ?? (hasQuery ? "relevance" : "newest");
  const options = [
    { value: "newest", label: "Newest" },
    { value: "relevance", label: "Best match", disabled: !hasQuery },
  ];
  return (
    <div className="flex items-center gap-1 text-sm" role="radiogroup" aria-label="Sort">
      <span className="mr-1 text-muted">Sort</span>
      {options.map((o) => (
        <button
          key={o.value}
          type="button"
          role="radio"
          aria-checked={current === o.value}
          disabled={o.disabled}
          title={o.disabled ? "Search for something to sort by match" : undefined}
          onClick={() => set("sort", o.value === (hasQuery ? "relevance" : "newest") ? null : o.value)}
          className={`rounded-md px-2 py-1 disabled:cursor-not-allowed disabled:text-faint ${
            current === o.value ? "bg-surface font-medium text-ink shadow-[inset_0_0_0_1px_var(--line)]" : "text-muted hover:text-ink"
          }`}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}

function Toast({ message, action, onAction }: { message: string; action: string; onAction: () => void }) {
  return (
    <div
      role="status"
      className="fixed bottom-5 left-1/2 z-20 flex -translate-x-1/2 items-center gap-4 rounded-lg bg-ink px-4 py-2.5 text-sm text-paper shadow-lg"
    >
      {message}
      <button type="button" onClick={onAction} className="font-semibold text-jade-soft underline-offset-2 hover:underline">
        {action}
      </button>
    </div>
  );
}

export function FeedView() {
  const { params, category, clearFilters } = useFeedParams();
  const { data: meta, error: metaError } = useCategories();
  const activeCategory = meta?.categories.find((c) => c.key === category);
  const rate = meta?.usd_vnd_rate ?? 26_000;
  const previousVisit = usePreviousVisit();
  const updatePost = usePostUpdate();
  const [filtersOpen, setFiltersOpen] = useState(false);
  const [hidden, setHidden] = useState<PostItem | null>(null);

  const queryString = params.toString();
  const feed = useInfiniteQuery({
    queryKey: ["posts", queryString],
    queryFn: ({ pageParam }) => {
      const p = new URLSearchParams(queryString);
      p.set("limit", PAGE_SIZE);
      if (pageParam) p.set("cursor", pageParam);
      return api.posts(p);
    },
    initialPageParam: null as string | null,
    getNextPageParam: (last) => last.next_cursor,
  });

  const sentinel = useRef<HTMLDivElement>(null);
  const { hasNextPage, isFetchingNextPage, fetchNextPage } = feed;
  useEffect(() => {
    const el = sentinel.current;
    if (!el || !hasNextPage) return;
    const observer = new IntersectionObserver(
      ([entry]) => {
        if (entry.isIntersecting && !isFetchingNextPage) fetchNextPage();
      },
      { rootMargin: "600px" },
    );
    observer.observe(el);
    return () => observer.disconnect();
  }, [hasNextPage, isFetchingNextPage, fetchNextPage]);

  useEffect(() => {
    if (!hidden) return;
    const t = setTimeout(() => setHidden(null), 6000);
    return () => clearTimeout(t);
  }, [hidden]);

  const posts = feed.data?.pages.flatMap((p) => p.items) ?? [];
  const noun = (activeCategory?.label ?? "posts").toLowerCase();
  const hasFilters = [...params.keys()].some((k) => !FEED_BASE_PARAMS.includes(k));
  const error = feed.error ?? metaError;

  return (
    <div className="mx-auto max-w-[1280px] px-4 pb-24 pt-6 sm:px-6 lg:grid lg:grid-cols-[15.5rem_minmax(0,1fr)] lg:gap-10">
      <aside className={`${filtersOpen ? "block" : "hidden"} mb-6 lg:mb-0 lg:block`} aria-label="Filters">
        <div className="lg:sticky lg:top-6 lg:max-h-[calc(100dvh-3rem)] lg:overflow-y-auto lg:pr-2">
          <FilterSidebar category={activeCategory} />
        </div>
      </aside>

      <main className="min-w-0">
        {meta && <CategoryTabs categories={meta.categories} active={category} />}
        <SearchBox placeholder={`Search ${activeCategory?.post_count ?? ""} ${noun} in Vietnamese or English`.replace("  ", " ")} />

        <div className="mt-4 flex flex-wrap items-center justify-between gap-3">
          <button
            type="button"
            onClick={() => setFiltersOpen((v) => !v)}
            aria-expanded={filtersOpen}
            className="flex items-center gap-2 rounded-md border border-line bg-surface px-3 py-1.5 text-sm font-medium lg:hidden"
          >
            <SlidersIcon />
            {filtersOpen ? "Hide filters" : "Filters"}
          </button>
          <div className="min-w-0 flex-1">
            <ActiveFilters category={activeCategory} />
          </div>
          <SortControl />
        </div>

        <div className="mt-5">
          <div className="hidden gap-x-8 px-5 pb-2 md:grid md:grid-cols-[minmax(0,1fr)_15rem]">
            <span className="text-[13px] text-faint">
              {feed.isSuccess && posts.length > 0 && (feed.hasNextPage ? `${posts.length} loaded, scroll for more` : `${posts.length} ${noun}`)}
            </span>
            <PayScale />
          </div>

          {error ? (
            <div className="rounded-xl border border-vermilion bg-vermilion-soft p-5 text-sm">
              <p className="font-semibold text-vermilion">The feed didn’t load.</p>
              <p className="mt-1">{error.message}</p>
              <button type="button" onClick={() => feed.refetch()} className="mt-3 font-medium text-jade hover:underline">
                Try again
              </button>
            </div>
          ) : feed.isPending ? (
            <ul aria-busy className="border-t border-line">
              {Array.from({ length: 6 }, (_, i) => (
                <li key={i} className="grid gap-8 border-b border-line px-5 py-5 md:grid-cols-[minmax(0,1fr)_15rem]">
                  <div className="space-y-2.5">
                    <div className="h-4 w-3/5 animate-pulse rounded bg-track" />
                    <div className="h-3 w-2/5 animate-pulse rounded bg-track" />
                  </div>
                  <div className="h-1.5 animate-pulse self-center rounded bg-track" />
                </li>
              ))}
            </ul>
          ) : posts.length === 0 ? (
            <div className="border-t border-line px-5 py-14 text-center">
              <p className="text-lg font-semibold">
                {params.get("saved") ? `No saved ${noun} yet` : `No ${noun} match this search`}
              </p>
              <p className="mt-1 text-sm text-muted">
                {params.get("saved")
                  ? "Use Save on a post to keep it here. Saved posts never expire."
                  : "Try fewer words, or remove a filter."}
              </p>
              {hasFilters && (
                <button type="button" onClick={clearFilters} className="mt-4 text-sm font-medium text-jade hover:underline">
                  Clear filters
                </button>
              )}
            </div>
          ) : (
            <ul className="border-t border-line">
              {posts.map((post) => (
                <PostRow
                  key={post.id}
                  post={post}
                  rate={rate}
                  isNew={previousVisit != null && new Date(post.crawled_at).getTime() > previousVisit}
                  onSave={(p) => updatePost.mutate({ id: p.id, patch: { is_saved: !p.is_saved } })}
                  onHide={(p) => {
                    updatePost.mutate({ id: p.id, patch: { is_hidden: true } });
                    setHidden(p);
                  }}
                />
              ))}
            </ul>
          )}

          <div ref={sentinel} />
          {isFetchingNextPage && <p className="py-6 text-center text-sm text-muted">Loading more…</p>}
          {feed.isSuccess && !hasNextPage && posts.length > 0 && (
            <p className="py-8 text-center text-sm text-faint">You’ve reached the end.</p>
          )}
        </div>
      </main>

      {hidden && (
        <Toast
          message="Post hidden."
          action="Undo"
          onAction={() => {
            updatePost.mutate(
              { id: hidden.id, patch: { is_hidden: false } },
              { onSuccess: () => feed.refetch() },
            );
            setHidden(null);
          }}
        />
      )}
    </div>
  );
}
