import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import AdminLayout from "@/layouts/AdminLayout";
import { getAdminClaims, getAdminAnalyticsOverview, AdminAnalyticsOverview } from "@/lib/api";
import OversightPanel from "@/components/OversightPanel";
import LiveOperationsTable from "@/components/LiveOperationsTable";

export default function AdminDashboard() {
  const navigate = useNavigate();
  const [data, setData] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [page, setPage] = useState(1);
  const [riskFilter, setRiskFilter] = useState("");
  const [decisionFilter, setDecisionFilter] = useState("");
  const [verdictFilter, setVerdictFilter] = useState("");
  const [sortBy, setSortBy] = useState("created_at");
  const [sortOrder, setSortOrder] = useState("desc");
  const pageSize = 20;

  const [overview, setOverview] = useState<AdminAnalyticsOverview | null>(null);
  const [overviewLoading, setOverviewLoading] = useState(true);

  useEffect(() => {
    const adminId = localStorage.getItem("adminId");
    if (!adminId) {
      navigate("/admin/login");
      return;
    }
  }, [navigate]);

  useEffect(() => {
    getAdminAnalyticsOverview(30)
      .then((d) => {
        setOverview(d);
        setOverviewLoading(false);
      })
      .catch(() => setOverviewLoading(false));
  }, []);

  useEffect(() => {
    setLoading(true);
    const offset = (page - 1) * pageSize;
    getAdminClaims({
      limit: pageSize,
      offset,
      risk_level: riskFilter || undefined,
      decision: decisionFilter || undefined,
      verdict: verdictFilter || undefined,
    })
      .then((d) => {
        setData(d);
        setLoading(false);
      })
      .catch(() => setLoading(false));
  }, [page, riskFilter, decisionFilter, verdictFilter]);

  const claims = data?.claims || [];
  const totalClaims = data?.total || 0;
  const limit = data?.limit || pageSize;
  const offset = data?.offset || 0;

  const totalPages = Math.ceil(totalClaims / limit) || 1;
  const currentPage = Math.floor(offset / limit) + 1;
  const hasNext = offset + limit < totalClaims;
  const hasPrevious = offset > 0;

  // Since sorting isn't supported directly by the new endpoint, client-side sorting handles the custom sorting dynamically.
  const sortedClaims = [...claims].sort((a: any, b: any) => {
    let valA: any = null;
    let valB: any = null;

    if (sortBy === "created_at") {
      valA = new Date(a.created_at || 0).getTime();
      valB = new Date(b.created_at || 0).getTime();
    } else if (sortBy === "fraud_risk_score") {
      valA = a.final_assessment?.fraud_risk_score ?? a.fraud_risk_score ?? 0;
      valB = b.final_assessment?.fraud_risk_score ?? b.fraud_risk_score ?? 0;
    } else if (sortBy === "estimated_cost") {
      valA = a.estimated_cost || a.cost_comparison?.member_estimate || 0;
      valB = b.estimated_cost || b.cost_comparison?.member_estimate || 0;
    }

    if (valA < valB) return sortOrder === "asc" ? -1 : 1;
    if (valA > valB) return sortOrder === "asc" ? 1 : -1;
    return 0;
  });

  const getRiskColor = (level: string) => {
    switch (level?.toLowerCase()) {
      case "high":
      case "critical":
        return "bg-destructive/10 text-destructive";
      case "medium":
        return "bg-amber-100 text-amber-800";
      case "low":
        return "bg-primary/10 text-primary";
      default:
        return "bg-muted text-muted-foreground";
    }
  };

  const getDecisionIcon = (decision: string) => {
    switch (decision) {
      case "APPROVE":
      case "APPROVE_CLAIM":
        return"";
      case "INVESTIGATE":
      case "INVESTIGATE_FURTHER":
        return"";
      case "REJECT":
      case "DECLINE_CLAIM":
        return"";
      default:
        return"";
    }
  };

  const adminId = localStorage.getItem("adminId") || "Admin";

  return (
    <AdminLayout>
      <div className="p-8 max-w-7xl mx-auto w-full">
        <div className="mb-8 flex flex-col sm:flex-row sm:items-center justify-between gap-4 rounded-3xl bg-gradient-to-br from-primary/10 via-primary/5 to-transparent border border-primary/10 px-7 py-6">
          <div>
            <h2 className="text-3xl font-black text-foreground">Welcome back, <span className="text-primary">{adminId}</span></h2>
            <p className="text-muted-foreground mt-1 text-sm">Here's what's happening across your assessment network.</p>
          </div>
          <div className="flex items-center gap-2 bg-card border border-border rounded-full px-4 py-2 shadow-sm shrink-0">
            <span className="material-symbols-outlined text-[18px] text-primary">calendar_today</span>
            <span className="text-sm font-semibold text-foreground">
              {new Date().toLocaleDateString("en-GB", { day: "2-digit", month: "short", year: "numeric" })}
            </span>
          </div>
        </div>

        <OversightPanel data={overview} loading={overviewLoading} />

        <LiveOperationsTable />

        {/* Filters */}
        <div className="bg-card rounded-3xl border border-border shadow-sm overflow-hidden">
          <div className="px-6 py-5 border-b border-border flex flex-col sm:flex-row sm:items-center justify-between gap-4">
            <h3 className="text-lg font-black text-foreground flex items-center gap-2">
              <span className="material-symbols-outlined text-primary bg-primary/10 p-1.5 rounded-lg text-[20px]">folder_open</span>
              All Claims
            </h3>
            <div className="flex items-center gap-2 flex-wrap">
              <select
                value={riskFilter}
                onChange={(e) => { setRiskFilter(e.target.value); setPage(1); }}
                className="px-3 py-2 border border-border rounded-full text-sm bg-background text-foreground focus:ring-2 focus:ring-primary/50 outline-none"
              >
                <option value="">All Risks</option>
                <option value="low">Low Risk</option>
                <option value="medium">Medium Risk</option>
                <option value="high">High Risk</option>
              </select>

              <select
                value={decisionFilter}
                onChange={(e) => { setDecisionFilter(e.target.value); setPage(1); }}
                className="px-3 py-2 border border-border rounded-full text-sm bg-background text-foreground focus:ring-2 focus:ring-primary/50 outline-none"
              >
                <option value="">All Decisions</option>
                <option value="APPROVE_CLAIM">Approve</option>
                <option value="INVESTIGATE_FURTHER">Investigate Further</option>
                <option value="DECLINE_CLAIM">Decline</option>
              </select>

              <select
                value={verdictFilter}
                onChange={(e) => { setVerdictFilter(e.target.value); setPage(1); }}
                className="px-3 py-2 border border-border rounded-full text-sm bg-background text-foreground focus:ring-2 focus:ring-primary/50 outline-none"
              >
                <option value="">All Verdicts</option>
                <option value="CONSISTENT">Consistent</option>
                <option value="SUSPICIOUS">Suspicious</option>
                <option value="INCONSISTENT">Inconsistent</option>
              </select>

              <select
                value={sortBy}
                onChange={(e) => setSortBy(e.target.value)}
                className="px-3 py-2 border border-border rounded-full text-sm bg-background text-foreground focus:ring-2 focus:ring-primary/50 outline-none"
              >
                <option value="created_at">Date</option>
                <option value="fraud_risk_score">Risk Score</option>
                <option value="estimated_cost">Cost</option>
              </select>
              <button
                onClick={() => setSortOrder(sortOrder === "desc" ? "asc" : "desc")}
                className="px-3 py-2 border border-border rounded-full text-sm font-medium flex items-center gap-1 hover:bg-muted transition-colors text-foreground"
              >
                <span className="material-symbols-outlined text-[18px]">{sortOrder === "desc" ? "arrow_downward" : "arrow_upward"}</span>
                {sortOrder === "desc" ? "Newest" : "Oldest"}
              </button>
            </div>
          </div>

          {loading ? (
            <div className="flex items-center justify-center py-20">
              <p className="text-muted-foreground animate-pulse">Loading claims...</p>
            </div>
          ) : (
            <>
              <div className="p-4 space-y-3">
                {sortedClaims.map((claim: any) => {
                  const riskLevel = claim.final_assessment?.risk_level || claim.risk_level;
                  const fraudScore = claim.final_assessment?.fraud_risk_score ?? claim.fraud_risk_score ?? 0;
                  const decision = claim.final_assessment?.decision || claim.decision;
                  const estCost = claim.estimated_cost || claim.cost_comparison?.member_estimate || 0;
                  const location = claim.location || claim.member_submission?.location || "N/A";

                  return (
                    <div key={claim.claim_id} className="px-5 py-4 rounded-2xl border border-border bg-background hover:border-primary/30 hover:shadow-sm transition-all flex flex-col sm:flex-row sm:items-center gap-4">
                      {/* Left: Key info */}
                      <div className="flex-1 grid grid-cols-2 sm:grid-cols-4 gap-3">
                        <div>
                          <p className="text-xs text-muted-foreground">Claim ID</p>
                          <p className="text-sm font-bold text-primary">{claim.claim_id}</p>
                        </div>
                        <div>
                          <p className="text-xs text-muted-foreground">Location</p>
                          <p className="text-sm text-foreground">{location}</p>
                        </div>
                        <div>
                          <p className="text-xs text-muted-foreground">Est. Cost</p>
                          <p className="text-sm font-semibold text-foreground">KES {Number(estCost).toLocaleString()}</p>
                        </div>
                        <div>
                          <p className="text-xs text-muted-foreground">Date</p>
                          <p className="text-sm text-foreground">{claim.created_at?.split(" ")[0] || "N/A"}</p>
                        </div>
                      </div>

                      {/* Middle: Risk + Decision + Parties */}
                      <div className="flex items-center gap-4 flex-shrink-0">
                        <div className="flex items-center gap-2">
                          <div className="h-1.5 w-12 bg-muted rounded-full overflow-hidden">
                            <div
                              className={`h-full rounded-full ${fraudScore >= 70 ? "bg-destructive" : fraudScore >= 50 ? "bg-amber-500" : "bg-primary"}`}
                              style={{ width: `${fraudScore}%` }}
                            ></div>
                          </div>
                          <span className={`inline-flex items-center px-2 py-0.5 rounded-full text-xs font-bold ${getRiskColor(riskLevel)}`}>
                            {fraudScore}
                          </span>
                        </div>
                        <span className="text-sm whitespace-nowrap">{getDecisionIcon(decision)} {decision?.replace(/_/g, " ")}</span>
                        <div className="flex gap-1">
                          <span className={`size-5 rounded-full text-[10px] flex items-center justify-center font-bold ${claim.parties_complete?.member || claim.parties_analyzed?.member ? "bg-primary/20 text-primary" : "bg-muted text-muted-foreground"}`}>M</span>
                          <span className={`size-5 rounded-full text-[10px] flex items-center justify-center font-bold ${claim.parties_complete?.assessor || claim.parties_analyzed?.assessor ? "bg-primary/20 text-primary" : "bg-muted text-muted-foreground"}`}>A</span>
                          <span className={`size-5 rounded-full text-[10px] flex items-center justify-center font-bold ${claim.parties_complete?.repair_shop || claim.parties_analyzed?.repair_shop ? "bg-primary/20 text-primary" : "bg-muted text-muted-foreground"}`}>R</span>
                        </div>
                      </div>

                      {/* Right: Action */}
                      <Link
                        to={`/admin/claim/${claim.claim_id}`}
                        className="text-xs font-bold bg-primary text-primary-foreground px-4 py-2 rounded-full hover:bg-primary/90 transition-all text-center flex-shrink-0"
                      >
                        View Report
                      </Link>
                    </div>
                  );
                })}

                {sortedClaims.length === 0 && (
                  <div className="px-6 py-12 text-center text-muted-foreground">
                    No claims match the selected criteria.
                  </div>
                )}
              </div>

              {/* Pagination */}
              <div className="px-6 py-4 bg-muted/50 border-t border-border flex items-center justify-between">
                <p className="text-sm text-muted-foreground">
                  Page {currentPage} of {totalPages} ({totalClaims} claims)
                </p>
                <div className="flex gap-2">
                  <button
                    disabled={!hasPrevious}
                    onClick={() => setPage(page - 1)}
                    className="px-3 py-1.5 border border-border rounded-full text-sm font-medium disabled:opacity-40 hover:bg-muted transition-colors text-foreground"
                  >
                    Previous
                  </button>
                  <button
                    disabled={!hasNext}
                    onClick={() => setPage(page + 1)}
                    className="px-3 py-1.5 border border-border rounded-full text-sm font-medium disabled:opacity-40 hover:bg-muted transition-colors text-foreground"
                  >
                    Next
                  </button>
                </div>
              </div>
            </>
          )}
        </div>
      </div>
    </AdminLayout>
  );
}