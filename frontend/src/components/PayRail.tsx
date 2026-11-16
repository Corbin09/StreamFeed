import { toUsd } from "@/lib/format";

/** Every rail shares this USD scale so pay can be compared down the feed at a glance. */
export const RAIL_MAX_USD = 6000;
export const RAIL_TICKS = [0, 1000, 2000, 3000, 4000, 5000, 6000];

const pct = (usd: number) => Math.min(100, Math.max(0, (usd / RAIL_MAX_USD) * 100));

type Props = {
  min: number | null;
  max: number | null;
  currency: string | null;
  rate: number;
  /** Draw the 1k gridlines behind the rail (the feed column shows them per row). */
  grid?: boolean;
  className?: string;
};

export function PayRail({ min, max, currency, rate, grid = true, className = "" }: Props) {
  const lo = toUsd(min, currency, rate);
  const hi = toUsd(max, currency, rate);

  let segment: React.CSSProperties | null = null;
  if (lo != null && hi != null) {
    // A single figure still gets a visible mark.
    segment = { left: `${pct(lo)}%`, width: `max(6px, ${pct(hi) - pct(lo)}%)` };
  } else if (hi != null) {
    // "Up to": fade in from zero to the ceiling.
    segment = {
      left: "0%",
      width: `${pct(hi)}%`,
      background: "linear-gradient(to right, transparent, var(--jade))",
    };
  } else if (lo != null) {
    // "From": solid start that fades towards higher pay.
    segment = {
      left: `${pct(lo)}%`,
      width: `${Math.min(18, 100 - pct(lo))}%`,
      background: "linear-gradient(to right, var(--jade), transparent)",
    };
  }
  const overflow = (hi ?? lo ?? 0) > RAIL_MAX_USD;

  return (
    <div
      className={`relative h-5 ${className}`}
      style={
        grid
          ? {
              backgroundImage: `repeating-linear-gradient(to right, var(--line) 0 1px, transparent 1px calc(100% / ${RAIL_TICKS.length - 1}))`,
              backgroundSize: "calc(100% + 1px) 100%",
              backgroundPosition: "0 0",
            }
          : undefined
      }
    >
      <div
        className={`absolute inset-x-0 top-1/2 h-1.5 -translate-y-1/2 rounded-full ${
          segment ? "bg-track" : "border-t-2 border-dashed border-line bg-transparent"
        }`}
      />
      {segment && (
        <div
          className="absolute top-1/2 h-1.5 -translate-y-1/2 rounded-full bg-jade"
          style={segment}
        />
      )}
      {overflow && (
        <span
          aria-hidden
          className="absolute -right-1 top-1/2 size-0 -translate-y-1/2 border-y-4 border-l-[5px] border-y-transparent border-l-jade"
        />
      )}
    </div>
  );
}

/** Tick labels aligned with the rails underneath. */
export function PayScale({ className = "" }: { className?: string }) {
  return (
    <div className={`relative h-4 text-[11px] text-faint tabular ${className}`} aria-hidden>
      {RAIL_TICKS.map((t, i) => (
        <span
          key={t}
          className="absolute top-0"
          style={{
            left: `${(i / (RAIL_TICKS.length - 1)) * 100}%`,
            transform: i === 0 ? "none" : i === RAIL_TICKS.length - 1 ? "translateX(-100%)" : "translateX(-50%)",
          }}
        >
          {t === 0 ? "$0" : t === RAIL_MAX_USD ? "6k+" : `${t / 1000}k`}
        </span>
      ))}
    </div>
  );
}
