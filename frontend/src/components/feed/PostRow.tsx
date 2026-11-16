"use client";

import Link from "next/link";

import { EyeOffIcon, StarIcon } from "@/components/icons";
import { PayRail } from "@/components/PayRail";
import type { PostItem } from "@/lib/api";
import { WORK_MODE_LABELS, formatPrice, humanize, locationLabel, sourceLabel, timeAgo } from "@/lib/format";

const MAX_SKILLS = 5;

type Props = {
  post: PostItem;
  rate: number;
  isNew: boolean;
  onSave: (post: PostItem) => void;
  onHide: (post: PostItem) => void;
};

export function PostRow({ post, rate, isNew, onSave, onHide }: Props) {
  const attrs = post.attributes;
  const locations = attrs.locations?.length ? attrs.locations : post.location ? [post.location] : [];
  const skills = attrs.skills ?? [];
  const price = formatPrice(post.price_min, post.price_max, post.currency);

  return (
    <li className="group grid gap-x-8 gap-y-3 border-b border-line px-4 py-4 hover:bg-surface sm:px-5 md:grid-cols-[minmax(0,1fr)_15rem]">
      <div className="min-w-0">
        <h2 className="text-[16px] font-semibold leading-snug">
          {isNew && (
            <span className="mr-2 inline-block size-2 -translate-y-0.5 rounded-full bg-saffron" title="New since your last visit">
              <span className="sr-only">New. </span>
            </span>
          )}
          <Link
            href={`/posts/${post.id}`}
            className="decoration-jade decoration-2 underline-offset-4 hover:underline"
            lang={post.language ?? undefined}
          >
            {post.title}
          </Link>
        </h2>

        <div className="mt-1 flex flex-wrap items-baseline gap-x-3 gap-y-1 text-sm text-muted">
          {post.company && <span className="font-medium text-ink">{post.company}</span>}
          {locations.length > 0 && <span>{locations.map(locationLabel).join(", ")}</span>}
          {attrs.work_mode && <span>{WORK_MODE_LABELS[attrs.work_mode] ?? humanize(attrs.work_mode)}</span>}
        </div>

        {(attrs.levels?.length || skills.length > 0) && (
          <div className="mt-2.5 flex flex-wrap items-center gap-1.5 text-[13px]">
            {attrs.levels?.map((level) => (
              <span key={level} className="rounded bg-jade-soft px-1.5 py-0.5 font-medium text-jade-strong">
                {humanize(level)}
              </span>
            ))}
            {skills.slice(0, MAX_SKILLS).map((skill) => (
              <span key={skill} className="rounded border border-line px-1.5 py-[1px] text-muted">
                {skill}
              </span>
            ))}
            {skills.length > MAX_SKILLS && <span className="text-faint">+{skills.length - MAX_SKILLS}</span>}
          </div>
        )}

        <div className="mt-2.5 flex flex-wrap gap-x-3 text-[13px] text-faint">
          <span>{sourceLabel(post.source.type)}</span>
          {post.also_on.length > 0 && <span>Also on {post.also_on.map(sourceLabel).join(", ")}</span>}
          <time dateTime={post.posted_at ?? post.crawled_at}>{timeAgo(post.posted_at ?? post.crawled_at)}</time>
        </div>
      </div>

      <div className="flex flex-col justify-between gap-2">
        <div>
          <PayRail min={post.price_min} max={post.price_max} currency={post.currency} rate={rate} />
          <p className={`mt-1 text-sm tabular ${price ? "font-semibold" : "text-faint"}`}>
            {price ?? (attrs.salary_negotiable ? "Negotiable" : "Not listed")}
          </p>
        </div>
        <div className="flex gap-1 md:justify-end">
          <button
            type="button"
            onClick={() => onSave(post)}
            aria-pressed={post.is_saved}
            className={`flex items-center gap-1.5 rounded-md px-2 py-1 text-sm ${
              post.is_saved ? "text-saffron" : "text-muted hover:bg-paper hover:text-ink"
            }`}
          >
            <StarIcon filled={post.is_saved} />
            {post.is_saved ? "Saved" : "Save"}
          </button>
          <button
            type="button"
            onClick={() => onHide(post)}
            className="flex items-center gap-1.5 rounded-md px-2 py-1 text-sm text-muted hover:bg-paper hover:text-ink"
          >
            <EyeOffIcon />
            Hide
          </button>
        </div>
      </div>
    </li>
  );
}
