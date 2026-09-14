import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { getLiveAssessmentOperations, LiveOperation } from "@/lib/api";

/**
 * "Live Assessment Operations" -- every assessment currently in the system,
 * one row each, with the fields an ops admin actually scans for: vehicle,
 * assessor, status, AI risk, value, age. Distinct from the "All Claims"
 * table below it (which is claim-centric, filterable by decision/verdict) --
 * this one is assignment-centric and reads like a live ops board.
 */
const STATUS_STYLES: Record<string, string> = {
  Inspection: "bg-blue-500/10 text-blue-600",
  "AI Review": "bg-violet-500/10 text-violet-600",
  Overdue: "bg-destructive/10 text-destructive",
  Completed: "bg-emerald-500/10 text-emerald-600",
};

const RISK_STYLES: Record<string, string> = {
  low: "bg-primary/10 text-primary",
  medium: "bg-amber-500/10 text-amber-700",
  high: "bg-destructive/10 text-destructive",
  pending: "bg-muted text-muted-foreground",
  unknown: "bg-muted text-muted-foreground",
};

function ageLabel(assignedAt: string | null): string {
  if (!assignedAt) return "—";
  const then = new Date(assignedAt.replace(" ", "T")).getTime();
  if (Number.isNaN(then)) return "—";
  const diffMs = Date.now() - then;
  const hours = diffMs / 3_600_000;
  if (hours < 1) return `${Math.max(1, Math.round(hours * 60))}m`;
  if (hours < 24) return `${Math.round(hours)}h`;
  return `${Math.round(hours / 24)}d`;
}

