"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";

import { ArrowLeftIcon, ExternalIcon, EyeOffIcon, StarIcon } from "@/components/icons";
import { PayRail, PayScale } from "@/components/PayRail";
import { api, type PostDetail } from "@/lib/api";
import {
  WORK_MODE_LABELS,
  formatPrice,
  fullDate,
  humanize,
  locationLabel,
  sourceLabel,
  timeAgo,
} from "@/lib/format";
import { useCategories, usePostUpdate } from "@/lib/hooks";

const LANGUAGE_LABELS: Record<string, string> = { vi: "Vietnamese", en: "English" };

function experience(months: number | undefined): string | null {
  if (months == null) return null;
  if (months === 0) return "No experience required";
  if (months < 12) return `${months} months`;
  const years = Math.round(months / 12);
  return `${years}+ year${years === 1 ? "" : "s"}`;
}

function Fact({ term, children }: { term: string; children: React.ReactNode }) {
  if (children == null || children === "" || (Array.isArray(children) && children.length === 0)) return null;
  return (
    <div className="grid grid-cols-[7.5rem_minmax(0,1fr)] gap-3 py-2 text-sm">
      <dt className="text-muted">{term}</dt>
      <dd>{children}</dd>
    </div>
  );
}

function Detail({ post, rate }: { post: PostDetail; rate: number }) {
  const router = useRouter();
  const update = usePostUpdate();
  const a = post.attributes;
  const price = formatPrice(post.price_min, post.price_max, post.currency);
  const locations = a.locations?.length ? a.locations : post.location ? [post.location] : [];

  return (
    <article className="lg:grid lg:grid-cols-[minmax(0,1fr)_20rem] lg:gap-12">
      <div className="min-w-0">
        <header>
          <h1 className="text-[28px] font-bold leading-tight tracking-tight sm:text-[34px]" lang={post.language ?? undefined}>
            {post.title}
          </h1>
          <p className="mt-2 text-lg">
            {post.company && <span className="font-semibold">{post.company}</span>}
            {post.author && !post.company && <span className="font-semibold">{post.author}</span>}
          </p>
          <div className="mt-5 flex flex-wrap gap-2">
            <a
              href={post.url}
              target="_blank"
              rel="noreferrer"
              className="flex items-center gap-2 rounded-lg bg-jade px-4 py-2 text-sm font-semibold text-white hover:bg-jade-strong dark:text-paper"
            >
              Open on {sourceLabel(post.source.type)}
              <ExternalIcon />
            </a>
            <button
              type="button"
              aria-pressed={post.is_saved}
              onClick={() => update.mutate({ id: post.id, patch: { is_saved: !post.is_saved } })}
              className={`flex items-center gap-2 rounded-lg border px-4 py-2 text-sm font-medium ${
                post.is_saved ? "border-saffron text-saffron" : "border-line bg-surface hover:border-faint"
              }`}
            >
              <StarIcon filled={post.is_saved} />
              {post.is_saved ? "Saved" : "Save"}
            </button>
            <button
              type="button"
              onClick={() => {
                const hide = !post.is_hidden;
                update.mutate({ id: post.id, patch: { is_hidden: hide } });
                if (hide) router.push("/");
              }}
              className="flex items-center gap-2 rounded-lg border border-line bg-surface px-4 py-2 text-sm font-medium hover:border-faint"
            >
              <EyeOffIcon />
              {post.is_hidden ? "Unhide" : "Hide"}
            </button>
          </div>
        </header>

        <div
          className="mt-8 max-w-[68ch] space-y-2.5 border-t border-line pt-6 text-[15.5px] leading-[1.7]"
          lang={post.language ?? undefined}
        >
          {post.body ? (
            post.body
              .split(/\n+/)
              .map((line) => line.trim())
              .filter(Boolean)
              .map((line, i) => <p key={i}>{line}</p>)
          ) : (
            <p className="text-muted">This post has no description. Open it on the source site.</p>
          )}
        </div>

        {a.comments && a.comments.length > 0 && (
          <section className="mt-10 max-w-[68ch] border-t border-line pt-6">
            <h2 className="text-lg font-semibold">
              Comments <span className="font-normal text-faint tabular">{a.comments.length}</span>
            </h2>
            <p className="mt-1 text-sm text-muted">The comments that were visible under the post when it was read.</p>
            <ul className="mt-4 space-y-4">
              {a.comments.map((c, i) => (
                <li key={i} className="text-[15px]" lang={post.language ?? undefined}>
                  <p className="text-sm">
                    <span className="font-semibold">{c.author ?? "Unknown"}</span>
                    {c.posted_at && (
                      <span className="ml-2 text-faint" title={fullDate(c.posted_at)}>
                        {timeAgo(c.posted_at)}
                      </span>
                    )}
                  </p>
                  <p className="mt-0.5 whitespace-pre-line leading-relaxed">{c.text}</p>
                </li>
              ))}
            </ul>
          </section>
        )}
      </div>

      <aside className="mt-10 lg:mt-0">
        <div className="space-y-6 lg:sticky lg:top-6">
          <section className="rounded-xl border border-line bg-surface p-5">
            <h2 className="text-sm text-muted">Salary</h2>
            <p className={`mt-1 text-2xl font-bold tabular ${price ? "" : "text-faint"}`}>
              {price ?? (a.salary_negotiable ? "Negotiable" : "Not listed")}
            </p>
            {a.salary_text && a.salary_text !== price && <p className="mt-0.5 text-sm text-muted">Posted as “{a.salary_text}”</p>}
            <PayRail className="mt-4" min={post.price_min} max={post.price_max} currency={post.currency} rate={rate} />
            <PayScale className="mt-1" />
          </section>

          <dl className="divide-y divide-line">
            <Fact term="Level">{a.levels?.map(humanize).join(", ")}</Fact>
            <Fact term="Location">{locations.map(locationLabel).join(", ")}</Fact>
            <Fact term="Work mode">{a.work_mode ? (WORK_MODE_LABELS[a.work_mode] ?? humanize(a.work_mode)) : null}</Fact>
            <Fact term="Experience">{experience(a.experience_months)}</Fact>
            <Fact term="Role">{a.expertise}</Fact>
            <Fact term="Type">{a.employment_type ? humanize(a.employment_type) : null}</Fact>
            <Fact term="Skills">
              {a.skills?.length ? (
                <span className="flex flex-wrap gap-1.5">
                  {a.skills.map((s) => (
                    <Link
                      key={s}
                      href={`/?skills=${encodeURIComponent(s)}`}
                      className="rounded border border-line px-1.5 py-[1px] text-[13px] text-muted hover:border-jade hover:text-jade"
                    >
                      {s}
                    </Link>
                  ))}
                </span>
              ) : null}
            </Fact>
            <Fact term="Language">{post.language ? LANGUAGE_LABELS[post.language] ?? post.language : null}</Fact>
            <Fact term="Posted">
              <span title={fullDate(post.posted_at)}>{post.posted_at ? timeAgo(post.posted_at) : "Unknown"}</span>
            </Fact>
            <Fact term="Expires">{a.valid_through ? fullDate(a.valid_through).replace(/,.*$/, "") : null}</Fact>
            <Fact term="Source">{post.source.name}</Fact>
            <Fact term="Last seen">
              <span title={fullDate(post.last_seen_at)}>{timeAgo(post.last_seen_at)}</span>
            </Fact>
          </dl>

          {post.duplicates.length > 0 && (
            <section>
              <h2 className="text-sm font-semibold">Also posted on</h2>
              <ul className="mt-2 space-y-2">
                {post.duplicates.map((d) => (
                  <li key={d.id} className="text-sm">
                    <Link href={`/posts/${d.id}`} className="font-medium hover:underline">
                      {sourceLabel(d.source.type)}
                    </Link>{" "}
                    <span className="text-muted">{timeAgo(d.posted_at)}</span>
                  </li>
                ))}
              </ul>
            </section>
          )}
        </div>
      </aside>
    </article>
  );
}

export default function PostPage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const postId = Number(id);
  const { data: meta } = useCategories();
  const { data: post, error, isPending } = useQuery({
    queryKey: ["post", postId],
    queryFn: () => api.post(postId),
    enabled: Number.isFinite(postId),
  });

  return (
    <div className="mx-auto max-w-[1280px] px-4 pb-24 pt-6 sm:px-6">
      <button
        type="button"
        onClick={() => (window.history.length > 1 ? router.back() : router.push("/"))}
        className="mb-6 flex items-center gap-2 text-sm font-medium text-muted hover:text-ink"
      >
        <ArrowLeftIcon />
        Back to feed
      </button>
      {error ? (
        <div className="rounded-xl border border-vermilion bg-vermilion-soft p-5 text-sm">
          <p className="font-semibold text-vermilion">This post didn’t load.</p>
          <p className="mt-1">{error.message}</p>
        </div>
      ) : isPending ? (
        <div className="space-y-3">
          <div className="h-8 w-2/3 animate-pulse rounded bg-track" />
          <div className="h-5 w-1/3 animate-pulse rounded bg-track" />
        </div>
      ) : (
        <Detail post={post} rate={meta?.usd_vnd_rate ?? 26_000} />
      )}
    </div>
  );
}
