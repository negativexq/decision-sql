import { useEffect, useRef, useState } from "react";
import { formatCell, toNumber } from "../../format";
import { arrange, compare, DEFAULT_DISPLAY, formatter, type Formatter } from "./display";
import type { CardDisplay, ChartType } from "../../types";

type Row = Record<string, unknown>;

export const CHART_TYPES: { id: ChartType; label: string }[] = [
  { id: "number", label: "Number" },
  { id: "bar", label: "Bars" },
  { id: "column", label: "Columns" },
  { id: "line", label: "Line" },
  { id: "area", label: "Area" },
  { id: "pie", label: "Pie" },
  { id: "stacked", label: "Stacked columns" },
  { id: "lines", label: "Lines by series" },
  { id: "scatter", label: "Scatter" },
  { id: "table", label: "Table" },
];

/** Chart types that draw one series per value of a series column. */
export const SERIES_CHARTS: ChartType[] = ["stacked", "lines"];

// Categorical colours for slices; the first is the app accent. Every slice also carries
// its label and value in the legend, so colour is never the only cue.
const SERIES = ["#2f5bea", "#15a08c", "#e0901b", "#8a4fd0", "#d4485b", "#5b8bd9", "#7a8b2e", "#a06a3c"];
const OTHER = "#c4c7cf";
const MAX_SLICES = 7;
const MAX_POINTS = 60;
const MAX_SERIES = 7;
const MAX_SCATTER = 1000;

const ISO_DATE = /^\d{4}-\d{2}(-\d{2})?([T ][\d:.]+)?(Z|[+-]\d{2}:?\d{2})?$/;

export interface Columns {
  numeric: string[];
  other: string[];
}

export function splitColumns(columns: string[], rows: Row[]): Columns {
  const numeric = columns.filter(
    (column) =>
      rows.some((row) => toNumber(row[column]) !== null) &&
      rows.every((row) => row[column] == null || toNumber(row[column]) !== null),
  );
  return { numeric, other: columns.filter((column) => !numeric.includes(column)) };
}

/** The chart a result suggests before anyone picks one. */
export function suggestChart(columns: string[], rows: Row[]): ChartType {
  const { numeric, other } = splitColumns(columns, rows);
  if (!numeric.length) return "table";
  if (rows.length === 1 && numeric.length >= 1 && other.length === 0) return "number";
  const temporal = (column: string) =>
    rows.every((row) => row[column] == null || (typeof row[column] === "string" && ISO_DATE.test(row[column] as string)));
  // A category, a series, and a value: "revenue by month and region".
  if (other.length === 2 && numeric.length === 1) return other.some(temporal) ? "lines" : "stacked";
  if (other.length !== 1) return "table";
  if (temporal(other[0])) return "line";
  if (rows.length <= 6) return "pie";
  return "bar";
}

/** Measure the element so SVG text keeps its size whatever shape the card has. */
function useSize<T extends HTMLElement>() {
  const ref = useRef<T | null>(null);
  const [size, setSize] = useState({ width: 0, height: 0 });
  useEffect(() => {
    const element = ref.current;
    if (!element) return;
    const observer = new ResizeObserver(([entry]) => {
      const { width, height } = entry.contentRect;
      setSize({ width: Math.floor(width), height: Math.floor(height) });
    });
    observer.observe(element);
    return () => observer.disconnect();
  }, []);
  return [ref, size] as const;
}

function niceCeil(value: number): number {
  if (value <= 0) return 1;
  const power = 10 ** Math.floor(Math.log10(value));
  const step = [1, 1.2, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10].find((candidate) => candidate * power >= value) ?? 10;
  return step * power;
}

