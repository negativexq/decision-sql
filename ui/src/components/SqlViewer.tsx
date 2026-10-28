import { useState } from "react";

export function SqlViewer({ sql }: { sql: string | null }) {
  const [copied, setCopied] = useState(false);
  if (!sql) {
    return (
      <div className="empty-panel">
        <strong>No SQL was written</strong>
        <p>The model answered without proposing a query, so nothing reached the database.</p>
      </div>
    );
  }
  return (
    <div className="sql-box">
      <button
        type="button"
        className="button ghost small copy"
        onClick={() => {
          void navigator.clipboard.writeText(sql).then(() => setCopied(true));
        }}
      >
        {copied ? "Copied" : "Copy"}
      </button>
      <pre><code>{sql}</code></pre>
    </div>
  );
}
