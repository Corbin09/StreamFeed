"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { usePathname } from "next/navigation";

import { api } from "@/lib/api";

function WorkerStatus() {
  const { data, isError } = useQuery({
    queryKey: ["worker-health"],
    queryFn: api.workerHealth,
    refetchInterval: 30_000,
  });
  const online = data?.status === "ok";
  const label = isError ? "API offline" : !data ? "Checking…" : online ? "Worker running" : "Worker stopped";
  return (
    <span className="flex items-center gap-2 text-sm text-muted" title={data?.last_beat_at ?? undefined}>
      <span
        aria-hidden
        className={`size-2 rounded-full ${online ? "bg-jade" : isError ? "bg-vermilion" : "bg-faint"}`}
      />
      {label}
    </span>
  );
}

export function SiteHeader() {
  const pathname = usePathname();
  const nav = [
    { href: "/", label: "Feed", active: pathname === "/" || pathname.startsWith("/posts") },
    { href: "/admin/sources", label: "Sources", active: pathname.startsWith("/admin") },
  ];
  return (
    <header className="border-b border-line bg-surface">
      <div className="mx-auto flex h-14 max-w-[1280px] items-center gap-6 px-4 sm:px-6">
        <Link href="/" className="flex items-center gap-2 text-[17px] font-bold tracking-tight">
          <svg aria-hidden viewBox="0 0 20 20" className="size-5">
            <rect x="1" y="4" width="18" height="2.5" rx="1.25" className="fill-track" />
            <rect x="6" y="4" width="9" height="2.5" rx="1.25" className="fill-jade" />
            <rect x="1" y="9" width="18" height="2.5" rx="1.25" className="fill-track" />
            <rect x="3" y="9" width="6" height="2.5" rx="1.25" className="fill-jade" />
            <rect x="1" y="14" width="18" height="2.5" rx="1.25" className="fill-track" />
            <rect x="9" y="14" width="9" height="2.5" rx="1.25" className="fill-jade" />
          </svg>
          StreamFeed
        </Link>
        <nav className="flex gap-1">
          {nav.map((item) => (
            <Link
              key={item.href}
              href={item.href}
              aria-current={item.active ? "page" : undefined}
              className={`rounded-md px-3 py-1.5 text-sm font-medium ${
                item.active ? "bg-paper text-ink" : "text-muted hover:text-ink"
              }`}
            >
              {item.label}
            </Link>
          ))}
        </nav>
        <div className="ml-auto hidden sm:block">
          <WorkerStatus />
        </div>
      </div>
    </header>
  );
}