export function ReportChart({
  type,
  columns,
  rows,
  dimension,
  measure,
  series = null,
  emptyText = "The query returned no rows.",
  display = DEFAULT_DISPLAY,
}: {
  type: ChartType;
  columns: string[];
  rows: Row[];
  dimension: string | null;
  measure: string | null;
  series?: string | null;
  emptyText?: string;
  display?: CardDisplay;
}) {
  const [ref, size] = useSize<HTMLDivElement>();
  const { numeric, other } = splitColumns(columns, rows);
  const fmt = formatter(display);

  if (!rows.length) {
    return <div className="rc-empty">{emptyText}</div>;
  }
  const value = measure && numeric.includes(measure) ? measure : numeric[0];
  const category = dimension && columns.includes(dimension) && dimension !== value ? dimension : other[0] ?? columns.find((column) => column !== value);
  if (type === "table") {
    const shown = arrange(rows, value, category, display);
    return (
      <MiniTable
        columns={columns}
        rows={shown}
        formatted={display.format === "auto" ? undefined : value}
        fmt={fmt}
        clipped={shown.length < rows.length ? rows.length : undefined}
      />
    );
  }

  if (type === "number" && !value && rows.length === 1) {
    // An aggregate over no rows comes back as one empty value, as SUM does.
    const column = measure ?? columns[0];
    if (rows[0][column] === null || rows[0][column] === undefined) {
      return (
        <div className="rc-number">
          <strong>—</strong>
          <span>{column}, no value</span>
        </div>
      );
    }
  }
  if (!value) {
    return <div className="rc-empty">This result has no numeric column to chart. Choose Table instead.</div>;
  }
  if (type === "number") {
    const number = toNumber(rows[0][value]);
    const versus = compare(rows, value, display);
    return (
      <div className="rc-number">
        <strong>{number === null ? formatCell(rows[0][value]) : display.format === "auto" ? formatCell(rows[0][value]) : fmt.full(number)}</strong>
        <span>{value}{rows.length > 1 && display.compare !== "second_row" ? ", first row" : ""}</span>
        {versus && (
          <span className={versus.good ? "rc-delta good" : "rc-delta bad"}>
            <span aria-hidden="true">{versus.difference > 0 ? "▲" : versus.difference < 0 ? "▼" : "•"}</span>{" "}
            {versus.change === null ? fmt.full(versus.difference) : `${(Math.abs(versus.change) * 100).toFixed(1)}%`} vs {versus.label}
            {display.compare === "target" && display.target !== null ? ` (${fmt.full(display.target)})` : ""}
          </span>
        )}
      </div>
    );
  }

  if (type === "scatter") {
    const x = dimension && numeric.includes(dimension) && dimension !== value ? dimension : numeric.find((column) => column !== value);
    if (!x) return <div className="rc-empty">A scatter needs two numeric columns. Choose another chart.</div>;
    const label = other[0];
    const dots = rows.slice(0, MAX_SCATTER).flatMap((row) => {
      const px = toNumber(row[x]);
      const py = toNumber(row[value]);
      return px === null || py === null ? [] : [{ x: px, y: py, label: label ? formatCell(row[label]) : "" }];
    });
    return (
      <div className="rc-frame">
        <div className="rc-plot" ref={ref}>
          {size.width > 0 && size.height > 0 && <Scatter dots={dots} width={size.width} height={size.height} fmt={fmt} />}
        </div>
        <p className="rc-caption">
          {value} against {x}
          {rows.length > MAX_SCATTER ? `, ${MAX_SCATTER} of ${rows.length} rows` : ""}
        </p>
      </div>
    );
  }

  if (SERIES_CHARTS.includes(type)) {
    const split = series && columns.includes(series) && series !== value && series !== category ? series : other.find((column) => column !== category);
    if (!split || !category) {
      return <div className="rc-empty">This chart needs a category column and a series column. Choose them in Edit, or pick another chart.</div>;
    }
    const grid = pivot(rows, category, split, value, display);
    return (
      <div className="rc-frame">
        <div className="rc-plot" ref={ref}>
          {size.width > 0 && size.height > 0 && (
            <>
              {type === "stacked" && <Stacked grid={grid} width={size.width} height={size.height} fmt={fmt} />}
              {type === "lines" && <Lines grid={grid} width={size.width} height={size.height} fmt={fmt} />}
            </>
          )}
        </div>
        <ul className="rc-legend rc-legend-row">
          {grid.series.map((item) => (
            <li key={item.name}>
              <span className="rc-swatch" style={{ background: item.color }} aria-hidden="true" />
              <span className="rc-legend-label" title={item.name}>{item.name}</span>
            </li>
          ))}
        </ul>
        <p className="rc-caption">
          {value} by {category} and {split}
          {grid.clipped ? `, ${grid.labels.length} of ${grid.clipped} ${category} values` : ""}
        </p>
      </div>
    );
  }

  const arranged = arrange(rows, value, category, display);
  const label = category ? dateLabeler(arranged.map((row) => row[category])) : null;
  const points = arranged.slice(0, MAX_POINTS).map((row, index) => ({
    label: category ? (label ? label(row[category]) : formatCell(row[category])) : String(index + 1),
    value: toNumber(row[value]) ?? 0,
  }));
  const clipped = rows.length > points.length;

  return (
    <div className="rc-frame">
      <div className="rc-plot" ref={ref}>
        {size.width > 0 && size.height > 0 && (
          <>
            {type === "bar" && <Bars points={points} width={size.width} height={size.height} fmt={fmt} />}
            {type === "column" && <Columns points={points} width={size.width} height={size.height} fmt={fmt} />}
            {(type === "line" || type === "area") && (
              <Line points={points} width={size.width} height={size.height} filled={type === "area"} fmt={fmt} />
            )}
            {type === "pie" && <Pie points={points} width={size.width} height={size.height} fmt={fmt} />}
          </>
        )}
      </div>
      <p className="rc-caption">
        {value}{category ? ` by ${category}` : ""}
        {clipped ? `, ${points.length} of ${rows.length} rows` : ""}
      </p>
    </div>
  );
}

