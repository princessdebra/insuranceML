import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import AnalystLayout from "@/layouts/AnalystLayout";
import { getAdminClaims } from "@/lib/api";

type ClaimRow = {
  claim_id: string;
  created_at: string | null;
  estimated_cost: number;
  final_assessment: { decision: string; fraud_risk_score: number; risk_level: string };
  fraud_indicators: {
    photo_anomalies: { type: string; severity: string }[];
    narrative_inconsistencies: { type: string; severity: string }[];
    cross_party_issues: { type?: string; severity?: string }[];
  };
};

const KES = (n: number) => `KES ${Math.round(n || 0).toLocaleString()}`;

function violationLabel(type?: string) {
  if (!type) return "Flag";
  return type.replace(/^ai_detected_/, "").replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

// Every distinct flag type across a claim's photo/narrative/cross-party
// findings -- the same raw signals the Media & Statements tabs already show
// per claim, just surfaced here as a scannable list across the whole book.
function violationsOf(c: ClaimRow): string[] {
  const all = [
    ...c.fraud_indicators.photo_anomalies,
    ...c.fraud_indicators.narrative_inconsistencies,
    ...c.fraud_indicators.cross_party_issues,
  ];
  return Array.from(new Set(all.map((a) => violationLabel(a.type))));
}

function statusBadge(decision: string) {
  const map: Record<string, string> = {
    APPROVE_CLAIM: "bg-emerald-500/10 text-emerald-700",
    INVESTIGATE_FURTHER: "bg-amber-500/10 text-amber-700",
    DECLINE_CLAIM: "bg-destructive/10 text-destructive",
  };
  const label: Record<string, string> = {
    APPROVE_CLAIM: "Approve",
    INVESTIGATE_FURTHER: "Investigate",
    DECLINE_CLAIM: "Decline",
  };
  return { className: map[decision] || "bg-muted text-muted-foreground", label: label[decision] || decision };
}

export default function AnalystAllClaims() {
  const navigate = useNavigate();
  const [claims, setClaims] = useState<ClaimRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [tab, setTab] = useState<"flagged" | "anomalies" | "normal">("flagged");

  useEffect(() => {
    if (!localStorage.getItem("analystId")) {
      navigate("/analyst/login");
      return;
    }
    getAdminClaims({ limit: 200 })
      .then((d) => setClaims(d.claims || []))
      .finally(() => setLoading(false));
  }, [navigate]);

  // Flagged: the AI's own escalation call (matches what the assessor/admin
  // decision engine already flags -- not a second, invented threshold).
  // Anomalies: claims with real photo/narrative/cross-party findings that
  // didn't rise to a full escalation. Normal: everything else -- clean on
  // every signal we have.
  const { flagged, anomalies, normal } = useMemo(() => {
    const flagged: ClaimRow[] = [], anomalies: ClaimRow[] = [], normal: ClaimRow[] = [];
    for (const c of claims) {
      if (c.final_assessment.decision !== "APPROVE_CLAIM") flagged.push(c);
      else if (violationsOf(c).length > 0) anomalies.push(c);
      else normal.push(c);
    }
    return { flagged, anomalies, normal };
  }, [claims]);

  const current = tab === "flagged" ? flagged : tab === "anomalies" ? anomalies : normal;

  const totalFlaggedValue = flagged.reduce((s, c) => s + (c.estimated_cost || 0), 0);
  const violationCounts: Record<string, number> = {};
  for (const c of flagged) for (const v of violationsOf(c)) violationCounts[v] = (violationCounts[v] || 0) + 1;
  const topViolation = Object.entries(violationCounts).sort((a, b) => b[1] - a[1])[0]?.[0] || "None detected";

  return (
    <AnalystLayout>
      <div className="p-8 max-w-6xl mx-auto w-full">
        <div className="mb-6">
          <h2 className="text-3xl font-black text-foreground">All Claims</h2>
          <p className="text-muted-foreground mt-1 text-sm">Every claim in the system, grouped by how much attention it needs.</p>
        </div>

        {/* Tabs */}
        <div className="flex items-center gap-2 mb-6 border-b border-border">
          {([
            { key: "flagged", label: "Flagged", count: flagged.length, icon: "flag" },
            { key: "anomalies", label: "Anomalies", count: anomalies.length, icon: "warning" },
            { key: "normal", label: "Normal Claims", count: normal.length, icon: "check_circle" },
          ] as const).map((t) => (
            <button
              key={t.key}
              onClick={() => setTab(t.key)}
              className={`flex items-center gap-2 px-4 py-3 text-sm font-bold border-b-2 -mb-px transition-colors ${
                tab === t.key ? "border-primary text-primary" : "border-transparent text-muted-foreground hover:text-foreground"
              }`}
            >
              <span className="material-symbols-outlined text-[18px]">{t.icon}</span>
              {t.label}
              <span className={`text-[10px] font-black px-1.5 py-0.5 rounded-full ${tab === t.key ? "bg-primary/10 text-primary" : "bg-muted text-muted-foreground"}`}>
                {t.count}
              </span>
            </button>
          ))}
        </div>

        {loading ? (
          <div className="bg-card p-10 rounded-2xl border border-border shadow-sm text-center text-muted-foreground animate-pulse">
            Loading claims...
          </div>
        ) : tab === "flagged" ? (
          <>
            {/* Summary cards -- same shape as the reference Flagged Claims view */}
            <div className="flex flex-col sm:flex-row gap-4 mb-6">
              <div className="flex-1 flex items-center gap-4 bg-card rounded-2xl border border-border shadow-sm px-6 py-5">
                <span className="material-symbols-outlined text-destructive bg-destructive/10 p-3 rounded-xl text-[24px]">payments</span>
                <div>
                  <p className="text-[10px] font-black uppercase tracking-widest text-muted-foreground">Total Flagged Value</p>
                  <p className="text-xl font-black text-foreground">{KES(totalFlaggedValue)}</p>
                </div>
              </div>
              <div className="flex-1 flex items-center gap-4 bg-card rounded-2xl border border-border shadow-sm px-6 py-5">
                <span className="material-symbols-outlined text-amber-600 bg-amber-500/10 p-3 rounded-xl text-[24px]">report_problem</span>
                <div>
                  <p className="text-[10px] font-black uppercase tracking-widest text-muted-foreground">Top Violation Type</p>
                  <p className="text-xl font-black text-foreground">{topViolation}</p>
                </div>
              </div>
            </div>

            <div className="bg-card rounded-2xl border border-border shadow-sm overflow-hidden">
              <div className="px-6 py-4 border-b border-border flex items-center justify-between">
                <h3 className="text-sm font-black uppercase tracking-widest text-foreground">Flagged Claims</h3>
                <span className="text-[10px] font-black uppercase px-2.5 py-1 rounded-full bg-destructive/10 text-destructive">{flagged.length} records</span>
              </div>
              <div className="overflow-x-auto">
                <table className="w-full text-left border-collapse text-sm">
                  <thead>
                    <tr className="bg-muted/40">
                      <th className="px-6 py-3 font-black uppercase text-[10px] text-muted-foreground">Claim Number &amp; Status</th>
                      <th className="px-6 py-3 font-black uppercase text-[10px] text-muted-foreground">Fraud Score</th>
                      <th className="px-6 py-3 font-black uppercase text-[10px] text-muted-foreground">Violation Flags</th>
                      <th className="px-6 py-3 font-black uppercase text-[10px] text-muted-foreground text-right">Billed Amount</th>
                      <th className="px-6 py-3 font-black uppercase text-[10px] text-muted-foreground text-center">Details</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-border">
                    {flagged.map((c) => {
                      const s = statusBadge(c.final_assessment.decision);
                      const flags = violationsOf(c);
                      return (
                        <tr key={c.claim_id} className="hover:bg-muted/20 transition-colors">
                          <td className="px-6 py-4">
                            <p className="font-black text-destructive">{c.claim_id}</p>
                            <span className={`inline-block mt-1 text-[9px] font-black uppercase px-2 py-0.5 rounded-full ${s.className}`}>{s.label}</span>
                          </td>
                          <td className="px-6 py-4 min-w-[160px]">
                            <div className="flex items-center gap-2">
                              <span className="font-black text-foreground tabular-nums">{(c.final_assessment.fraud_risk_score / 100).toFixed(2)}</span>
                              <div className="flex-1 h-1.5 bg-muted rounded-full overflow-hidden max-w-[100px]">
                                <div
                                  className={`h-full rounded-full ${c.final_assessment.fraud_risk_score >= 75 ? "bg-destructive" : c.final_assessment.fraud_risk_score >= 50 ? "bg-amber-500" : "bg-primary"}`}
                                  style={{ width: `${Math.min(100, c.final_assessment.fraud_risk_score)}%` }}
                                />
                              </div>
                            </div>
                            <p className="text-[9px] font-bold uppercase text-muted-foreground mt-0.5">{c.final_assessment.risk_level} risk</p>
                          </td>
                          <td className="px-6 py-4">
                            {flags.length === 0 ? (
                              <span className="text-xs italic text-muted-foreground">None detected</span>
                            ) : (
                              <div className="flex flex-wrap gap-1.5 max-w-[280px]">
                                {flags.slice(0, 2).map((f) => (
                                  <span key={f} className="text-[9px] font-black uppercase px-2 py-1 rounded-md bg-destructive/10 text-destructive">{f}</span>
                                ))}
                                {flags.length > 2 && (
                                  <span className="text-[9px] font-bold uppercase px-2 py-1 rounded-md bg-muted text-muted-foreground">+{flags.length - 2} more</span>
                                )}
                              </div>
                            )}
                          </td>
                          <td className="px-6 py-4 text-right font-bold text-foreground tabular-nums">{KES(c.estimated_cost)}</td>
                          <td className="px-6 py-4 text-center">
                            <button
                              onClick={() => navigate(`/analyst/claim/${c.claim_id}`)}
                              className="inline-flex items-center justify-center size-8 rounded-full border border-border text-muted-foreground hover:text-primary hover:border-primary/40 transition-colors"
                            >
                              <span className="material-symbols-outlined text-[18px]">info</span>
                            </button>
                          </td>
                        </tr>
                      );
                    })}
                    {flagged.length === 0 && (
                      <tr>
                        <td colSpan={5} className="px-6 py-10 text-center text-sm italic text-muted-foreground">No flagged claims right now.</td>
                      </tr>
                    )}
                  </tbody>
                </table>
              </div>
            </div>
          </>
        ) : (
          // Anomalies / Normal Claims -- simpler list, same underlying data
          <div className="bg-card rounded-2xl border border-border shadow-sm overflow-hidden">
            <div className="overflow-x-auto">
              <table className="w-full text-left border-collapse text-sm">
                <thead>
                  <tr className="bg-muted/40">
                    <th className="px-6 py-3 font-black uppercase text-[10px] text-muted-foreground">Claim</th>
                    <th className="px-6 py-3 font-black uppercase text-[10px] text-muted-foreground">Fraud Score</th>
                    {tab === "anomalies" && <th className="px-6 py-3 font-black uppercase text-[10px] text-muted-foreground">Flags</th>}
                    <th className="px-6 py-3 font-black uppercase text-[10px] text-muted-foreground text-right">Billed Amount</th>
                    <th className="px-6 py-3 font-black uppercase text-[10px] text-muted-foreground text-center">Details</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-border">
                  {current.map((c) => (
                    <tr key={c.claim_id} className="hover:bg-muted/20 transition-colors">
                      <td className="px-6 py-4 font-bold text-foreground">{c.claim_id}</td>
                      <td className="px-6 py-4 tabular-nums text-muted-foreground">{c.final_assessment.fraud_risk_score}/100</td>
                      {tab === "anomalies" && (
                        <td className="px-6 py-4">
                          <div className="flex flex-wrap gap-1.5 max-w-[280px]">
                            {violationsOf(c).map((f) => (
                              <span key={f} className="text-[9px] font-black uppercase px-2 py-1 rounded-md bg-amber-500/10 text-amber-700">{f}</span>
                            ))}
                          </div>
                        </td>
                      )}
                      <td className="px-6 py-4 text-right font-bold text-foreground tabular-nums">{KES(c.estimated_cost)}</td>
                      <td className="px-6 py-4 text-center">
                        <button
                          onClick={() => navigate(`/analyst/claim/${c.claim_id}`)}
                          className="inline-flex items-center justify-center size-8 rounded-full border border-border text-muted-foreground hover:text-primary hover:border-primary/40 transition-colors"
                        >
                          <span className="material-symbols-outlined text-[18px]">info</span>
                        </button>
                      </td>
                    </tr>
                  ))}
                  {current.length === 0 && (
                    <tr>
                      <td colSpan={tab === "anomalies" ? 4 : 3} className="px-6 py-10 text-center text-sm italic text-muted-foreground">
                        No claims in this category.
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          </div>
        )}
      </div>
    </AnalystLayout>
  );
}
