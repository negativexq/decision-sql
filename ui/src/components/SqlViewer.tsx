import { useState } from "react";

export function SqlViewer({ sql }: { sql: string | null }) {
  const [copied, setCopied] = useState(false);
  if (!sql) return <div className="empty-box">No SQL proposal entered the runtime.</div>;
  return (
    <div className="sql-box">
      <button type="button" className="copy-button" onClick={() => { void navigator.clipboard.writeText(sql); setCopied(true); }}>
        {copied ? "Copied" : "Copy SQL"}
      </button>
      <pre><code>{sql}</code></pre>
    </div>
  );
}