type Point = { label: string; value: number };

interface Grid2 {
  labels: string[];
  series: { name: string; color: string; values: (number | null)[] }[];
  /** How many categories there were, when fewer are shown. */
  clipped: number | null;
}

/**
 * Rows of (category, series, value) as one row of values per series. The largest
 * series by total keep their own colour; the rest are summed as Other.
 */
function pivot(rows: Row[], category: string, split: string, value: string, display: CardDisplay): Grid2 {
  const keys: unknown[] = [];
  const index = new Map<string, number>();
  const cells = new Map<string, Map<number, number>>();
  const totals = new Map<string, number>();
  for (const row of rows) {
    const key = String(row[category] ?? "");
    if (!index.has(key)) {
      index.set(key, keys.length);
      keys.push(row[category]);
    }
    const name = row[split] == null ? "(none)" : formatCell(row[split]);
    const amount = toNumber(row[value]);
    if (amount === null) continue;
    const at = index.get(key)!;
    const line = cells.get(name) ?? new Map<number, number>();
    line.set(at, (line.get(at) ?? 0) + amount);
    cells.set(name, line);
    totals.set(name, (totals.get(name) ?? 0) + Math.abs(amount));
  }
  let order = keys.map((_, at) => at);
  const sum = (at: number) => [...cells.values()].reduce((total, line) => total + (line.get(at) ?? 0), 0);
  if (display.sort === "label") {
    order.sort((a, b) => String(keys[a] ?? "").localeCompare(String(keys[b] ?? ""), undefined, { numeric: true }));
  } else if (display.sort === "value_desc" || display.sort === "value_asc") {
    const sign = display.sort === "value_desc" ? -1 : 1;
    order.sort((a, b) => sign * (sum(a) - sum(b)));
  }
  const limit = Math.min(display.limit ?? MAX_POINTS, MAX_POINTS);
  const clipped = order.length > limit ? order.length : null;
  order = order.slice(0, limit);
  const labeler = dateLabeler(order.map((at) => keys[at]));
  const labels = order.map((at) => (labeler ? labeler(keys[at]) : formatCell(keys[at])));

  const names = [...totals.keys()].sort((a, b) => totals.get(b)! - totals.get(a)!);
  const kept = names.slice(0, names.length > MAX_SERIES ? MAX_SERIES - 1 : MAX_SERIES);
  const rest = names.slice(kept.length);
  const values = (pick: string[]) =>
    order.map((at) => {
      const found = pick.map((name) => cells.get(name)?.get(at)).filter((amount): amount is number => amount !== undefined);
      return found.length ? found.reduce((total, amount) => total + amount, 0) : null;
    });
  const series = kept.map((name, at) => ({ name, color: SERIES[at % SERIES.length], values: values([name]) }));
  if (rest.length) series.push({ name: `Other (${rest.length})`, color: OTHER, values: values(rest) });
  return { labels, series, clipped };
}

