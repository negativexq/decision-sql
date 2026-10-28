import { useMemo, useState } from "react";
import { formatCell, formatNumber, toNumber } from "../format";

type Row = Record<string, unknown>;

interface ChartShape {
  dimension: string | null;
  measures: string[];
  temporal: boolean;
}

const ISO_DATE = /^\d{4}-\d{2}(-\d{2})?([T ][\d:.]+)?(Z|[+-]\d{2}:?\d{2})?$/;

/** Pick a dimension and the numeric measures; null when the result is not chartable. */
export function chartShape(columns: string[], rows: Row[]): ChartShape | null {
  if (!rows.length) return null;
  const numeric = columns.filter((column) =>
    rows.every((row) => row[column] == null || toNumber(row[column]) !== null) &&
    rows.some((row) => toNumber(row[column]) !== null),
  );
  const others = columns.filter((column) => !numeric.includes(column));
  const temporal = others.find((column) =>
    rows.every((row) => row[column] == null || (typeof row[column] === "string" && ISO_DATE.test(row[column] as string))),
  );
  const dimension = temporal ?? others[0] ?? null;
  // Identifier-like numeric columns make poor measures when another measure exists.
  const candidates = numeric.length > 1 ? numeric.filter((column) => !/(^id$|_id$)/i.test(column)) : numeric;
  if (!candidates.length) return null;
  // Lead with the measure the rows are ordered by: "top 10 by revenue" should chart revenue.
  const sortedBy = candidates.find((column) => isOrderedBy(rows, column));
  const measures = sortedBy ? [sortedBy, ...candidates.filter((column) => column !== sortedBy)] : candidates;
  if (!dimension && rows.length > 1) return null;
  return { dimension, measures, temporal: Boolean(temporal) };
}

function isOrderedBy(rows: Row[], column: string): boolean {
  const values = rows.map((row) => toNumber(row[column]));
  if (values.length < 3 || values.some((value) => value === null)) return false;
  const numbers = values as number[];
  const descending = numbers.every((value, index) => index === 0 || value <= numbers[index - 1]);
  const ascending = numbers.every((value, index) => index === 0 || value >= numbers[index - 1]);
  return (descending || ascending) && numbers[0] !== numbers[numbers.length - 1];
}

export function ResultChart({ columns, rows }: { columns: string[]; rows: Row[] }) {
  const shape = useMemo(() => chartShape(columns, rows), [columns, rows]);
  const [measure, setMeasure] = useState<string | null>(null);
  if (!shape) {
    return (
      <div className="empty-panel">
        <strong>No chart for this result</strong>
        <p>Charts need at least one numeric column and a column to group by.</p>
      </div>
    );
  }
  const active = measure && shape.measures.includes(measure) ? measure : shape.measures[0];

  if (!shape.dimension) {
    return (
      <div className="kpi-grid">
        {shape.measures.map((column) => (
          <div className="kpi" key={column}>
            <span>{column}</span>
            <strong>{formatCell(rows[0][column])}</strong>
          </div>
        ))}
      </div>
    );
  }

  const points = rows.slice(0, 60).map((row) => ({
    label: formatCell(row[shape.dimension as string]),
    value: toNumber(row[active]) ?? 0,
  }));

  return (
    <div className="chart">
      <div className="chart-toolbar">
        <span className="chart-caption">
          {active} by {shape.dimension}
          {rows.length > points.length && ` (first ${points.length} rows)`}
        </span>
        {shape.measures.length > 1 && (
          <label className="select">
            <span className="visually-hidden">Measure</span>
            <select value={active} onChange={(event) => setMeasure(event.target.value)}>
              {shape.measures.map((column) => <option key={column} value={column}>{column}</option>)}
            </select>
          </label>
        )}
      </div>
      {shape.temporal ? <LineChart points={points} /> : <BarChart points={points} />}
    </div>
  );
}

function BarChart({ points }: { points: { label: string; value: number }[] }) {
  const max = Math.max(...points.map((point) => Math.abs(point.value)), 1);
  return (
    <ol className="bars">
      {points.map((point, index) => (
        <li key={`${point.label}-${index}`}>
          <span className="bar-label" title={point.label}>{point.label}</span>
          <span className="bar-track">
            <span className="bar-fill" style={{ width: `${(Math.abs(point.value) / max) * 100}%` }} />
          </span>
          <span className="bar-value">{formatNumber(point.value)}</span>
        </li>
      ))}
    </ol>
  );
}

/** Round an axis maximum up to a readable multiple of a power of ten. */
function niceCeil(value: number): number {
  const power = 10 ** Math.floor(Math.log10(value));
  const step = [1, 1.2, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10].find((candidate) => candidate * power >= value) ?? 10;
  return step * power;
}

function LineChart({ points }: { points: { label: string; value: number }[] }) {
  const width = 720;
  const height = 240;
  const pad = { top: 16, right: 16, bottom: 28, left: 56 };
  const values = points.map((point) => point.value);
  const min = Math.min(0, ...values);
  const max = niceCeil(Math.max(...values, 1));
  const x = (index: number) =>
    pad.left + (points.length === 1 ? 0.5 : index / (points.length - 1)) * (width - pad.left - pad.right);
  const y = (value: number) => pad.top + (1 - (value - min) / (max - min)) * (height - pad.top - pad.bottom);
  const path = points.map((point, index) => `${index ? "L" : "M"}${x(index)},${y(point.value)}`).join(" ");
  const ticks = [min, (min + max) / 2, max];
  const labelEvery = Math.max(1, Math.ceil(points.length / 6));
  return (
    <svg className="line-chart" viewBox={`0 0 ${width} ${height}`} role="img" aria-label="Line chart of the result">
      {ticks.map((tick) => (
        <g key={tick}>
          <line x1={pad.left} x2={width - pad.right} y1={y(tick)} y2={y(tick)} className="grid" />
          <text x={pad.left - 8} y={y(tick)} className="axis" textAnchor="end" dominantBaseline="middle">
            {formatNumber(tick)}
          </text>
        </g>
      ))}
      <path d={path} className="line" />
      {points.map((point, index) => (
        <g key={`${point.label}-${index}`}>
          <circle cx={x(index)} cy={y(point.value)} r={3} className="point">
            <title>{`${point.label}: ${formatNumber(point.value)}`}</title>
          </circle>
          {index % labelEvery === 0 && (
            <text x={x(index)} y={height - 8} className="axis" textAnchor="middle">
              {point.label.slice(0, 10)}
            </text>
          )}
        </g>
      ))}
    </svg>
  );
}
