import type { ReactNode } from "react";

const nav = ["Playground", "Runs", "Traces", "Schema"] as const;

export function AppShell({ page, onNavigate, children, health }: { page: string; onNavigate: (page: string) => void; children: ReactNode; health: { status: string; database: string } | null }) {
  return (
    <div className="shell">
      <aside className="sidebar">
        <div className="brand-mark">DS</div>
        <div className="brand-copy"><strong>Decision-SQL</strong><span>Governed query console</span></div>
        <nav>{nav.map((item) => <button className={page === item ? "active" : ""} key={item} onClick={() => onNavigate(item)}><span className="nav-icon">{item.slice(0, 1)}</span>{item}</button>)}</nav>
        <div className="sidebar-bottom"><div className="mini-label">Runtime posture</div><div className="posture"><span className="pulse" />Fail-closed by default</div></div>
      </aside>
      <main className="main"><header className="topbar"><div><span className="eyebrow">OPERATOR CONSOLE</span><h1>{page}</h1></div><div className="health"><span className={`health-dot ${health?.status === "ok" ? "ok" : "unknown"}`} />API {health?.status === "ok" ? "healthy" : "checking"}<span className="health-separator" /><span className={`health-dot ${health?.database === "ok" ? "ok" : "unknown"}`} />Database {health?.database === "ok" ? "ready" : "unavailable"}</div></header><div className="content">{children}</div></main>
    </div>
  );
}