function Stacked({ grid, width, height, fmt }: { grid: Grid2; width: number; height: number; fmt: Formatter }) {
  const pad = { top: 10, right: 8, bottom: 22, left: 44 };
  // Positive values stack up from zero and negative ones down, so each part keeps its sign.
  const ups = grid.labels.map((_, at) => grid.series.reduce((total, item) => total + Math.max(item.values[at] ?? 0, 0), 0));
  const downs = grid.labels.map((_, at) => grid.series.reduce((total, item) => total + Math.min(item.values[at] ?? 0, 0), 0));
  const max = niceCeil(Math.max(...ups, 0));
  const min = Math.min(0, ...downs);
  const plotW = width - pad.left - pad.right;
  const band = plotW / Math.max(1, grid.labels.length);
  const y = (amount: number) => pad.top + (1 - (amount - min) / (max - min || 1)) * (height - pad.top - pad.bottom);
  const labelEvery = Math.max(1, Math.ceil(grid.labels.length / Math.max(1, Math.floor(plotW / 56))));
  return (
    <svg width={width} height={height} role="img" aria-label="Stacked column chart">
      <Grid min={min} max={max} y={y} left={pad.left} right={width - pad.right} fmt={fmt} />
      {grid.labels.map((label, at) => {
        let up = 0;
        let down = 0;
        return (
          <g key={`${label}-${at}`}>
            {grid.series.map((item) => {
              const amount = item.values[at];
              if (!amount) return null;
              const from = amount > 0 ? up : down;
              const to = from + amount;
              if (amount > 0) up = to;
              else down = to;
              const top = y(Math.max(from, to));
              return (
                <rect
                  key={item.name}
                  x={pad.left + at * band + band * 0.15}
                  y={top}
                  width={Math.max(1, band * 0.7)}
                  height={Math.max(1, y(Math.min(from, to)) - top)}
                  fill={item.color}
                  stroke="var(--bg)"
                  strokeWidth={0.5}
                >
                  <title>{`${label}, ${item.name}: ${fmt.full(amount)}`}</title>
                </rect>
              );
            })}
            {at % labelEvery === 0 && (
              <text x={pad.left + at * band + band / 2} y={height - 6} className="rc-axis" textAnchor="middle">
                {truncate(label, Math.max(4, Math.floor((band * labelEvery) / 7)))}
              </text>
            )}
          </g>
        );
      })}
    </svg>
  );
}

function Lines({ grid, width, height, fmt }: { grid: Grid2; width: number; height: number; fmt: Formatter }) {
  const pad = { top: 10, right: 12, bottom: 22, left: 44 };
  const all = grid.series.flatMap((item) => item.values.filter((amount): amount is number => amount !== null));
  const min = Math.min(0, ...all);
  const max = niceCeil(Math.max(...all, 0));
  const plotW = width - pad.left - pad.right;
  const count = grid.labels.length;
  const x = (at: number) => pad.left + (count === 1 ? 0.5 : at / (count - 1)) * plotW;
  const y = (amount: number) => pad.top + (1 - (amount - min) / (max - min || 1)) * (height - pad.top - pad.bottom);
  const labelEvery = Math.max(1, Math.ceil(count / Math.max(1, Math.floor(plotW / 64))));
  // A category a series has no row for breaks its line instead of drawing a zero.
  const path = (values: (number | null)[]) =>
    values.map((amount, at) => (amount === null ? "" : `${at && values[at - 1] !== null ? "L" : "M"}${x(at)},${y(amount)}`)).join(" ");
  return (
    <svg width={width} height={height} role="img" aria-label="Line chart by series">
      <Grid min={min} max={max} y={y} left={pad.left} right={width - pad.right} fmt={fmt} />
      {grid.series.map((item) => (
        <g key={item.name}>
          <path d={path(item.values)} fill="none" stroke={item.color} strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />
          {count <= 40 &&
            item.values.map((amount, at) =>
              amount === null ? null : (
                <circle key={at} cx={x(at)} cy={y(amount)} r={2.5} fill="var(--bg)" stroke={item.color} strokeWidth={1.5}>
                  <title>{`${grid.labels[at]}, ${item.name}: ${fmt.full(amount)}`}</title>
                </circle>
              ),
            )}
        </g>
      ))}
      {grid.labels.map((label, at) =>
        at % labelEvery === 0 ? (
          <text
            key={`${label}-${at}`}
            x={x(at)}
            y={height - 6}
            className="rc-axis"
            textAnchor={at === 0 && count > 1 ? "start" : at === count - 1 && count > 1 ? "end" : "middle"}
          >
            {truncate(label, 10)}
          </text>
        ) : null,
      )}
    </svg>
  );
}

