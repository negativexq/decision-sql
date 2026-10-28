import { formatCell, toNumber } from "../format";

export function ResultTable({ columns, rows, truncated }: { columns: string[]; rows: Record<string, unknown>[]; truncated: boolean }) {
  if (!columns.length) {
    return (
      <div className="empty-panel">
        <strong>The query returned no rows</strong>
        <p>Try widening the time range or removing a filter from the question.</p>
      </div>
    );
  }
  const numeric = new Set(columns.filter((column) => rows.some((row) => toNumber(row[column]) !== null)));
  return (
    <div className="table-wrap">
      <table className="data-table">
        <thead>
          <tr>{columns.map((column) => <th key={column} className={numeric.has(column) ? "num" : undefined}>{column}</th>)}</tr>
        </thead>
        <tbody>
          {rows.map((row, index) => (
            <tr key={index}>
              {columns.map((column) => (
                <td key={column} className={row[column] == null ? "null" : numeric.has(column) ? "num" : undefined}>
                  {formatCell(row[column])}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      {truncated && <p className="table-note">Showing the first {rows.length} rows. Narrow the question to see the rest.</p>}
    </div>
  );
}
