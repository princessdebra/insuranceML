import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import AssessorLayout from "@/layouts/AssessorLayout";
import { getAssessorClaims, getAssessorDashboardOverview, AssessorDashboardOverview } from "@/lib/api";

export default function AssessorDashboard() {
  const navigate = useNavigate();
  const [data, setData] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [overview, setOverview] = useState<AssessorDashboardOverview | null>(null);
  const [overviewLoading, setOverviewLoading] = useState(true);

  useEffect(() => {
    const assessorId = localStorage.getItem("assessorId");
    if (!assessorId) { navigate("/assessor/login"); return; }
    getAssessorClaims(assessorId).then((d) => { setData(d); setLoading(false); }).catch(() => setLoading(false));
    getAssessorDashboardOverview(assessorId)
      .then((d) => { setOverview(d); setOverviewLoading(false); })
      .catch(() => setOverviewLoading(false));
  }, [navigate]);

  // Claims still being analyzed (risk_level === "pending") finish on the
  // backend in the background -- without this, the dashboard would just
  // keep showing "Analyzing..." forever until the assessor manually
  // refreshes the page. Poll while anything is still pending, and stop
  // once every claim has a real result, so this doesn't run forever.
  const claimsForPolling = data?.claims || [];
  const hasPendingClaims = claimsForPolling.some((c: any) => c.risk_level === "pending");

  useEffect(() => {
    if (!hasPendingClaims) return;
    const assessorId = localStorage.getItem("assessorId");
    if (!assessorId) return;

    const interval = setInterval(() => {
      getAssessorClaims(assessorId).then((d) => setData(d)).catch(() => {});
    }, 18000);

    return () => clearInterval(interval);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [hasPendingClaims]);

  if (loading) return <AssessorLayout><div className="flex items-center justify-center h-full"><p className="text-muted-foreground">Loading...</p></div></AssessorLayout>;

  const claims = data?.claims || [];

  const widgets = overview ? [
    { icon: "pending_actions", label: "Pending Inspections", value: overview.pending_inspections, iconColor: "text-amber-500", iconBg: "bg-amber-500/10" },
    { icon: "today", label: "Inspections Scheduled Today", value: overview.inspections_scheduled_today, iconColor: "text-blue-500", iconBg: "bg-blue-500/10" },
    { icon: "edit_note", label: "Reports Awaiting Submission", value: overview.reports_awaiting_submission, iconColor: "text-primary", iconBg: "bg-primary/10" },
    { icon: "undo", label: "Reports Returned for Review", value: overview.reports_returned_for_review, iconColor: "text-destructive", iconBg: "bg-destructive/10" },
    { icon: "task_alt", label: "Completed Assessments", value: overview.completed_assessments, iconColor: "text-emerald-600", iconBg: "bg-emerald-500/10" },
    { icon: "warning", label: "Overdue Assessments", value: overview.overdue_assessments, iconColor: "text-destructive", iconBg: "bg-destructive/10" },
    {
      icon: "schedule", label: "Avg. Assessment Turnaround",
      value: overview.avg_turnaround_hours !== null ? overview.avg_turnaround_hours : "—",
      suffix: overview.avg_turnaround_hours !== null ? "hrs" : "",
      iconColor: "text-muted-foreground", iconBg: "bg-muted",
    },
    {
      icon: "payments", label: "Estimated Claim Value",
      value: `KES ${Math.round(overview.estimated_claim_value_total).toLocaleString()}`,
      iconColor: "text-primary", iconBg: "bg-primary/10",
    },
  ] : [];

  return (
    <AssessorLayout>
      <div className="p-8 max-w-7xl mx-auto w-full">
        <div className="mb-8">
          <h2 className="text-3xl font-bold text-foreground">Welcome back, Assessor</h2>
          <p className="text-muted-foreground mt-1">Here is an overview of your Claims assignments.</p>
        </div>

        {/* Stats */}
        {overviewLoading ? (
          <div className="bg-card p-8 rounded-xl border border-border shadow-sm mb-10 text-center text-muted-foreground animate-pulse">
            Loading assignment overview...
          </div>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-6 mb-6">
            {widgets.map((stat) => (
              <div key={stat.label} className="bg-card p-6 rounded-xl border border-border shadow-sm">
                <div className="flex items-center justify-between mb-4">
                  <span className={`material-symbols-outlined ${stat.iconColor} ${stat.iconBg} p-2 rounded-lg`}>{stat.icon}</span>
                </div>
                <p className="text-sm font-medium text-muted-foreground">{stat.label}</p>
                <p className="text-3xl font-bold mt-1 text-foreground">{stat.value}{stat.suffix && <span className="text-sm font-normal text-muted-foreground ml-1">{stat.suffix}</span>}</p>
              </div>
            ))}
          </div>
        )}

        {/* Claims by Status */}
        {overview && Object.keys(overview.claims_by_status).length > 0 && (
          <div className="bg-card p-6 rounded-xl border border-border shadow-sm mb-10">
            <h4 className="text-sm font-bold text-foreground mb-4">Claims by Status</h4>
            <div className="flex flex-wrap gap-4">
              {Object.entries(overview.claims_by_status).map(([status, count]) => (
                <div key={status} className="flex items-center gap-2 px-4 py-2 bg-muted/30 border rounded-lg">
                  <span className="text-lg font-black text-foreground tabular-nums">{count}</span>
                  <span className="text-[11px] font-bold uppercase text-muted-foreground">{status.replace(/_/g, " ")}</span>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Claims Table */}
        <div className="bg-card rounded-xl border border-border shadow-sm overflow-hidden">
          <div className="px-6 py-5 border-b border-border flex flex-col sm:flex-row sm:items-center justify-between gap-4">
            <div className="flex items-center gap-3">
              <h3 className="text-lg font-bold text-foreground">Active Claim Assignments</h3>
              {hasPendingClaims && (
                <span className="inline-flex items-center gap-1.5 text-[11px] font-semibold text-muted-foreground italic">
                  <span className="size-2.5 border-2 border-muted-foreground/40 border-t-primary rounded-full animate-spin"></span>
                  Auto-refreshing while claims are analyzing...
                </span>
              )}
            </div>
            <div className="flex items-center gap-2">
              <button className="px-4 py-2 border border-border rounded-lg text-sm font-medium flex items-center gap-2 hover:bg-muted transition-colors text-foreground">
                <span className="material-symbols-outlined text-[18px]">filter_list</span>Filter
              </button>
              <button className="px-4 py-2 border border-border rounded-lg text-sm font-medium flex items-center gap-2 hover:bg-muted transition-colors text-foreground">
                <span className="material-symbols-outlined text-[18px]">download</span>Export
              </button>
            </div>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-left border-collapse">
              <thead>
                <tr className="bg-muted">
                  <th className="px-6 py-4 text-xs font-bold text-muted-foreground uppercase tracking-wider">Claim Reference</th>
                  {/* <th className="px-6 py-4 text-xs font-bold text-muted-foreground uppercase tracking-wider">Location</th> */}
                  <th className="px-6 py-4 text-xs font-bold text-muted-foreground uppercase tracking-wider">Est. Cost</th>
                  <th className="px-6 py-4 text-xs font-bold text-muted-foreground uppercase tracking-wider">Status</th>
                  <th className="px-6 py-4 text-xs font-bold text-muted-foreground uppercase tracking-wider text-right">Actions</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border">
                {claims.map((claim: any) => (
                  <tr key={claim.claim_id} className="hover:bg-muted/50 transition-colors">
                    <td className="px-6 py-4 whitespace-nowrap">
                      <div className="flex flex-col">
                        <span className="text-sm font-bold text-primary">{claim.claim_id}</span>
                        <span className="text-xs text-muted-foreground">Assigned: {claim.assigned_at?.split(" ")[0]}</span>
                      </div>
                    </td>
                    {/* <td className="px-6 py-4 whitespace-nowrap">
                      <div className="flex items-center gap-2 text-sm text-foreground">
                        <span className="material-symbols-outlined text-muted-foreground text-[18px]">location_on</span>
                        {claim.location || "N/A"}
                      </div>
                    </td> */}
                    <td className="px-6 py-4 whitespace-nowrap">
                      {/* risk_level stays the literal string "pending" (set at claim
                          creation, before analyze_multiparty_claim's background pipeline
                          finishes and overwrites it with the real cost/score) -- reuse
                          that same sentinel here instead of showing a misleading KES 0. */}
                      {claim.risk_level === "pending" ? (
                        <span className="inline-flex items-center gap-1.5 text-xs font-semibold text-muted-foreground italic">
                          <span className="size-3 border-2 border-muted-foreground/40 border-t-primary rounded-full animate-spin"></span>
                          Analyzing...
                        </span>
                      ) : (
                        <span className="text-sm font-semibold text-foreground">KES {Number(claim.estimated_cost).toLocaleString()}</span>
                      )}
                    </td>
                    <td className="px-6 py-4 whitespace-nowrap">
                      <span className="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-bold bg-amber-100 text-amber-800">
                        {claim.assignment_status}
                      </span>
                    </td>
                    <td className="px-6 py-4 whitespace-nowrap text-right">
                      <div className="flex items-center justify-end gap-2">
                        <Link to={`/assessor/claim/${claim.claim_id}`} className="text-xs font-bold bg-primary text-primary-foreground px-4 py-2 rounded-lg hover:bg-primary/90 transition-all">Inspect</Link>
                        <Link to={`/assessor/claim/${claim.claim_id}`} className="text-xs font-bold text-muted-foreground hover:text-primary transition-all px-2">Details</Link>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="px-6 py-4 bg-muted/50 border-t border-border flex items-center justify-between">
            <p className="text-sm text-muted-foreground">Showing {claims.length} of {claims.length} active assignments</p>
          </div>
        </div>

      </div>
    </AssessorLayout>
  );
}