function Scatter({ dots, width, height, fmt }: { dots: { x: number; y: number; label: string }[]; width: number; height: number; fmt: Formatter }) {
  if (!dots.length) return null;
  const pad = { top: 10, right: 16, bottom: 22, left: 44 };
  const xs = dots.map((dot) => dot.x);
  const ys = dots.map((dot) => dot.y);
  const [xMin, xMax] = [Math.min(...xs), Math.max(...xs)];
  const yMin = Math.min(0, ...ys);
  const yMax = niceCeil(Math.max(...ys, 0));
  const plotW = width - pad.left - pad.right;
  const x = (amount: number) => pad.left + (xMax === xMin ? 0.5 : (amount - xMin) / (xMax - xMin)) * plotW;
  const y = (amount: number) => pad.top + (1 - (amount - yMin) / (yMax - yMin || 1)) * (height - pad.top - pad.bottom);
  const xTicks = xMax === xMin ? [xMin] : [xMin, (xMin + xMax) / 2, xMax];
  // The x axis keeps plain numbers: the card's format is for the value it charts.
  const plain = (amount: number) => (Math.abs(amount) >= 1e4 ? `${Math.round(amount / 1e3)}K` : String(Math.round(amount * 100) / 100));
  return (
    <svg width={width} height={height} role="img" aria-label="Scatter chart">
      <Grid min={yMin} max={yMax} y={y} left={pad.left} right={width - pad.right} fmt={fmt} />
      {xTicks.map((tick, at) => (
        <text
          key={tick}
          x={x(tick)}
          y={height - 6}
          className="rc-axis"
          textAnchor={xTicks.length > 1 && at === 0 ? "start" : at === xTicks.length - 1 && xTicks.length > 1 ? "end" : "middle"}
        >
          {plain(tick)}
        </text>
      ))}
      {dots.map((dot, at) => (
        <circle key={at} cx={x(dot.x)} cy={y(dot.y)} r={3} fill={SERIES[0]} fillOpacity={0.55} stroke={SERIES[0]} strokeWidth={0.75}>
          <title>{`${dot.label ? `${dot.label}: ` : ""}${plain(dot.x)}, ${fmt.full(dot.y)}`}</title>
        </circle>
      ))}
    </svg>
  );
}

/**
 * Short labels for a date column: months when every value is the first of a month,
 * days when every time is midnight. Null when the column is not dates.
 */
function dateLabeler(values: unknown[]): ((value: unknown) => string) | null {
  const texts = values.filter((value): value is string => typeof value === "string");
  if (!texts.length || texts.length !== values.filter((value) => value != null).length) return null;
  if (!texts.every((text) => ISO_DATE.test(text))) return null;
  const midnight = texts.every((text) => text.length <= 10 || /[T ]00:00(:00(\.0+)?)?/.test(text));
  const firstOfMonth = midnight && texts.every((text) => text.slice(8, 10) === "01" || text.length === 7);
  return (value) => {
    if (typeof value !== "string") return formatCell(value);
    if (firstOfMonth) return value.slice(0, 7);
    if (midnight) return value.slice(0, 10);
    return value.slice(0, 16).replace("T", " ");
  };
}

function Bars({ points, width, height, fmt }: { points: Point[]; width: number; height: number; fmt: Formatter }) {
  const labelWidth = Math.min(160, Math.max(64, width * 0.3));
  const valueWidth = 56;
  const row = Math.max(14, Math.min(28, height / points.length));
  const shown = points.slice(0, Math.max(1, Math.floor(height / row)));
  const max = Math.max(...shown.map((point) => Math.abs(point.value)), 1);
  const track = Math.max(10, width - labelWidth - valueWidth - 12);
  return (
    <svg width={width} height={height} role="img" aria-label="Bar chart">
      {shown.map((point, index) => {
        const y = index * row;
        const length = (Math.abs(point.value) / max) * track;
        return (
          <g key={`${point.label}-${index}`}>
            <text x={labelWidth - 8} y={y + row / 2} className="rc-axis" textAnchor="end" dominantBaseline="middle">
              {truncate(point.label, Math.floor(labelWidth / 7))}
            </text>
            <rect x={labelWidth} y={y + row * 0.2} width={Math.max(1, length)} height={row * 0.6} rx={2} fill={SERIES[0]}>
              <title>{`${point.label}: ${fmt.full(point.value)}`}</title>
            </rect>
            <text x={labelWidth + length + 6} y={y + row / 2} className="rc-value" dominantBaseline="middle">
              {fmt.short(point.value)}
            </text>
          </g>
        );
      })}
    </svg>
  );
}

