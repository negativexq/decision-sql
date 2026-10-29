import { formatNumber, toNumber } from "../../format";
import type { CardDisplay } from "../../types";

type Row = Record<string, unknown>;

export const DEFAULT_DISPLAY: CardDisplay = {
  format: "auto",
  decimals: null,
  currency: "USD",
  sort: "query",
  limit: null,
  compare: "none",
  target: null,
  higher_is_better: true,
};

export interface Formatter {
  /** The full value, for numbers on cards, tables, and tooltips. */
  full: (value: number) => string;
  /** A short value, for axes and labels beside bars. */
  short: (value: number) => string;
}

function compact(value: number): string {
  const abs = Math.abs(value);
  if (abs >= 1e9) return `${formatNumber(value / 1e9)}B`;
  if (abs >= 1e6) return `${formatNumber(value / 1e6)}M`;
  if (abs >= 1e4) return `${formatNumber(value / 1e3)}K`;
  return formatNumber(value);
}

// The same locale as every other number in the app, so a card reads like its neighbours.
export function formatter(display: CardDisplay = DEFAULT_DISPLAY): Formatter {
  if (display.format === "auto") return { full: formatNumber, short: compact };
  const digits = display.decimals ?? (display.format === "currency" ? 2 : display.format === "percent" ? 1 : undefined);
  const base: Intl.NumberFormatOptions =
    display.format === "currency"
      ? { style: "currency", currency: display.currency }
      : display.format === "percent"
        ? { style: "percent" }
        : {};
  const exact = digits === undefined ? {} : { minimumFractionDigits: digits, maximumFractionDigits: digits };
  let full: Intl.NumberFormat;
  let short: Intl.NumberFormat;
  try {
    full = new Intl.NumberFormat("en-US", { ...base, ...exact });
    short = new Intl.NumberFormat("en-US", { ...base, notation: "compact", maximumFractionDigits: 1 });
  } catch {
    // An unknown currency code falls back to plain numbers rather than breaking the card.
    return { full: formatNumber, short: compact };
  }
  return { full: (value) => full.format(value), short: (value) => short.format(value) };
}

/** Rows in the order and number the card shows; the query's own rows are not changed. */
export function arrange(rows: Row[], value: string | undefined, category: string | undefined, display: CardDisplay): Row[] {
  let ordered = rows;
  if (display.sort === "value_desc" || display.sort === "value_asc") {
    if (value) {
      const sign = display.sort === "value_desc" ? -1 : 1;
      ordered = [...rows].sort((a, b) => sign * ((toNumber(a[value]) ?? -Infinity) - (toNumber(b[value]) ?? -Infinity)));
    }
  } else if (display.sort === "label" && category) {
    ordered = [...rows].sort((a, b) => String(a[category] ?? "").localeCompare(String(b[category] ?? ""), undefined, { numeric: true }));
  }
  return display.limit ? ordered.slice(0, display.limit) : ordered;
}

export interface Comparison {
  label: string;
  /** The change as a ratio of the reference, or null when the reference is zero. */
  change: number | null;
  difference: number;
  good: boolean;
}

/** A number card's first value against its second row or its target. */
export function compare(rows: Row[], value: string, display: CardDisplay): Comparison | null {
  const current = toNumber(rows[0]?.[value]);
  if (current === null || display.compare === "none") return null;
  const reference = display.compare === "target" ? display.target : toNumber(rows[1]?.[value]);
  if (reference === null || reference === undefined) return null;
  const difference = current - reference;
  return {
    label: display.compare === "target" ? "target" : "second row",
    change: reference === 0 ? null : difference / Math.abs(reference),
    difference,
    good: difference === 0 || difference > 0 === display.higher_is_better,
  };
}
