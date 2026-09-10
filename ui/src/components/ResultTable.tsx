export function ResultTable({ columns, rows, truncated }: { columns: string[]; rows: Record<string, unknown>[]; truncated: boolean }) {
  if (!columns.length) return <div className="empty-box">No result rows returned.</div>;
  return (
    <div className="table-wrap">
      <table>
        <thead><tr>{columns.map((column) => <th key={column}>{column}</th>)}</tr></thead>
        <tbody>
          {rows.map((row, index) => (
            <tr key={index}>{columns.map((column) => <td key={column}>{formatValue(row[column])}</td>)}</tr>
          ))}
        </tbody>
      </table>
      {truncated && <div className="table-note">Showing the bounded result window.</div>}
    </div>
  );
}
function formatValue(value: unknown): string {
  if (value === null || value === undefined) return "NULL";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}
