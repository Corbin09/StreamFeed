// Display formatting shared by the feed, the detail page and admin.

const MINUTE = 60_000;
const HOUR = 60 * MINUTE;
const DAY = 24 * HOUR;

export function timeAgo(iso: string | null | undefined, now = Date.now()): string {
  if (!iso) return "";
  const diff = Math.max(0, now - new Date(iso).getTime());
  if (diff < MINUTE) return "just now";
  if (diff < HOUR) return `${Math.floor(diff / MINUTE)}m ago`;
  if (diff < DAY) return `${Math.floor(diff / HOUR)}h ago`;
  if (diff < 30 * DAY) return `${Math.floor(diff / DAY)}d ago`;
  return new Date(iso).toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" });
}

export function fullDate(iso: string | null | undefined): string {
  if (!iso) return "Unknown";
  return new Date(iso).toLocaleString("en-GB", {
    day: "numeric",
    month: "short",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

const usd = (n: number) => n.toLocaleString("en-US", { maximumFractionDigits: 0 });

function millions(n: number): string {
  const m = n / 1_000_000;
  return Number.isInteger(m) ? String(m) : m.toFixed(1).replace(/\.0$/, "");
}

/** "$2,000–3,000", "15–25M VND", "Up to $2,000", "From 20M VND", or null. */
export function formatPrice(min: number | null, max: number | null, currency: string | null): string | null {
  if (min == null && max == null) return null;
  const isVnd = currency === "VND";
  const one = (n: number) => (isVnd ? millions(n) : usd(n));
  const wrap = (s: string) => (isVnd ? `${s}M VND` : `$${s}`);
  if (min != null && max != null) {
    return min === max ? wrap(one(min)) : wrap(`${one(min)}–${one(max)}`);
  }
  return max != null ? `Up to ${wrap(one(max))}` : `From ${wrap(one(min as number))}`;
}

export function toUsd(amount: number | null, currency: string | null, rate: number): number | null {
  if (amount == null) return null;
  return currency === "VND" ? amount / rate : amount;
}

export function intervalLabel(minutes: number): string {
  if (minutes % 60 === 0) return `Every ${minutes / 60}h`;
  return `Every ${minutes}m`;
}

export function humanize(key: string): string {
  const s = key.replace(/[_-]+/g, " ");
  return s.charAt(0).toUpperCase() + s.slice(1).toLowerCase();
}

const SOURCE_LABELS: Record<string, string> = {
  itviec: "ITviec",
  linkedin: "LinkedIn",
  facebook: "Facebook",
};

export const sourceLabel = (type: string) => SOURCE_LABELS[type] ?? humanize(type);

const LOCATION_LABELS: Record<string, string> = {
  hanoi: "Hanoi",
  hcmc: "Ho Chi Minh City",
  danang: "Da Nang",
  haiphong: "Hai Phong",
  cantho: "Can Tho",
  hue: "Hue",
  nhatrang: "Nha Trang",
  binhduong: "Binh Duong",
  dongnai: "Dong Nai",
  bacninh: "Bac Ninh",
  quynhon: "Quy Nhon",
  remote: "Remote",
  overseas: "Overseas",
};

export const locationLabel = (key: string) => LOCATION_LABELS[key] ?? humanize(key);

export const WORK_MODE_LABELS: Record<string, string> = {
  onsite: "On-site",
  hybrid: "Hybrid",
  remote: "Remote",
};
