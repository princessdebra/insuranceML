import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import AssessorLayout from "@/layouts/AssessorLayout";
import { getAssessorClaims } from "@/lib/api";

export default function AssessorDashboard() {
  const navigate = useNavigate();
  const [data, setData] = useState<any>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const assessorId = localStorage.getItem("assessorId");
    if (!assessorId) { navigate("/assessor/login"); return; }
    getAssessorClaims(assessorId).then((d) => { setData(d); setLoading(false); }).catch(() => setLoading(false));
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
  const pendingCount = claims.filter((c: any) => c.assignment_status === "pending").length;
  const avgRisk = claims.length ? Math.round(claims.reduce((s: number, c: any) => s + (c.fraud_risk_score || 0), 0) / claims.length) : 0;

  return (
    <AssessorLayout>
      <div className="p-8 max-w-7xl mx-auto w-full">
        <div className="mb-8">
          <h2 className="text-3xl font-bold text-foreground">Welcome back, Assessor</h2>
          <p className="text-muted-foreground mt-1">Here is an overview of your Claims assignments.</p>
        </div>

        {/* Stats */}
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-6 mb-10">
          {[
            { icon: "assignment_turned_in", label: "Total Assigned Claims", value: String(claims.length).padStart(2, "0"), iconColor: "text-primary", iconBg: "bg-primary/10", tag: "Overall" },
            { icon: "pending_actions", label: "Pending Inspections", value: String(pendingCount).padStart(2, "0"), iconColor: "text-amber-500", iconBg: "bg-amber-500/10", tag: "Urgent" },
            { icon: "analytics", label: "Average Risk Score", value: `${avgRisk}`, iconColor: "text-blue-500", iconBg: "bg-blue-500/10", tag: "AI Insight", suffix: "/100" },
            { icon: "calendar_today", label: "Completed Today", value: "00", iconColor: "text-muted-foreground", iconBg: "bg-muted", tag: "" },
          ].map((stat) => (
            <div key={stat.label} className="bg-card p-6 rounded-xl border border-border shadow-sm">
              <div className="flex items-center justify-between mb-4">
                <span className={`material-symbols-outlined ${stat.iconColor} ${stat.iconBg} p-2 rounded-lg`}>{stat.icon}</span>
                {stat.tag && <span className={`text-xs font-bold uppercase tracking-wider ${stat.tag === "Urgent" ? "text-amber-500" : "text-muted-foreground"}`}>{stat.tag}</span>}
              </div>
              <p className="text-sm font-medium text-muted-foreground">{stat.label}</p>
              <p className="text-3xl font-bold mt-1 text-foreground">{stat.value}{stat.suffix && <span className="text-sm font-normal text-muted-foreground ml-1">{stat.suffix}</span>}</p>
            </div>
          ))}
        </div>

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
                  <th className="px-6 py-4 text-xs font-bold text-muted-foreground uppercase tracking-wider">Risk Score</th>
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
                      {claim.risk_level === "pending" ? (
                        <span className="text-sm text-muted-foreground italic">pending</span>
                      ) : (
                        <div className="flex items-center gap-2">
                          <div className="flex-1 h-1.5 w-16 bg-muted rounded-full overflow-hidden">
                            <div className="h-full bg-primary rounded-full" style={{ width: `${claim.fraud_risk_score}%` }}></div>
                          </div>
                          <span className="text-sm font-medium text-primary">{claim.risk_level} ({claim.fraud_risk_score})</span>
                        </div>
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

        {/* AI Insights */}
        <div className="mt-8 grid grid-cols-1 lg:grid-cols-3 gap-8">
          <div className="lg:col-span-2 bg-gradient-to-br from-primary to-emerald-800 p-6 rounded-xl text-primary-foreground shadow-lg flex items-center justify-between">
            <div>
              <h4 className="text-lg font-bold mb-2">Automated Claims Assist</h4>
              <p className="text-emerald-50 text-sm max-w-md">Our AI monitors all claims for anomalies and provides real-time risk assessments.</p>
              <button className="mt-4 px-4 py-2 bg-card text-primary rounded-lg text-sm font-bold shadow-sm hover:bg-background transition-colors">
                View AI Analysis
              </button>
            </div>
            <span className="material-symbols-outlined text-[64px] opacity-20 hidden sm:block">psychology</span>
          </div>
          <div className="bg-card p-6 rounded-xl border border-border shadow-sm">
            <h4 className="font-bold mb-4 flex items-center gap-2 text-foreground">
              <span className="material-symbols-outlined text-primary text-[20px]">event_note</span>
              Upcoming Schedule
            </h4>
            <div className="space-y-4">
              <div className="flex gap-3 pb-4 border-b border-border">
                <div className="bg-primary/10 text-primary p-2 rounded flex flex-col items-center justify-center min-w-[48px]">
                  <span className="text-xs font-bold">18</span>
                  <span className="text-[10px] uppercase font-bold">Mar</span>
                </div>
                <div>
                  <p className="text-sm font-bold text-foreground">Workshop Visit</p>
                  <p className="text-xs text-muted-foreground">AutoExpress Thika Rd</p>
                </div>
              </div>
              <p className="text-xs text-center text-muted-foreground italic">No other inspections scheduled.</p>
            </div>
          </div>
        </div>
      </div>
    </AssessorLayout>
  );
}
