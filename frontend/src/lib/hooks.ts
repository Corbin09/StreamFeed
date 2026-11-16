"use client";

import { type InfiniteData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

import { api, type PostDetail, type PostPage } from "./api";

/** Parameters that belong to the page, not to the category's filter set. */
export const FEED_BASE_PARAMS = ["category", "q", "sort"];

/**
 * Feed state lives in the URL (ARCHITECTURE.md §9): the URL's search params are passed to
 * GET /posts unchanged, so a feed can be bookmarked and the back button works.
 */
export function useFeedParams() {
  const searchParams = useSearchParams();
  const router = useRouter();
  const pathname = usePathname();

  const update = useCallback(
    (mutate: (p: URLSearchParams) => void) => {
      const next = new URLSearchParams(searchParams.toString());
      mutate(next);
      next.delete("cursor");
      const qs = next.toString();
      router.replace(qs ? `${pathname}?${qs}` : pathname, { scroll: false });
    },
    [searchParams, router, pathname],
  );

  const set = useCallback(
    (key: string, value: string | null) =>
      update((p) => {
        if (value === null || value === "") p.delete(key);
        else p.set(key, value);
      }),
    [update],
  );

  const toggle = useCallback(
    (key: string, value: string) =>
      update((p) => {
        const values = p.getAll(key);
        p.delete(key);
        const next = values.includes(value) ? values.filter((v) => v !== value) : [...values, value];
        next.forEach((v) => p.append(key, v));
      }),
    [update],
  );

  const clearFilters = useCallback(
    () =>
      update((p) => {
        for (const key of [...p.keys()]) if (!FEED_BASE_PARAMS.includes(key)) p.delete(key);
      }),
    [update],
  );

  return { params: searchParams, category: searchParams.get("category") ?? "job", set, toggle, update, clearFilters };
}

export function useCategories() {
  return useQuery({ queryKey: ["categories"], queryFn: api.categories, staleTime: 60_000 });
}

/** Save or hide a post and patch every cached feed page and detail in place. */
export function usePostUpdate() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, patch }: { id: number; patch: { is_saved?: boolean; is_hidden?: boolean } }) =>
      api.updatePost(id, patch),
    onMutate: async ({ id, patch }) => {
      await qc.cancelQueries({ queryKey: ["posts"] });
      qc.setQueriesData<InfiniteData<PostPage>>({ queryKey: ["posts"] }, (data) =>
        data
          ? {
              ...data,
              pages: data.pages.map((page) => ({
                ...page,
                items: page.items
                  .map((item) => (item.id === id ? { ...item, ...patch } : item))
                  .filter((item) => !(item.id === id && patch.is_hidden)),
              })),
            }
          : data,
      );
      qc.setQueryData<PostDetail>(["post", id], (post) => (post ? { ...post, ...patch } : post));
    },
    onSettled: (_data, _error, { id }) => {
      qc.invalidateQueries({ queryKey: ["post", id] });
      qc.invalidateQueries({ queryKey: ["categories"] });
    },
    onError: () => qc.invalidateQueries({ queryKey: ["posts"] }),
  });
}

let previousVisit: number | null | undefined;

/**
 * Time of the previous visit, read once per page load (per-viewer convenience in
 * localStorage). Posts crawled after it are marked as new.
 */
export function usePreviousVisit(): number | null {
  const [value, setValue] = useState<number | null>(previousVisit ?? null);
  useEffect(() => {
    if (previousVisit === undefined) {
      try {
        const stored = window.localStorage.getItem("streamfeed:last-visit");
        previousVisit = stored ? Number(stored) : null;
        window.localStorage.setItem("streamfeed:last-visit", String(Date.now()));
      } catch {
        previousVisit = null;
      }
    }
    setValue(previousVisit);
  }, []);
  return value;
}

export function useDebounced<T>(value: T, ms: number): T {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setDebounced(value), ms);
    return () => clearTimeout(t);
  }, [value, ms]);
  return debounced;
}