function Columns({ points, width, height, fmt }: { points: Point[]; width: number; height: number; fmt: Formatter }) {
  const pad = { top: 10, right: 8, bottom: 22, left: 44 };
  const max = niceCeil(Math.max(...points.map((point) => point.value), 0));
  const min = Math.min(0, ...points.map((point) => point.value));
  const plotW = width - pad.left - pad.right;
  const plotH = height - pad.top - pad.bottom;
  const band = plotW / points.length;
  const y = (value: number) => pad.top + (1 - (value - min) / (max - min || 1)) * plotH;
  const labelEvery = Math.max(1, Math.ceil(points.length / Math.max(1, Math.floor(plotW / 56))));
  return (
    <svg width={width} height={height} role="img" aria-label="Column chart">
      <Grid min={min} max={max} y={y} left={pad.left} right={width - pad.right} fmt={fmt} />
      {points.map((point, index) => {
        const top = y(Math.max(point.value, 0));
        const bottom = y(Math.min(point.value, 0));
        return (
          <g key={`${point.label}-${index}`}>
            <rect
              x={pad.left + index * band + band * 0.15}
              y={top}
              width={Math.max(1, band * 0.7)}
              height={Math.max(1, bottom - top)}
              rx={2}
              fill={SERIES[0]}
            >
              <title>{`${point.label}: ${fmt.full(point.value)}`}</title>
            </rect>
            {index % labelEvery === 0 && (
              <text x={pad.left + index * band + band / 2} y={height - 6} className="rc-axis" textAnchor="middle">
                {truncate(point.label, Math.max(4, Math.floor((band * labelEvery) / 7)))}
              </text>
            )}
          </g>
        );
      })}
    </svg>
  );
}

function Line({
  points,
  width,
  height,
  filled,
  fmt,
}: {
  points: Point[];
  width: number;
  height: number;
  filled: boolean;
  fmt: Formatter;
}) {
  const pad = { top: 10, right: 12, bottom: 22, left: 44 };
  const values = points.map((point) => point.value);
  const min = Math.min(0, ...values);
  const max = niceCeil(Math.max(...values, 0));
  const plotW = width - pad.left - pad.right;
  const x = (index: number) => pad.left + (points.length === 1 ? 0.5 : index / (points.length - 1)) * plotW;
  const y = (value: number) => pad.top + (1 - (value - min) / (max - min || 1)) * (height - pad.top - pad.bottom);
  const line = points.map((point, index) => `${index ? "L" : "M"}${x(index)},${y(point.value)}`).join(" ");
  const area = `${line} L${x(points.length - 1)},${y(Math.max(min, 0))} L${x(0)},${y(Math.max(min, 0))} Z`;
  const labelEvery = Math.max(1, Math.ceil(points.length / Math.max(1, Math.floor(plotW / 64))));
  return (
    <svg width={width} height={height} role="img" aria-label={filled ? "Area chart" : "Line chart"}>
      <Grid min={min} max={max} y={y} left={pad.left} right={width - pad.right} fmt={fmt} />
      {filled && <path d={area} fill={SERIES[0]} opacity={0.14} />}
      <path d={line} fill="none" stroke={SERIES[0]} strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />
      {points.map((point, index) => (
        <g key={`${point.label}-${index}`}>
          {points.length <= 40 && (
            <circle cx={x(index)} cy={y(point.value)} r={2.5} fill="var(--bg)" stroke={SERIES[0]} strokeWidth={1.5}>
              <title>{`${point.label}: ${fmt.full(point.value)}`}</title>
            </circle>
          )}
          {index % labelEvery === 0 && (
            <text
              x={x(index)}
              y={height - 6}
              className="rc-axis"
              textAnchor={index === 0 && points.length > 1 ? "start" : index === points.length - 1 && points.length > 1 ? "end" : "middle"}
            >
              {truncate(point.label, 10)}
            </text>
          )}
        </g>
      ))}
    </svg>
  );
}

function Grid({
  min,
  max,
  y,
  left,
  right,
  fmt,
}: {
  min: number;
  max: number;
  y: (value: number) => number;
  left: number;
  right: number;
  fmt: Formatter;
}) {
  const ticks = [min, min + (max - min) / 2, max];
  return (
    <g>
      {ticks.map((tick) => (
        <g key={tick}>
          <line x1={left} x2={right} y1={y(tick)} y2={y(tick)} className="rc-grid" />
          <text x={left - 6} y={y(tick)} className="rc-axis" textAnchor="end" dominantBaseline="middle">
            {fmt.short(tick)}
          </text>
        </g>
      ))}
    </g>
  );
}

