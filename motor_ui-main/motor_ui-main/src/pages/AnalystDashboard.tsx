import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import AnalystLayout from "@/layouts/AnalystLayout";
import { getAnalystClaims, listAllClaims, ClaimListEntry } from "@/lib/api";

export default function AnalystDashboard() {
  const navigate = useNavigate();
  const [data, setData] = useState<any>(null);
  const [loading, setLoading] = useState(true);

  // Every claim in the system, not just ones this analyst personally filed
  // -- a colleague picking up a follow-up call about someone else's claim
  // has no other way to find it, since the table below only lists this
  // analyst's own filings. A dropdown instead of typing an exact claim ID.
  const [allClaims, setAllClaims] = useState<ClaimListEntry[]>([]);
  const [selectedClaimId, setSelectedClaimId] = useState("");

  useEffect(() => {
    const analystId = localStorage.getItem("analystId");
    if (!analystId) { navigate("/analyst/login"); return; }
    getAnalystClaims(analystId).then((d) => { setData(d); setLoading(false); }).catch(() => setLoading(false));
    listAllClaims().then((d) => setAllClaims(d.claims || [])).catch(() => {});
  }, [navigate]);

  // Same "auto-refresh while anything is still analyzing" pattern used on
  // the assessor dashboard -- a freshly filed claim's risk_level stays
  // "pending" until the background AI pipeline finishes, which can take
  // minutes; without this an analyst would have to manually refresh to see
  // it resolve.
  const claimsForPolling = data?.claims || [];
  const hasPendingClaims = claimsForPolling.some((c: any) => c.risk_level === "pending");

  useEffect(() => {
    if (!hasPendingClaims) return;
    const analystId = localStorage.getItem("analystId");
    if (!analystId) return;
    const interval = setInterval(() => {
      getAnalystClaims(analystId).then((d) => setData(d)).catch(() => {});
    }, 18000);
    return () => clearInterval(interval);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [hasPendingClaims]);

  if (loading) {
    return (
      <AnalystLayout>
        <div className="flex items-center justify-center h-full">
          <div className="flex flex-col items-center gap-3">
            <div className="size-8 border-4 border-primary border-t-transparent rounded-full animate-spin"></div>
            <p className="text-muted-foreground text-sm">Loading your desk...</p>
          </div>
        </div>
      </AnalystLayout>
    );
  }

  const claims = data?.claims || [];
  const pendingCount = claims.filter((c: any) => c.risk_level === "pending").length;
  const filedToday = claims.filter((c: any) => c.created_at?.split(" ")[0] === new Date().toISOString().split("T")[0]).length;
  const avgRisk = claims.length
    ? Math.round(claims.filter((c: any) => c.risk_level !== "pending").reduce((s: number, c: any) => s + (c.fraud_risk_score || 0), 0) / Math.max(1, claims.filter((c: any) => c.risk_level !== "pending").length))
    : 0;

  const getRiskBadgeStyles = (level: string) => {
    switch (level) {
      case "high": return "bg-destructive/10 text-destructive border-destructive/20";
      case "medium": return "bg-amber-100 text-amber-800 border-amber-200";
      case "low": return "bg-emerald-100 text-emerald-800 border-emerald-200";
      default: return "bg-muted text-muted-foreground border-border";
    }
  };

  return (
    <AnalystLayout>
      <div className="p-8 max-w-7xl mx-auto w-full">
        {/* Hero CTA */}
        <div className="relative overflow-hidden rounded-3xl bg-gradient-to-br from-primary via-emerald-700 to-emerald-900 p-8 md:p-10 mb-8 shadow-lg">
          <div className="absolute inset-0 opacity-[0.06] pointer-events-none">
            <div className="absolute inset-0" style={{ backgroundImage: "radial-gradient(circle at 2px 2px, #ffffff 1px, transparent 0)", backgroundSize: "32px 32px" }} />
          </div>
          <span className="material-symbols-outlined absolute -right-4 -bottom-8 text-primary-foreground/10 text-[220px] leading-none pointer-events-none">support_agent</span>
          <div className="relative z-10 flex flex-col md:flex-row md:items-center justify-between gap-6">
            <div>
              <span className="inline-flex items-center gap-1.5 px-3 py-1 rounded-full bg-white/15 text-primary-foreground text-[11px] font-bold uppercase tracking-widest mb-3">
                <span className="material-symbols-outlined text-sm">call</span>
                Phone-In Claims Desk
              </span>
              <h2 className="text-2xl md:text-3xl font-black text-primary-foreground mb-2">Welcome back, {localStorage.getItem("analystName") || "Analyst"}</h2>
              <p className="text-white/80 text-sm max-w-lg">Take a claim over the phone and file it on the member's behalf — same AI-powered intake as the self-service app.</p>
            </div>
            <Link
              to="/analyst/file-claim"
              className="flex items-center justify-center gap-2 px-6 py-4 bg-white text-primary rounded-xl text-sm font-black shadow-xl hover:scale-105 transition-transform whitespace-nowrap"
            >
              <span className="material-symbols-outlined">add_call</span>
              File New Claim
            </Link>
          </div>
        </div>

        {/* Jump to any claim -- not just ones this analyst filed */}
        <div className="bg-card p-6 rounded-2xl border border-border shadow-sm mb-8">
          <p className="text-sm font-bold text-foreground mb-1">Jump to Any Claim</p>
          <p className="text-xs text-muted-foreground mb-4">
            Picking up a follow-up call about a claim a colleague filed? Pick it from the full list below.
          </p>
          <div className="flex gap-2 max-w-xl flex-wrap">
            <select
              value={selectedClaimId}
              onChange={(e) => setSelectedClaimId(e.target.value)}
              className="flex-1 min-w-[280px] h-11 px-4 rounded-lg border border-border bg-background text-sm focus:border-primary focus:ring-1 focus:ring-primary outline-none"
            >
              <option value="">
                {allClaims.length === 0 ? "Loading claims..." : `Select a claim (${allClaims.length})`}
              </option>
              {allClaims.map((c) => (
                <option key={c.claim_id} value={c.claim_id}>
                  {c.claim_id} — {c.member_name || c.member_id || "Unknown member"}
                  {c.location ? ` — ${c.location}` : ""}
                </option>
              ))}
            </select>
            <Link
              to={selectedClaimId ? `/analyst/claim/${selectedClaimId}` : "#"}
              aria-disabled={!selectedClaimId}
              onClick={(e) => { if (!selectedClaimId) e.preventDefault(); }}
              className={`inline-flex items-center gap-1.5 px-4 py-2 rounded-lg text-xs font-bold transition-colors ${
                selectedClaimId ? "bg-primary text-primary-foreground hover:bg-primary/90" : "bg-muted text-muted-foreground cursor-not-allowed"
              }`}
            >
              <span className="material-symbols-outlined text-[16px]">gavel</span>
              View &amp; Triage
            </Link>
            <Link
              to={selectedClaimId ? `/analyst/claim/${selectedClaimId}/photos` : "#"}
              aria-disabled={!selectedClaimId}
              onClick={(e) => { if (!selectedClaimId) e.preventDefault(); }}
              className={`inline-flex items-center gap-1.5 px-4 py-2 rounded-lg text-xs font-bold border transition-colors ${
                selectedClaimId ? "border-primary/30 text-primary hover:bg-primary/5" : "border-border text-muted-foreground cursor-not-allowed"
              }`}
            >
              <span className="material-symbols-outlined text-[16px]">add_a_photo</span>
              Add Photos
            </Link>
          </div>
        </div>

        {/* Stats */}
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-6 mb-10">
          {[
            { icon: "folder_open", label: "Claims Filed", value: String(claims.length).padStart(2, "0"), iconColor: "text-primary", iconBg: "bg-primary/10", tag: "Overall" },
            { icon: "hourglass_top", label: "Still Analyzing", value: String(pendingCount).padStart(2, "0"), iconColor: "text-amber-500", iconBg: "bg-amber-500/10", tag: pendingCount > 0 ? "In Progress" : "" },
            { icon: "today", label: "Filed Today", value: String(filedToday).padStart(2, "0"), iconColor: "text-blue-500", iconBg: "bg-blue-500/10", tag: "" },
            { icon: "analytics", label: "Average Risk Score", value: `${avgRisk}`, iconColor: "text-violet-500", iconBg: "bg-violet-500/10", tag: "AI Insight", suffix: "/100" },
          ].map((stat) => (
            <div key={stat.label} className="bg-card p-6 rounded-2xl border border-border shadow-sm hover:shadow-md transition-shadow">
              <div className="flex items-center justify-between mb-4">
                <span className={`material-symbols-outlined ${stat.iconColor} ${stat.iconBg} p-2 rounded-lg`}>{stat.icon}</span>
                {stat.tag && <span className="text-xs font-bold uppercase tracking-wider text-muted-foreground">{stat.tag}</span>}
              </div>
              <p className="text-sm font-medium text-muted-foreground">{stat.label}</p>
              <p className="text-3xl font-bold mt-1 text-foreground">{stat.value}{stat.suffix && <span className="text-sm font-normal text-muted-foreground ml-1">{stat.suffix}</span>}</p>
            </div>
          ))}
        </div>

        {/* Claims Table */}
        <div className="bg-card rounded-2xl border border-border shadow-sm overflow-hidden">
          <div className="px-6 py-5 border-b border-border flex flex-col sm:flex-row sm:items-center justify-between gap-4">
            <div className="flex items-center gap-3">
              <h3 className="text-lg font-bold text-foreground">Claims You've Filed</h3>
              {hasPendingClaims && (
                <span className="inline-flex items-center gap-1.5 text-[11px] font-semibold text-muted-foreground italic">
                  <span className="size-2.5 border-2 border-muted-foreground/40 border-t-primary rounded-full animate-spin"></span>
                  Auto-refreshing while claims are analyzing...
                </span>
              )}
            </div>
          </div>

          {claims.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-20 px-6 text-center">
              <div className="size-16 rounded-2xl bg-primary/10 flex items-center justify-center mb-4">
                <span className="material-symbols-outlined text-primary text-3xl">call_missed</span>
              </div>
              <h4 className="text-base font-bold text-foreground mb-1">No claims filed yet</h4>
              <p className="text-sm text-muted-foreground max-w-sm mb-6">
                When a member calls in to report an incident, use "File New Claim" to look them up and file it on their behalf.
              </p>
              <Link to="/analyst/file-claim" className="inline-flex items-center gap-2 px-5 py-2.5 bg-primary text-primary-foreground rounded-lg text-sm font-bold hover:bg-primary/90 transition-colors">
                <span className="material-symbols-outlined text-[18px]">add_call</span>
                File Your First Claim
              </Link>
            </div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-left border-collapse">
                <thead>
                  <tr className="bg-muted">
                    <th className="px-6 py-4 text-xs font-bold text-muted-foreground uppercase tracking-wider">Claim</th>
                    <th className="px-6 py-4 text-xs font-bold text-muted-foreground uppercase tracking-wider">Member</th>
                    <th className="px-6 py-4 text-xs font-bold text-muted-foreground uppercase tracking-wider">Est. Cost</th>
                    <th className="px-6 py-4 text-xs font-bold text-muted-foreground uppercase tracking-wider">AI Risk</th>
                    <th className="px-6 py-4 text-xs font-bold text-muted-foreground uppercase tracking-wider text-right">Actions</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-border">
                  {claims.map((claim: any) => (
                    <tr key={claim.claim_id} className="hover:bg-muted/50 transition-colors">
                      <td className="px-6 py-4 whitespace-nowrap">
                        <div className="flex flex-col">
                          <span className="text-sm font-black text-primary">{claim.claim_id}</span>
                          <span className="text-xs text-muted-foreground">Filed: {claim.created_at?.split(" ")[0]}</span>
                        </div>
                      </td>
                      <td className="px-6 py-4 whitespace-nowrap">
                        <div className="flex flex-col">
                          <span className="text-sm font-semibold text-foreground">{claim.member_name || claim.member_id}</span>
                          <span className="text-xs text-muted-foreground">{claim.member_phone || claim.member_id}</span>
                        </div>
                      </td>
                      <td className="px-6 py-4 whitespace-nowrap">
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
                          <span className={`px-2.5 py-1 rounded-full text-xs font-bold border capitalize ${getRiskBadgeStyles(claim.risk_level)}`}>
                            {claim.risk_level} ({claim.fraud_risk_score})
                          </span>
                        )}
                      </td>
                      <td className="px-6 py-4 whitespace-nowrap text-right">
                        <div className="flex items-center justify-end gap-4">
                          <Link
                            to={`/analyst/claim/${claim.claim_id}`}
                            className="inline-flex items-center gap-1 text-xs font-bold text-primary hover:underline"
                          >
                            <span className="material-symbols-outlined text-[16px]">gavel</span>
                            View &amp; Triage
                          </Link>
                          <Link
                            to={`/analyst/claim/${claim.claim_id}/photos`}
                            className="inline-flex items-center gap-1 text-xs font-bold text-primary hover:underline"
                          >
                            <span className="material-symbols-outlined text-[16px]">add_a_photo</span>
                            Add Photos
                          </Link>
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </div>
    </AnalystLayout>
  );
}
