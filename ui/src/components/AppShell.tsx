import type { ReactNode } from "react";

const nav = ["Playground", "Runs", "Traces", "Schema"] as const;

function NavIcon({ name }: { name: string }) {
  const paths: Record<string, string> = {
    Playground: "M4 5.5h16M4 12h10M4 18.5h16",
    Runs: "M5 5h14v14H5z M8 9h8M8 13h5",
    Traces: "M5 5h14v14H5z M8 9h8M8 13h5M8 17h3",
    Schema: "M5 6.5 12 3l7 3.5v11L12 21l-7-3.5z M5 6.5 12 10l7-3.5 M12 10v11",
  };
  return <svg className="nav-icon" viewBox="0 0 24 24" aria-hidden="true"><path d={paths[name]} fill="none" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth="1.6" /></svg>;
}

export function AppShell({ page, onNavigate, children, health }: { page: string; onNavigate: (page: string) => void; children: ReactNode; health: { status: string; database: string } | null }) {
  return (
    <div className="shell">
      <a className="skip-link" href="#main-content">Skip to content</a>
      <header className="topbar">
        <div className="topbar-brand"><div className="brand-mark">DS</div><div className="brand-copy"><strong>Decision-SQL</strong><span>Governed query console</span></div><span className="environment-tag">LOCAL</span></div>
        <div className="topbar-title"><span className="eyebrow">OPERATOR CONSOLE</span><h1>{page}</h1></div>
        <nav className="main-nav" aria-label="Primary navigation">{nav.map((item) => <button type="button" className={page === item ? "active" : ""} aria-current={page === item ? "page" : undefined} key={item} onClick={() => onNavigate(item)}><NavIcon name={item} />{item}</button>)}</nav>
        <div className="health"><span className={`health-dot ${health?.status === "ok" ? "ok" : "unknown"}`} />API {health?.status === "ok" ? "healthy" : "checking"}<span className="health-separator" /><span className={`health-dot ${health?.database === "ok" ? "ok" : "unknown"}`} />Database {health?.database === "ok" ? "ready" : "unavailable"}</div>
      </header>
      <main className="main" id="main-content" tabIndex={-1}><div className="content">{children}</div></main>
    </div>
  );
}
