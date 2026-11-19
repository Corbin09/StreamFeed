// Typed client for the StreamFeed REST API (backend/app/api, ARCHITECTURE.md §8).

// Same-origin by default: Next.js proxies /api/* to the backend (next.config.ts).
// Set NEXT_PUBLIC_API_URL only to call an API on another origin directly.
export const API_URL = (process.env.NEXT_PUBLIC_API_URL || "/api").replace(/\/$/, "");

export type SourceRef = { id: number; type: string; name: string };

export type JobAttributes = {
  levels?: string[];
  skills?: string[];
  locations?: string[];
  work_mode?: string;
  salary_negotiable?: boolean;
  salary_text?: string;
  employment_type?: string;
  expertise?: string;
  experience_months?: number;
  valid_through?: string;
  /** Comments shown under the post (Facebook groups), at most 10. */
  comments?: { author: string | null; text: string; posted_at: string | null }[];
  [key: string]: unknown;
};

export type PostItem = {
  id: number;
  title: string;
  company: string | null;
  url: string;
  source: SourceRef;
  category: string;
  language: string | null;
  location: string | null;
  price_min: number | null;
  price_max: number | null;
  currency: string | null;
  attributes: JobAttributes;
  posted_at: string | null;
  crawled_at: string;
  is_saved: boolean;
  is_hidden: boolean;
  duplicate_of_id: number | null;
  also_on: string[];
  snippet: string;
};

export type PostPage = { items: PostItem[]; next_cursor: string | null };

export type PostDetail = PostItem & {
  body: string;
  author: string | null;
  last_seen_at: string;
  updated_at: string;
  duplicates: { id: number; title: string; url: string; source: SourceRef; posted_at: string | null }[];
};

export type RunStatus = "queued" | "running" | "success" | "failed" | "needs_login";

export type Run = {
  id: number;
  source: SourceRef;
  status: RunStatus;
  trigger: "schedule" | "manual";
  raw_dir: string | null;
  started_at: string;
  finished_at: string | null;
  duration_seconds: number | null;
  fetched: number;
  inserted: number;
  updated: number;
  error: string | null;
};

export type SourceStatus = "idle" | "ok" | "needs_login" | "error";

export type Source = {
  id: number;
  type: string;
  name: string;
  category: string | null;
  config: Record<string, unknown>;
  enabled: boolean;
  interval_minutes: number;
  min_interval_minutes: number | null;
  status: SourceStatus;
  consecutive_failures: number;
  last_run_at: string | null;
  last_error: string | null;
  plugin_available: boolean;
  post_count: number;
  last_run: Run | null;
  /** "07:00-09:00" when the source runs once a day in that local window. */
  daily_window: string | null;
  next_run_at: string | null;
};

export type FilterOption = { value: string; label: string; count: number | null };
export type FilterSpec = {
  key: string;
  label: string;
  type: "multi_select" | "select" | "boolean";
  options: FilterOption[];
};
export type CategoryMeta = {
  key: string;
  label: string;
  price_label: string;
  post_count: number;
  filters: FilterSpec[];
};
export type CategoriesMeta = { categories: CategoryMeta[]; usd_vnd_rate: number };

export type WorkerHealth = {
  status: "ok" | "stale" | "missing";
  last_beat_at: string | null;
  age_seconds: number | null;
};

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

async function request<T>(path: string, init?: RequestInit & { allowStatus?: number[] }): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${API_URL}${path}`, {
      ...init,
      headers: { "Content-Type": "application/json", ...init?.headers },
    });
  } catch {
    throw new ApiError(0, `Can't reach the server. Check your connection, or start the stack with "docker compose up -d".`);
  }
  const body = await res.json().catch(() => null);
  if (res.status === 502 || res.status === 504 || (res.status === 500 && body === null)) {
    throw new ApiError(res.status, "The API isn't responding. Check that the backend container is running.");
  }
  if (!res.ok && !init?.allowStatus?.includes(res.status)) {
    const detail = body?.detail;
    const message = typeof detail === "string" ? detail : `Request failed (${res.status})`;
    throw new ApiError(res.status, message);
  }
  return body as T;
}

export const api = {
  posts: (params: URLSearchParams) => request<PostPage>(`/posts?${params}`),
  post: (id: number) => request<PostDetail>(`/posts/${id}`),
  updatePost: (id: number, patch: { is_saved?: boolean; is_hidden?: boolean }) =>
    request<PostDetail>(`/posts/${id}`, { method: "PATCH", body: JSON.stringify(patch) }),
  categories: () => request<CategoriesMeta>("/meta/categories"),
  sources: () => request<Source[]>("/sources"),
  updateSource: (
    id: number,
    patch: { enabled?: boolean; interval_minutes?: number; config?: Record<string, unknown> },
  ) =>
    request<Source>(`/sources/${id}`, { method: "PATCH", body: JSON.stringify(patch) }),
  runSource: (id: number) => request<{ run_id: number }>(`/sources/${id}/run`, { method: "POST" }),
  runs: (limit = 20) => request<Run[]>(`/runs?limit=${limit}`),
  run: (id: number) => request<Run>(`/runs/${id}`),
  workerHealth: () => request<WorkerHealth>("/health/worker", { allowStatus: [503] }),
};