export default function LiveOperationsTable() {
  const [operations, setOperations] = useState<LiveOperation[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [statusFilter, setStatusFilter] = useState("");
  const [riskFilter, setRiskFilter] = useState("");
  const [page, setPage] = useState(1);
  const pageSize = 8;

  useEffect(() => {
    setLoading(true);
    getLiveAssessmentOperations({
      limit: pageSize,
      offset: (page - 1) * pageSize,
      status: statusFilter || undefined,
      risk_level: riskFilter || undefined,
    })
      .then((d) => { setOperations(d.operations || []); setTotal(d.total || 0); setLoading(false); })
      .catch(() => setLoading(false));
  }, [page, statusFilter, riskFilter]);

  const totalPages = Math.max(1, Math.ceil(total / pageSize));

  return (
    <div className="bg-card rounded-xl border border-border shadow-sm overflow-hidden mb-10">
      <div className="px-6 py-5 border-b border-border flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <div>
          <h3 className="text-lg font-bold text-foreground flex items-center gap-2">
            <span className="relative flex size-2">
              <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-500 opacity-75"></span>
              <span className="relative inline-flex rounded-full size-2 bg-emerald-500"></span>
            </span>
            Live Assessment Operations
          </h3>
          <p className="text-xs text-muted-foreground mt-1">Every assessment currently in the system, in real time</p>
        </div>
        <div className="flex items-center gap-2 flex-wrap">
          <select
            value={statusFilter}
            onChange={(e) => { setStatusFilter(e.target.value); setPage(1); }}
            className="px-3 py-2 border border-border rounded-lg text-sm bg-background text-foreground focus:ring-2 focus:ring-primary/50 outline-none"
          >
            <option value="">All Statuses</option>
            <option value="Inspection">Inspection</option>
            <option value="AI Review">AI Review</option>
            <option value="Overdue">Overdue</option>
            <option value="Completed">Completed</option>
          </select>
          <select
            value={riskFilter}
            onChange={(e) => { setRiskFilter(e.target.value); setPage(1); }}
            className="px-3 py-2 border border-border rounded-lg text-sm bg-background text-foreground focus:ring-2 focus:ring-primary/50 outline-none"
          >
            <option value="">All AI Risk</option>
            <option value="low">Low</option>
            <option value="medium">Medium</option>
            <option value="high">High</option>
          </select>
        </div>
      </div>

      {loading ? (
        <div className="flex items-center justify-center py-16">
          <p className="text-muted-foreground animate-pulse text-sm">Loading operations...</p>
        </div>
      ) : (
        <>
          <div className="overflow-x-auto">
            <table className="w-full text-left border-collapse">
              <thead>
                <tr className="bg-muted/50">
                  <th className="px-6 py-3 text-[10px] font-black uppercase tracking-wider text-muted-foreground">Claim</th>
                  <th className="px-6 py-3 text-[10px] font-black uppercase tracking-wider text-muted-foreground">Vehicle</th>
                  <th className="px-6 py-3 text-[10px] font-black uppercase tracking-wider text-muted-foreground">Assessor</th>
                  <th className="px-6 py-3 text-[10px] font-black uppercase tracking-wider text-muted-foreground">Status</th>
                  <th className="px-6 py-3 text-[10px] font-black uppercase tracking-wider text-muted-foreground">AI Risk</th>
                  <th className="px-6 py-3 text-[10px] font-black uppercase tracking-wider text-muted-foreground">Value</th>
                  <th className="px-6 py-3 text-[10px] font-black uppercase tracking-wider text-muted-foreground">Age</th>
                  <th className="px-6 py-3"></th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border">
                {operations.map((op) => (
                  <tr key={op.assignment_id} className="hover:bg-muted/30 transition-colors">
                    <td className="px-6 py-3.5 text-sm font-bold text-primary whitespace-nowrap">{op.claim_id}</td>
                    <td className="px-6 py-3.5 text-sm text-foreground whitespace-nowrap">{op.vehicle || <span className="text-muted-foreground italic">Unknown</span>}</td>
                    <td className="px-6 py-3.5 text-sm text-foreground whitespace-nowrap">{op.assessor_name || "Unassigned"}</td>
                    <td className="px-6 py-3.5 whitespace-nowrap">
                      <span className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-[11px] font-bold ${STATUS_STYLES[op.status] || "bg-muted text-muted-foreground"}`}>
                        {op.status}
                      </span>
                    </td>
                    <td className="px-6 py-3.5 whitespace-nowrap">
                      <span className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-[11px] font-bold capitalize ${RISK_STYLES[op.ai_risk] || RISK_STYLES.unknown}`}>
                        {op.ai_risk}
                      </span>
                    </td>
                    <td className="px-6 py-3.5 text-sm font-semibold text-foreground whitespace-nowrap tabular-nums">
                      {op.value ? `KES ${Number(op.value).toLocaleString()}` : "—"}
                    </td>
                    <td className="px-6 py-3.5 text-xs text-muted-foreground whitespace-nowrap tabular-nums">{ageLabel(op.assigned_at)}</td>
                    <td className="px-6 py-3.5 whitespace-nowrap text-right">
                      <Link to={`/admin/claim/${op.claim_id}`} className="text-xs font-bold text-primary hover:underline">View</Link>
                    </td>
                  </tr>
                ))}
                {operations.length === 0 && (
                  <tr>
                    <td colSpan={8} className="px-6 py-12 text-center text-muted-foreground text-sm">No assessments match the selected filters.</td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
          <div className="px-6 py-4 bg-muted/50 border-t border-border flex items-center justify-between">
            <p className="text-sm text-muted-foreground">Page {page} of {totalPages} ({total} assessments)</p>
            <div className="flex gap-2">
              <button disabled={page <= 1} onClick={() => setPage((p) => p - 1)} className="px-3 py-1.5 border border-border rounded-lg text-sm font-medium disabled:opacity-40 hover:bg-muted transition-colors text-foreground">Previous</button>
              <button disabled={page >= totalPages} onClick={() => setPage((p) => p + 1)} className="px-3 py-1.5 border border-border rounded-lg text-sm font-medium disabled:opacity-40 hover:bg-muted transition-colors text-foreground">Next</button>
            </div>
          </div>
        </>
      )}
    </div>
  );
}
