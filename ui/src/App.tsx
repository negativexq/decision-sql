import { useEffect, useState } from "react";
import { api } from "./api/client";
import { AppShell } from "./components/AppShell";
import { Playground } from "./pages/Playground";
import { Runs } from "./pages/Runs";
import { Schema } from "./pages/Schema";
import { Traces } from "./pages/Traces";
import type { RunRecord, RunSummary } from "./types";

export default function App() {
  const [page, setPage] = useState("Playground");
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [selected, setSelected] = useState<RunRecord | null>(null);
  const [health, setHealth] = useState<{ status: string; database: string } | null>(null);
  useEffect(() => { void api.runs().then((data) => setRuns(data.runs)).catch(() => undefined); void api.health().then(setHealth).catch(() => setHealth({ status: "degraded", database: "unavailable" })); }, []);
  const onRun = (run: RunRecord) => { setRuns((current) => [{ ...run }, ...current.filter((item) => item.run_id !== run.run_id)]); setSelected(run); };
  const select = (run: RunSummary) => { void api.run(run.run_id).then(setSelected); setPage("Traces"); };
  return <AppShell page={page} onNavigate={setPage} health={health}>{page === "Playground" && <Playground onRun={onRun} />}{page === "Runs" && <Runs runs={runs} onSelect={select} />}{page === "Traces" && <Traces runs={runs} selected={selected} onSelect={select} />}{page === "Schema" && <Schema />}</AppShell>;
}