function Pie({ points, width, height, fmt }: { points: Point[]; width: number; height: number; fmt: Formatter }) {
  const positive = points.filter((point) => point.value > 0).sort((a, b) => b.value - a.value);
  const head = positive.slice(0, MAX_SLICES);
  const rest = positive.slice(MAX_SLICES).reduce((sum, point) => sum + point.value, 0);
  const slices = rest > 0 ? [...head, { label: "Other", value: rest }] : head;
  const total = slices.reduce((sum, point) => sum + point.value, 0);
  if (!total) return <div className="rc-empty">A pie needs positive values.</div>;
  const legendBeside = width > height * 1.3;
  const chartSize = Math.max(40, Math.min(legendBeside ? width * 0.5 : width, legendBeside ? height : height * 0.62) - 8);
  const r = chartSize / 2;
  const inner = r * 0.58;
  let angle = -Math.PI / 2;
  const arcs = slices.map((slice, index) => {
    const sweep = (slice.value / total) * Math.PI * 2;
    const start = angle;
    angle += sweep;
    return { ...slice, start, end: angle, color: slice.label === "Other" && rest > 0 && index === slices.length - 1 ? OTHER : SERIES[index % SERIES.length] };
  });
  return (
    <div className={legendBeside ? "rc-pie beside" : "rc-pie"}>
      <svg width={chartSize} height={chartSize} viewBox={`${-r} ${-r} ${chartSize} ${chartSize}`} role="img" aria-label="Pie chart">
        {arcs.map((arc) => (
          <path key={arc.label} d={donutPath(r - 1, inner, arc.start, arc.end)} fill={arc.color} stroke="var(--bg)" strokeWidth={1.5}>
            <title>{`${arc.label}: ${fmt.full(arc.value)}`}</title>
          </path>
        ))}
        <text y={-4} textAnchor="middle" className="rc-pie-total">{fmt.short(total)}</text>
        <text y={12} textAnchor="middle" className="rc-axis">total</text>
      </svg>
      <ul className="rc-legend">
        {arcs.map((arc) => (
          <li key={arc.label}>
            <span className="rc-swatch" style={{ background: arc.color }} aria-hidden="true" />
            <span className="rc-legend-label" title={arc.label}>{arc.label}</span>
            <span className="rc-legend-value">{Math.round((arc.value / total) * 100)}%</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

function donutPath(outer: number, inner: number, start: number, end: number): string {
  // A full circle cannot be drawn as one arc; stop just short of it.
  const stop = end - start >= Math.PI * 2 ? end - 0.0001 : end;
  const large = stop - start > Math.PI ? 1 : 0;
  const point = (radius: number, theta: number) => `${radius * Math.cos(theta)},${radius * Math.sin(theta)}`;
  return [
    `M${point(outer, start)}`,
    `A${outer},${outer} 0 ${large} 1 ${point(outer, stop)}`,
    `L${point(inner, stop)}`,
    `A${inner},${inner} 0 ${large} 0 ${point(inner, start)}`,
    "Z",
  ].join(" ");
}

function MiniTable({
  columns,
  rows,
  formatted,
  fmt,
  clipped,
}: {
  columns: string[];
  rows: Row[];
  formatted?: string;
  fmt: Formatter;
  clipped?: number;
}) {
  const cell = (column: string, value: unknown) => {
    const number = column === formatted ? toNumber(value) : null;
    return number === null ? formatCell(value) : fmt.full(number);
  };
  return (
    <div className="rc-table">
      <table>
        <thead>
          <tr>{columns.map((column) => <th key={column}>{column}</th>)}</tr>
        </thead>
        <tbody>
          {rows.slice(0, 200).map((row, index) => (
            <tr key={index}>
              {columns.map((column) => (
                <td key={column} className={toNumber(row[column]) !== null ? "num" : undefined}>{cell(column, row[column])}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      {clipped && <p className="rc-caption">{rows.length} of {clipped} rows</p>}
    </div>
  );
}

function truncate(text: string, length: number): string {
  return text.length > length ? `${text.slice(0, Math.max(1, length - 1))}…` : text;
}
