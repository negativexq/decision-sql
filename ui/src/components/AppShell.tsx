import type { ReactNode } from "react";

const nav = ["Playground", "Runs", "Traces", "Schema"] as const;

export function AppShell({ page, onNavigate, children, health }: { page: string; onNavigate: (page: string) => void; children: ReactNode; health: { status: string; database: string } | null }) {
  return (
    <div className="shell">
      <header className="topbar">
        <div className="topbar-brand"><div className="brand-mark">DS</div><div className="brand-copy"><strong>Decision-SQL</strong><span>Governed query console</span></div></div>
        <div className="topbar-title"><span className="eyebrow">OPERATOR CONSOLE</span><h1>{page}</h1></div>
        <nav className="main-nav">{nav.map((item) => <button className={page === item ? "active" : ""} key={item} onClick={() => onNavigate(item)}><span className="nav-icon">{item.slice(0, 1)}</span>{item}</button>)}</nav>
        <div className="health"><span className={`health-dot ${health?.status === "ok" ? "ok" : "unknown"}`} />API {health?.status === "ok" ? "healthy" : "checking"}<span className="health-separator" /><span className={`health-dot ${health?.database === "ok" ? "ok" : "unknown"}`} />Database {health?.database === "ok" ? "ready" : "unavailable"}</div>
      </header>
      <main className="main"><div className="content">{children}</div></main>
    </div>
  );
}
