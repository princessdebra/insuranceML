import { useEffect, useState } from "react";
import { useParams, useNavigate, Link } from "react-router-dom";
import AdminLayout from "@/layouts/AdminLayout";
import { getClaimFullReport, getSimulationStatus, getClaimDecision, recordClaimDecision, BASE_URL } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Progress } from "@/components/ui/progress";
import { Button } from "@/components/ui/button";

export default function AdminClaimReport() {
  const { claimId } = useParams();
  const navigate = useNavigate();
  const [report, setReport] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [activeTab, setActiveTab] = useState("overview");

  // Trajectory Simulation States
  const [simulationStatus, setSimulationStatus] = useState<any>(null);
  const [loadingSimulation, setLoadingSimulation] = useState(false);

  // Final triage decision (PAY / DENY / ESCALATE) -- the human reviewer's
  // actual, persisted business call, distinct from the AI's recomputed
  // "Recommended Action" suggestion shown below.
  const [decisionAction, setDecisionAction] = useState<"PAY" | "DENY" | "ESCALATE" | null>(null);
  const [decisionReason, setDecisionReason] = useState("");
  const [payoutAmount, setPayoutAmount] = useState("");
  const [submittingDecision, setSubmittingDecision] = useState(false);
  const [decisionError, setDecisionError] = useState("");

  const submitDecision = async () => {
    if (!decisionAction || !claimId) return;
    if ((decisionAction === "DENY" || decisionAction === "ESCALATE") && !decisionReason.trim()) {
      setDecisionError(`A reason is required to ${decisionAction.toLowerCase()} this claim.`);
      return;
    }
    setSubmittingDecision(true);
    setDecisionError("");
    try {
      const adminId = localStorage.getItem("adminId") || "admin";
      const res = await recordClaimDecision({
        claim_id: claimId,
        decision: decisionAction,
        decided_by: adminId,
        reason: decisionReason.trim() || undefined,
        payout_amount: decisionAction === "PAY" && payoutAmount ? Number(payoutAmount) : undefined,
      });
      if (res.success) {
        const updated = await getClaimDecision(claimId);
        setReport((prev: any) => ({ ...prev, claim_decision: updated.current_decision }));
        setDecisionAction(null);
        setDecisionReason("");
        setPayoutAmount("");
      } else {
        setDecisionError(res.detail || "Could not record decision.");
      }
    } catch (e) {
      setDecisionError("Could not record decision — check your connection.");
    } finally {
      setSubmittingDecision(false);
    }
  };

  useEffect(() => {
    if (!localStorage.getItem("adminId")) {
      navigate("/admin/login");
      return;
    }
    if (claimId) {
      getClaimFullReport(claimId)
        .then((d) => {
          setReport(d);
          setLoading(false);
        })
        .catch(() => setLoading(false));
    }
  }, [claimId, navigate]);

  // Fetch Trajectory simulation video data
  useEffect(() => {
    if (activeTab === "physics" && claimId) {
      setLoadingSimulation(true);
      getSimulationStatus(claimId)
        .then((status) => {
          setSimulationStatus(status);
          setLoadingSimulation(false);
        })
        .catch((err) => {
          console.error("Failed to fetch simulation status", err);
          setLoadingSimulation(false);
        });
    }
  }, [activeTab, claimId]);

  if (loading) {
    return (
      <AdminLayout>
        <div className="flex items-center justify-center h-full min-h-[400px]">
          <div className="flex flex-col items-center gap-2">
            <div className="size-8 border-4 border-primary border-t-transparent rounded-full animate-spin"></div>
            <p className="text-muted-foreground font-medium">Analyzing claims intelligence database...</p>
          </div>
        </div>
      </AdminLayout>
    );
  }

  if (!report) {
    return (
      <AdminLayout>
        <div className="flex items-center justify-center h-full p-8 text-center">
          <div className="max-w-md space-y-4">
            <span className="material-symbols-outlined text-destructive text-6xl">error</span>
            <h2 className="text-2xl font-bold">Report Loading Failed</h2>
            <p className="text-muted-foreground">We were unable to load the complete intelligence report for claim {claimId}.</p>
            <Link to="/admin/dashboard" className="inline-block px-6 py-2 bg-primary text-primary-foreground rounded-lg font-bold">Return to Dashboard</Link>
          </div>
        </div>
      </AdminLayout>
    );
  }

  const fa = report.final_assessment || {};
  const rb = report.risk_breakdown || {};
  const ds = report.detection_summary || {};
  const cp = report.cross_party_verification || {};
  const pr = report.physics_reconstruction || {};
  const warnings = report.critical_warnings || [];
  const photoAnomalies = report.fraud_indicators?.photo_anomalies || [];
  const narrativeIssues = report.fraud_indicators?.narrative_inconsistencies || [];
  const crossPartyIssues = report.fraud_indicators?.cross_party_issues || [];

  const getSeverityColor = (severity: string) => {
    switch (severity?.toLowerCase()) {
      case "critical":
        return "bg-destructive/10 text-destructive border-destructive/20";
      case "high":
      case "major":
        return "bg-amber-100 text-amber-800 border-amber-200";
      case "medium":
      case "moderate":
        return "bg-amber-50 text-amber-700 border-amber-100";
      case "low":
      case "minor":
        return "bg-blue-50 text-blue-700 border-blue-100";
      default:
        return "bg-muted text-muted-foreground border-border";
    }
  };

  const getDecisionColor = (decision: string) => {
    switch (decision?.toUpperCase()) {
      case "APPROVE_CLAIM":
      case "APPROVE":
        return "bg-emerald-500/10 text-emerald-500 border-emerald-500/20";
      case "INVESTIGATE_FURTHER":
      case "INVESTIGATE":
        return "bg-amber-500/10 text-amber-500 border-amber-500/20";
      case "DECLINE_CLAIM":
      case "REJECT":
        return "bg-destructive/10 text-destructive border-destructive/20";
      default:
        return "bg-muted text-muted-foreground border-border";
    }
  };

  const getVerdictColor = (verdict: string) => {
    switch (verdict?.toUpperCase()) {
      case "CONSISTENT":
        return "bg-emerald-500 text-white";
      case "SUSPICIOUS":
        return "bg-amber-500 text-white";
      case "INCONSISTENT":
        return "bg-destructive text-white";
      default:
        return "bg-muted text-muted-foreground";
    }
  };

  const riskBarColor = (score: number) => (score >= 70 ? "bg-destructive" : score >= 50 ? "bg-amber-500" : "bg-primary");

  // Consolidated "why was this flagged" summary -- pulls together signals
  // that are otherwise scattered across four different tabs (fraud
  // indicators, physics, measurement discrepancy, assessor pattern) into
  // one ranked list, so a reviewer doesn't have to click through everything
  // to get the headline reasons.
  const severityRank: Record<string, number> = { critical: 4, high: 3, medium: 2, low: 1 };
  type Reason = { severity: string; message: string; source: string };
  const topReasons: Reason[] = [];

  warnings.forEach((w: any) => {
    topReasons.push({
      severity: (w.severity || "medium").toLowerCase(),
      message: w.message,
      source: w.party ? `${w.type?.replace(/_/g, " ")} — ${w.party}` : w.type?.replace(/_/g, " "),
    });
  });

  (pr.inconsistencies || []).forEach((inc: any) => {
    topReasons.push({
      severity: (inc.severity || "medium").toLowerCase(),
      message: inc.description,
      source: "Physics reconstruction",
    });
  });

  if (pr.has_measurement_discrepancy) {
    (pr.measurement_flags || []).forEach((f: string) => {
      topReasons.push({ severity: "high", message: f, source: "Assessor measurement cross-check" });
    });
  }

  if (report.assessor_track_record?.flagged_rate_pct >= 30) {
    topReasons.push({
      severity: "high",
      message: `Assessor ${report.assessor_track_record.assessor_id} has a ${report.assessor_track_record.flagged_rate_pct}% measurement-discrepancy flag rate across ${report.assessor_track_record.total_assessed_claims} prior claims.`,
      source: "Assessor track record",
    });
  }

  if (cp.inconsistencies_found && crossPartyIssues.length === 0 && cp.duplicate_photos_detected > 0) {
    topReasons.push({
      severity: "high",
      message: `${cp.duplicate_photos_detected} duplicate photo(s) detected across parties.`,
      source: "Cross-party verification",
    });
  }

  const rankedReasons = topReasons
    .sort((a, b) => (severityRank[b.severity] || 0) - (severityRank[a.severity] || 0))
    .slice(0, 5);

  return (
    <AdminLayout>
      <div className="p-8 max-w-7xl mx-auto w-full space-y-8 pb-24">
        {/* Header Section */}
        <div className="flex flex-col md:flex-row md:items-center justify-between gap-6 pb-6 border-b border-border">
          <div>
            <nav className="flex mb-2">
              <ol className="flex items-center space-x-2 text-xs text-muted-foreground">
                <li><Link to="/admin/dashboard" className="hover:text-primary transition-colors">Dashboard</Link></li>
                <li><span className="material-symbols-outlined text-[12px]">chevron_right</span></li>
                <li className="text-primary font-bold">{report.claim_id}</li>
              </ol>
            </nav>
            <div className="flex items-center gap-3">
              <h1 className="text-4xl font-black text-foreground tracking-tighter">Claims Forensic Report</h1>
              <Badge variant="outline" className="font-mono text-xs px-2.5 py-1">
                Timestamp: {new Date(report.analysis_timestamp).toLocaleString()}
              </Badge>
            </div>
            <p className="text-muted-foreground mt-1 flex items-center gap-2">
              <span className="material-symbols-outlined text-sm">shield</span>
              AI Core Security & Fraud Intelligence Division
            </p>
          </div>
          
          <div className="flex items-center gap-4 bg-card border rounded-2xl p-4 shadow-sm">
            <div className="text-right">
              <p className="text-[10px] font-black uppercase text-muted-foreground tracking-widest">Recommended Action</p>
              <p className="text-lg font-black text-foreground">{fa.decision?.replace(/_/g, " ")}</p>
            </div>
            <div className={`px-4 py-2.5 rounded-xl border font-black uppercase tracking-wider text-xs ${getDecisionColor(fa.decision)}`}>
              {fa.risk_level} risk
            </div>
          </div>
        </div>

        {/* Decision Banner Explanation */}
        {fa.decision_reason && (
          <div className={`p-5 rounded-2xl border flex items-start gap-4 ${fa.fraud_risk_score >= 50 ? "bg-destructive/5 border-destructive/20 text-destructive" : "bg-primary/5 border-primary/20 text-primary"}`}>
            <span className="material-symbols-outlined text-3xl shrink-0 mt-0.5">psychology</span>
            <div>
              <p className="text-[10px] font-black uppercase tracking-widest opacity-80">Assessment Logic</p>
              <p className="text-sm font-bold leading-relaxed mt-1 text-foreground">{fa.decision_reason}</p>
            </div>
          </div>
        )}

        {/* Why This Claim Was Flagged -- consolidated top reasons, ranked by
            severity, pulled from across the report so a reviewer gets the
            headline picture without clicking through every tab. */}
        {rankedReasons.length > 0 ? (
          <Card className="overflow-hidden border-none shadow-sm w-full">
            <CardHeader className="bg-destructive/5 border-b border-destructive/10">
              <CardTitle className="text-sm font-black uppercase tracking-widest flex items-center gap-2 text-destructive">
                <span className="material-symbols-outlined">flag</span>
                Why This Claim Was Flagged — Top {rankedReasons.length} Reason{rankedReasons.length > 1 ? "s" : ""}
              </CardTitle>
            </CardHeader>
            <CardContent className="pt-6">
              <div className="space-y-3">
                {rankedReasons.map((r, idx) => (
                  <div key={idx} className={`p-4 rounded-xl border flex gap-3 ${getSeverityColor(r.severity)}`}>
                    <span className="text-sm font-black shrink-0 mt-0.5 opacity-60">#{idx + 1}</span>
                    <div>
                      <div className="flex items-center gap-2 mb-0.5">
                        <span className="text-[9px] font-black uppercase px-1.5 py-0.5 rounded bg-black/10">{r.severity}</span>
                        <span className="text-[9px] font-bold uppercase tracking-wide opacity-70">{r.source}</span>
                      </div>
                      <p className="text-xs font-semibold leading-relaxed">{r.message}</p>
                    </div>
                  </div>
                ))}
              </div>
            </CardContent>
          </Card>
        ) : (
          <div className="p-5 rounded-2xl border bg-emerald-500/5 border-emerald-500/20 text-emerald-700 flex items-center gap-3">
            <span className="material-symbols-outlined text-2xl">verified</span>
            <p className="text-sm font-bold">No specific fraud indicators found for this claim.</p>
          </div>
        )}

        {/* Final Triage Decision -- the human reviewer's actual, persisted
            call. Distinct from `final_assessment.decision` above, which is
            only ever a freshly recomputed AI suggestion and was never
            written to the database before this. */}
        <Card className="overflow-hidden border-none shadow-sm w-full">
          <CardHeader className="bg-foreground/[0.03] border-b border-border">
            <CardTitle className="text-sm font-black uppercase tracking-widest flex items-center gap-2 text-foreground">
              <span className="material-symbols-outlined">gavel</span>
              Final Triage Decision
            </CardTitle>
          </CardHeader>
          <CardContent className="pt-6 space-y-4">
            {report.claim_decision ? (
              <div className={`p-4 rounded-xl border-2 flex items-start gap-3 ${
                report.claim_decision.decision === "PAY" ? "bg-emerald-500/5 border-emerald-500/30" :
                report.claim_decision.decision === "DENY" ? "bg-destructive/5 border-destructive/30" :
                "bg-amber-500/5 border-amber-500/30"
              }`}>
                <span className={`material-symbols-outlined text-2xl ${
                  report.claim_decision.decision === "PAY" ? "text-emerald-600" :
                  report.claim_decision.decision === "DENY" ? "text-destructive" : "text-amber-600"
                }`}>
                  {report.claim_decision.decision === "PAY" ? "paid" : report.claim_decision.decision === "DENY" ? "block" : "priority_high"}
                </span>
                <div className="flex-1">
                  <p className="text-sm font-black uppercase tracking-wide">
                    {report.claim_decision.decision}
                    {report.claim_decision.decision === "PAY" && report.claim_decision.payout_amount
                      ? ` — KES ${Number(report.claim_decision.payout_amount).toLocaleString()}`
                      : ""}
                  </p>
                  <p className="text-xs text-muted-foreground mt-0.5">
                    by {report.claim_decision.decided_by} on {report.claim_decision.decided_at?.split(" ")[0]}
                  </p>
                  {report.claim_decision.reason && (
                    <p className="text-xs text-foreground/80 mt-2 italic">"{report.claim_decision.reason}"</p>
                  )}
                </div>
                <Button size="sm" variant="outline" className="text-xs" onClick={() => setDecisionAction("PAY")}>
                  Re-decide
                </Button>
              </div>
            ) : (
              <p className="text-xs text-muted-foreground">No decision has been recorded for this claim yet.</p>
            )}

            {decisionAction ? (
              <div className="p-4 rounded-xl border border-border bg-muted/20 space-y-3">
                <p className="text-xs font-black uppercase tracking-wide text-foreground">
                  Recording: <span className={
                    decisionAction === "PAY" ? "text-emerald-600" : decisionAction === "DENY" ? "text-destructive" : "text-amber-600"
                  }>{decisionAction}</span>
                </p>
                {decisionAction === "PAY" && (
                  <div>
                    <label className="text-[10px] font-black uppercase text-muted-foreground block mb-1">Payout Amount (KES, optional)</label>
                    <input
                      type="number"
                      className="w-full text-sm border border-border rounded-lg px-3 py-2 bg-card"
                      value={payoutAmount}
                      onChange={(e) => setPayoutAmount(e.target.value)}
                      placeholder="e.g. 250000"
                    />
                  </div>
                )}
                <div>
                  <label className="text-[10px] font-black uppercase text-muted-foreground block mb-1">
                    Reason {decisionAction !== "PAY" ? "(required)" : "(optional)"}
                  </label>
                  <textarea
                    className="w-full text-sm border border-border rounded-lg px-3 py-2 bg-card min-h-[70px]"
                    value={decisionReason}
                    onChange={(e) => setDecisionReason(e.target.value)}
                    placeholder={decisionAction === "DENY" ? "Why is this claim being denied?" : decisionAction === "ESCALATE" ? "Why does this need investigation?" : "Any notes for the record..."}
                  />
                </div>
                {decisionError && <p className="text-xs text-destructive font-semibold">{decisionError}</p>}
                <div className="flex gap-2">
                  <Button size="sm" disabled={submittingDecision} onClick={submitDecision} className="font-bold">
                    {submittingDecision ? "Recording..." : `Confirm ${decisionAction}`}
                  </Button>
                  <Button size="sm" variant="outline" disabled={submittingDecision} onClick={() => { setDecisionAction(null); setDecisionError(""); }}>
                    Cancel
                  </Button>
                </div>
              </div>
            ) : (
              <div className="flex flex-wrap gap-3">
                <Button
                  className="bg-emerald-600 hover:bg-emerald-700 text-white font-bold flex items-center gap-2"
                  onClick={() => setDecisionAction("PAY")}
                >
                  <span className="material-symbols-outlined text-[18px]">paid</span>
                  Pay Claim
                </Button>
                <Button
                  variant="destructive"
                  className="font-bold flex items-center gap-2"
                  onClick={() => setDecisionAction("DENY")}
                >
                  <span className="material-symbols-outlined text-[18px]">block</span>
                  Deny Claim
                </Button>
                <Button
                  className="bg-amber-500 hover:bg-amber-600 text-white font-bold flex items-center gap-2"
                  onClick={() => setDecisionAction("ESCALATE")}
                >
                  <span className="material-symbols-outlined text-[18px]">priority_high</span>
                  Escalate to SIU
                </Button>
              </div>
            )}
          </CardContent>
        </Card>

        {/* Diagnostic Tabs */}
        <div className="flex gap-2 border-b overflow-x-auto pb-px">
          {[
            { id: "overview", label: "Forensic Overview", icon: "dashboard" },
            { id: "member", label: "Statements & Narratives", icon: "person" },
            { id: "physics", label: "Trajectory Simulation", icon: "architecture" },
            { id: "media", label: "Media & Photo Anomaly", icon: "photo_library" },
            { id: "alignment", label: "Document Alignment", icon: "compare_arrows" },
          ].map((tab) => (
            <button
              key={tab.id}
              onClick={() => setActiveTab(tab.id)}
              className={`flex items-center gap-2 px-5 py-3 text-xs font-bold uppercase tracking-wider border-b-2 transition-all whitespace-nowrap ${
                activeTab === tab.id
                  ? "border-primary text-primary bg-primary/5"
                  : "border-transparent text-muted-foreground hover:text-foreground hover:border-border"
              }`}
            >
              <span className="material-symbols-outlined text-base">{tab.icon}</span>
              {tab.label}
            </button>
          ))}
        </div>

        {/* Tab Workspace Panel */}
        <div className="w-full space-y-8">

          {/* TAB 1: OVERVIEW */}
          {activeTab === "overview" && (
            <div className="space-y-8 animate-in fade-in duration-300">
              {/* Core Summaries Stacked Vertically */}
              <div className="flex flex-col gap-6 w-full">
                
                {/* Overall Score */}
                <div className="bg-card p-6 rounded-2xl border border-border shadow-sm space-y-4 w-full">
                  <div>
                    <h4 className="text-xs font-black text-muted-foreground uppercase tracking-widest mb-4 flex items-center gap-1">
                      <span className="material-symbols-outlined text-sm">speed</span>
                      Overall Score Index
                    </h4>
                    <div className="flex flex-col md:flex-row items-center gap-6 py-2">
                      <div className="relative size-28 shrink-0">
                        <svg className="size-full -rotate-90" viewBox="0 0 36 36">
                          <path d="M18 2.0845a 15.9155 15.9155 0 0 1 0 31.831a 15.9155 15.9155 0 0 1 0 -31.831" fill="none" stroke="hsl(var(--muted))" strokeWidth="3.5" />
                          <path d="M18 2.0845a 15.9155 15.9155 0 0 1 0 31.831a 15.9155 15.9155 0 0 1 0 -31.831" fill="none" stroke={rb.overall_score >= 70 ? "hsl(var(--destructive))" : rb.overall_score >= 50 ? "#f59e0b" : "hsl(var(--primary))"} strokeWidth="3.5" strokeDasharray={`${rb.overall_score}, 100`} />
                        </svg>
                        <div className="absolute inset-0 flex flex-col items-center justify-center">
                          <span className="text-3xl font-black text-foreground leading-none">{rb.overall_score}</span>
                          <span className="text-[9px] font-bold text-muted-foreground uppercase mt-0.5">Risk</span>
                        </div>
                      </div>
                      <div className="space-y-2 flex-1">
                        <span className={`text-[10px] font-black uppercase px-2.5 py-1 rounded-full ${getDecisionColor(fa.decision)}`}>
                          {fa.risk_level} RISK LEVEL
                        </span>
                        <p className="text-xs text-muted-foreground leading-relaxed font-medium">
                          Calculated recursively based on photo, narrative alignment, physical rules, and historical trends.
                        </p>
                      </div>
                    </div>
                  </div>
                  
                  <div className="pt-4 border-t border-dashed border-border bg-muted/20 p-4 rounded-xl">
                    <p className="text-[10px] font-black uppercase text-muted-foreground tracking-widest mb-1.5">Algorithmic Math Explanation</p>
                    <p className="text-xs text-foreground/80 leading-relaxed font-medium italic">
                      {rb.explanation}
                    </p>
                  </div>
                </div>

                {/* Detection Summary */}
                <div className="bg-card p-6 rounded-2xl border border-border shadow-sm space-y-4 w-full">
                  <div>
                    <h4 className="text-xs font-black text-muted-foreground uppercase tracking-widest mb-4 flex items-center gap-1">
                      <span className="material-symbols-outlined text-sm">rule_folder</span>
                      AI Anomaly Detection
                    </h4>
                    <div className="grid grid-cols-2 md:grid-cols-4 gap-4 pb-2">
                      <div className="text-center p-3 bg-destructive/5 rounded-xl border border-destructive/10">
                        <p className="text-2xl font-black text-destructive">{ds.critical_issues ?? 0}</p>
                        <p className="text-[10px] font-black uppercase text-muted-foreground">Critical</p>
                      </div>
                      <div className="text-center p-3 bg-amber-50 rounded-xl border border-amber-100">
                        <p className="text-2xl font-black text-amber-700">{ds.high_risk_issues ?? 0}</p>
                        <p className="text-[10px] font-black uppercase text-muted-foreground">High Risk</p>
                      </div>
                      <div className="text-center p-3 bg-amber-50/50 rounded-xl border border-amber-100/50">
                        <p className="text-2xl font-black text-amber-600">{ds.medium_risk_issues ?? 0}</p>
                        <p className="text-[10px] font-black uppercase text-muted-foreground">Medium Risk</p>
                      </div>
                      <div className="text-center p-3 bg-blue-50 rounded-xl border border-blue-100">
                        <p className="text-2xl font-black text-blue-600">{ds.low_risk_issues ?? 0}</p>
                        <p className="text-[10px] font-black uppercase text-muted-foreground">Low Risk</p>
                      </div>
                    </div>
                  </div>

                  {ds.primary_concerns?.length > 0 && (
                    <div className="border-t pt-4 space-y-2">
                      <p className="text-[10px] font-black text-muted-foreground uppercase tracking-widest">Primary Engine Concerns</p>
                      {ds.primary_concerns.map((concern: string, idx: number) => (
                        <div key={idx} className="flex gap-2 items-start text-xs font-semibold text-foreground/95 bg-muted/40 p-2.5 rounded-lg border border-border w-full">
                          <span className="material-symbols-outlined text-destructive text-sm mt-0.5">report_problem</span>
                          <span>{concern}</span>
                        </div>
                      ))}
                    </div>
                  )}
                </div>

                {/* Pipeline */}
                <div className="bg-card p-6 rounded-2xl border border-border shadow-sm space-y-4 w-full">
                  <div>
                    <h4 className="text-xs font-black text-muted-foreground uppercase tracking-widest mb-4 flex items-center gap-1">
                      <span className="material-symbols-outlined text-sm">group</span>
                      Verification Pipeline
                    </h4>
                    <div className="space-y-2.5">
                      {Object.entries(report.parties_analyzed || {}).map(([party, done]) => (
                        <div key={party} className="flex items-center justify-between p-2.5 bg-muted/30 rounded-xl border border-border/50">
                          <span className="text-xs font-bold capitalize text-foreground flex items-center gap-2">
                            <span className="material-symbols-outlined text-sm">
                              {party === "member" ? "person" : party === "assessor" ? "engineering" : "build"}
                            </span>
                            {party.replace("_", " ")}
                          </span>
                          <span className={`text-[10px] font-black px-2.5 py-0.5 rounded-full ${done ? "bg-emerald-500/10 text-emerald-600" : "bg-muted text-muted-foreground"}`}>
                            {done ? "✓ COMPLETE" : "PENDING"}
                          </span>
                        </div>
                      ))}
                    </div>
                  </div>

                  <div className="pt-4 border-t border-border mt-4">
                    <h5 className="text-[10px] font-black text-muted-foreground uppercase tracking-widest mb-2 flex items-center gap-1">
                      <span className="material-symbols-outlined text-xs">monetization_on</span>
                      Multi-Party Financial Cost comparison
                    </h5>
                    <div className="space-y-1.5 text-xs">
                      <div className="flex justify-between p-1">
                        <span className="text-muted-foreground font-semibold">Member Stated Estimate</span>
                        <span className="font-bold text-foreground">
                          {report.member_submission?.estimated_cost ? `KES ${Number(report.member_submission.estimated_cost).toLocaleString()}` : "N/A"}
                        </span>
                      </div>
                      <div className="flex justify-between p-1 border-t border-dashed">
                        <span className="text-muted-foreground font-semibold">Assessor Physical Estimate</span>
                        <span className="font-bold text-foreground">
                          {report.assessor_submission?.report_details?.estimated_cost || report.assessor_submission?.estimated_cost
                            ? `KES ${Number(report.assessor_submission?.report_details?.estimated_cost || report.assessor_submission?.estimated_cost).toLocaleString()}`
                            : "N/A"}
                        </span>
                      </div>
                      <div className="flex justify-between p-1 border-t border-dashed">
                        <span className="text-muted-foreground font-semibold">Repair Shop Invoice Proposal</span>
                        <span className="font-bold text-foreground">
                          {report.repair_shop_submission?.total_cost
                            ? `KES ${Number(report.repair_shop_submission.total_cost).toLocaleString()}`
                            : "N/A"}
                        </span>
                      </div>
                    </div>
                  </div>
                </div>

              </div>

              {/* Component risk scoring matrix Stacked Vertically */}
              <div className="bg-card p-6 rounded-2xl border border-border shadow-sm space-y-6 w-full">
                <div className="flex justify-between items-center">
                  <h4 className="text-xs font-black text-muted-foreground uppercase tracking-widest flex items-center gap-1">
                    <span className="material-symbols-outlined text-sm">analytics</span>
                    Component Risk Score Matrix
                  </h4>
                  <span className="text-[10px] font-bold text-muted-foreground uppercase tracking-wider">Dynamic Weight Allocations</span>
                </div>
                <div className="flex flex-col gap-4">
                  {[
                    { label: "Photo Evidence", score: rb.photo_risk, key: "photo" },
                    { label: "Narrative Logic", score: rb.narrative_risk, key: "narrative" },
                    { label: "Financial Impact", score: rb.amount_risk, key: "amount" },
                    { label: "Location Validation", score: rb.location_risk, key: "location" },
                    { label: "Historical Records", score: rb.historical_risk, key: "historical" },
                    { label: "Cross-Party Audit", score: rb.cross_party_risk, key: "cross_party" },
                  ].map((item) => {
                    const weight = rb.weights_applied?.[item.key];
                    return (
                      <div key={item.label} className="p-4 bg-muted/25 border rounded-xl flex flex-col md:flex-row items-start md:items-center justify-between gap-4 w-full">
                        <div className="flex-1 space-y-1.5 w-full">
                          <div className="flex justify-between text-xs font-bold uppercase tracking-wider text-muted-foreground">
                            <span>{item.label}</span>
                            <span className="text-foreground">{Math.round(item.score || 0)}/100</span>
                          </div>
                          <Progress value={item.score || 0} className="h-2" />
                        </div>
                        {weight !== undefined && (
                          <div className="text-[10px] font-bold text-primary bg-primary/10 rounded-lg px-3 py-1.5 shrink-0 whitespace-nowrap">
                            Allocation Weight: {Math.round(weight * 100)}%
                          </div>
                        )}
                      </div>
                    );
                  })}
                </div>
              </div>

              {/* Critical warnings Stacked Vertically */}
              {warnings.length > 0 && (
                <div className="bg-card p-6 rounded-2xl border border-destructive/20 shadow-sm w-full">
                  <h4 className="text-xs font-black text-destructive uppercase tracking-widest mb-4 flex items-center gap-2">
                    <span className="material-symbols-outlined text-[20px]">warning</span>
                    Critical Verification Warnings ({warnings.length})
                  </h4>
                  <div className="space-y-3">
                    {warnings.map((w: any, i: number) => (
                      <div key={i} className={`p-4 rounded-xl border ${getSeverityColor(w.severity)} w-full`}>
                        <div className="flex items-start justify-between gap-4">
                          <div className="flex-1">
                            <div className="flex items-center gap-2 mb-1">
                              <span className="text-[10px] font-black uppercase tracking-wider">{w.severity}</span>
                              <span className="text-xs opacity-70">• Party: {w.party} • Type: {w.type?.replace(/_/g, " ")}</span>
                            </div>
                            <p className="text-xs font-semibold leading-relaxed">{w.message}</p>
                          </div>
                          {w.confidence !== undefined && (
                            <span className="text-[10px] font-black uppercase whitespace-nowrap bg-white/20 px-2 py-0.5 rounded">
                              {w.confidence}% conf.
                            </span>
                          )}
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {/* Recommendations Stacked Vertically */}
              <div className="flex flex-col gap-6 w-full">
                <div className="bg-card p-6 rounded-2xl border border-border shadow-sm space-y-4 w-full">
                  <h4 className="text-xs font-black text-muted-foreground uppercase tracking-widest flex items-center gap-2">
                    <span className="material-symbols-outlined text-primary text-[20px]">lightbulb</span>
                    Strategic Fraud Recommendations
                  </h4>
                  <ul className="space-y-3 text-xs w-full">
                    {(report.recommendations || []).map((r: string, i: number) => (
                      <li key={i} className="text-foreground flex items-start gap-2.5 bg-muted/20 p-2.5 rounded-xl border font-bold w-full">
                        <span className="text-primary mt-0.5">✓</span>
                        <span>{r}</span>
                      </li>
                    ))}
                    {(!report.recommendations || report.recommendations.length === 0) && (
                      <li className="text-muted-foreground italic text-center py-4 w-full">No automatic recommendations compiled.</li>
                    )}
                  </ul>
                </div>
                
                <div className="bg-card p-6 rounded-2xl border border-border shadow-sm space-y-4 w-full">
                  <h4 className="text-xs font-black text-muted-foreground uppercase tracking-widest flex items-center gap-2">
                    <span className="material-symbols-outlined text-primary text-[20px]">playlist_add_check</span>
                    SIU Workflow Next Actions
                  </h4>
                  <ul className="space-y-3 text-xs w-full">
                    {(report.next_actions || []).map((a: string, i: number) => (
                      <li key={i} className="text-foreground flex items-start gap-2.5 bg-muted/20 p-2.5 rounded-xl border font-bold w-full">
                        <span className="material-symbols-outlined text-primary text-base">arrow_right</span>
                        <span>{a}</span>
                      </li>
                    ))}
                    {(!report.next_actions || report.next_actions.length === 0) && (
                      <li className="text-muted-foreground italic text-center py-4 w-full">No workflow actions registered.</li>
                    )}
                  </ul>
                </div>
              </div>

            </div>
          )}

          {/* TAB 2: STATEMENTS & NARRATIVES */}
          {activeTab === "member" && (
            <div className="space-y-8 animate-in fade-in duration-300">
              
              {/* Extracted narrative metadata parameters */}
              <Card className="overflow-hidden border-none shadow-sm w-full">
                <CardHeader className="bg-primary/5 border-b border-primary/10">
                  <CardTitle className="text-sm font-black uppercase tracking-widest flex items-center gap-2 text-primary">
                    <span className="material-symbols-outlined">person</span>
                    Stated Statement Analysis & Metadata
                  </CardTitle>
                </CardHeader>
                <CardContent className="pt-6 space-y-6">
                  {report.member_submission?.narrative_analysis && (
                    <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                      <div className="p-3 bg-muted/30 rounded-xl border text-xs font-semibold">
                        <p className="text-[8px] font-black text-muted-foreground uppercase">Weather</p>
                        <p className="text-xs font-bold capitalize mt-0.5">{report.member_submission.narrative_analysis.extracted_data?.weather_conditions || "Unknown"}</p>
                      </div>
                      <div className="p-3 bg-muted/30 rounded-xl border text-xs font-semibold">
                        <p className="text-[8px] font-black text-muted-foreground uppercase">Impact Zone</p>
                        <p className="text-xs font-bold capitalize mt-0.5">{report.member_submission.narrative_analysis.extracted_data?.impact_type || "Unknown"}</p>
                      </div>
                      <div className="p-3 bg-muted/30 rounded-xl border text-xs font-semibold">
                        <p className="text-[8px] font-black text-muted-foreground uppercase">Quality Score</p>
                        <p className="text-xs font-bold mt-0.5">{report.member_submission.narrative_analysis.narrative_quality_score}/100</p>
                      </div>
                      <div className="p-3 bg-muted/30 rounded-xl border text-xs font-semibold">
                        <p className="text-[8px] font-black text-muted-foreground uppercase">Sentiment</p>
                        <p className="text-xs font-bold capitalize mt-0.5">{report.member_submission.narrative_analysis.sentiment || "Neutral"}</p>
                      </div>
                    </div>
                  )}

                  {/* Narrative Alert logs */}
                  {narrativeIssues.length > 0 && (
                    <div className="space-y-3">
                      <p className="text-[10px] font-black text-destructive uppercase tracking-widest font-mono">Narrative Alert Flags</p>
                      {narrativeIssues.map((n: any, idx: number) => (
                        <div key={idx} className={`p-4 rounded-xl border ${getSeverityColor(n.severity)} text-xs font-medium space-y-1 w-full`}>
                          <p className="font-black uppercase text-[10px]">{n.type?.replace(/_/g, " ")}</p>
                          <p className="leading-relaxed opacity-95">{n.description}</p>
                          {n.confidence && <p className="text-[9px] font-bold uppercase opacity-60 mt-1">Verification Confidence: {n.confidence}%</p>}
                        </div>
                      ))}
                    </div>
                  )}
                </CardContent>
              </Card>

              {/* Submissions comparison layout Stacked Vertically */}
              <div className="flex flex-col gap-6 w-full">
                {[
                  { 
                    title: "Member Submission", 
                    data: report.member_submission, 
                    icon: "person" 
                  },
                  { 
                    title: "Assessor Submission", 
                    data: report.assessor_submission, 
                    icon: "engineering" 
                  },
                  { 
                    title: "Repair Shop Submission", 
                    data: report.repair_shop_submission, 
                    icon: "build" 
                  },
                ].map((sub) => (
                  <div key={sub.title} className="bg-card p-6 rounded-2xl border border-border shadow-sm space-y-4 w-full">
                    <h4 className="text-xs font-black text-muted-foreground uppercase tracking-widest flex items-center gap-2">
                      <span className="material-symbols-outlined text-[20px]">{sub.icon}</span>
                      {sub.title}
                    </h4>
                    {sub.data ? (
                      <div className="space-y-4 text-xs w-full">
                        <p className="text-foreground leading-relaxed italic bg-muted/30 p-3.5 rounded-xl border">
                          "{sub.data.narrative || sub.data.report || sub.data.estimate || "No detailed log text provided."}"
                        </p>
                        <div className="flex items-center gap-2 text-muted-foreground">
                          <span className="material-symbols-outlined text-[16px]">photo_camera</span>
                          <span>{sub.data.photos_count || 0} evidence files attached</span>
                        </div>

                        {sub.data.narrative_analysis && (
                          <div className="space-y-2 pt-3 border-t">
                            <p className="text-[9px] font-black text-muted-foreground uppercase tracking-widest">Metadata Extraction</p>
                            <div className="flex flex-wrap gap-2">
                              {sub.data.narrative_analysis.extracted_data && Object.entries(sub.data.narrative_analysis.extracted_data).map(([k, v]: [string, any]) => (
                                <div key={k} className="p-2 bg-muted/20 rounded border border-border flex justify-between items-center text-[10px] font-semibold gap-4 min-w-[200px] flex-1">
                                  <span className="text-muted-foreground uppercase text-[8px]">{k.replace("_", " ")}</span>
                                  <span className="font-bold">{String(v)}</span>
                                </div>
                              ))}
                            </div>
                          </div>
                        )}
                      </div>
                    ) : (
                      <p className="text-xs text-muted-foreground italic bg-muted/20 p-4 rounded-xl border border-dashed text-center w-full">
                        Submission file pending from this party.
                      </p>
                    )}
                  </div>
                ))}
              </div>

            </div>
          )}

          {/* TAB 3: TRAJECTORY SIMULATION */}
          {activeTab === "physics" && (
            <div className="space-y-8 animate-in fade-in duration-300">
              
              {/* Simulator video and dashboard values Stacked Vertically */}
              {pr && pr.status ? (
                <div className="space-y-6 w-full">
                  <div className="flex flex-col gap-6 w-full">
                    
                    {/* Simulator Video Display */}
                    <div className="w-full">
                      {loadingSimulation ? (
                        <div className="bg-muted/30 p-8 rounded-2xl border flex flex-col items-center justify-center min-h-[300px] text-center w-full">
                          <div className="size-8 border-4 border-primary border-t-transparent rounded-full animate-spin mb-4"></div>
                          <p className="text-sm font-bold text-muted-foreground">Checking collision trajectory status...</p>
                        </div>
                      ) : simulationStatus?.video_ready ? (
                        <div className="overflow-hidden border border-border rounded-xl shadow-sm bg-card w-full">
                          <div className="bg-primary/5 border-b border-primary/10 flex flex-row items-center justify-between py-3 px-4 w-full">
                            <span className="text-xs font-black uppercase tracking-widest flex items-center gap-2 text-primary">
                              <span className="material-symbols-outlined text-lg">videocam</span>
                              3D Trajectory Simulation trajectory
                            </span>
                            <a 
                              href={`${BASE_URL}/api/analysis/claims/${claimId}/simulation-video`}
                              download={`collision_simulation_${claimId}.mp4`}
                              className="flex items-center gap-1.5 text-xs font-bold text-primary hover:underline bg-primary/10 px-3 py-1.5 rounded-lg transition-all"
                            >
                              <span className="material-symbols-outlined text-sm">download</span>
                              Download MP4
                            </a>
                          </div>
                          <div className="p-0 w-full">
                            <div className="relative aspect-video bg-black flex items-center justify-center overflow-hidden w-full max-h-[600px]">
                              <video 
                                controls 
                                className="w-full h-full"
                                src={`${BASE_URL}/api/analysis/claims/${claimId}/simulation-video`}
                              >
                                Your browser does not support HTML5 video streaming.
                              </video>
                            </div>
                          </div>
                        </div>
                      ) : (
                        <div className="bg-muted/10 p-8 rounded-2xl border text-center flex flex-col items-center justify-center min-h-[300px] border-dashed w-full">
                          <span className="material-symbols-outlined text-muted-foreground text-5xl mb-4">video_settings</span>
                          <h4 className="text-base font-bold text-foreground mb-1">Simulation Video Pending</h4>
                          <p className="text-xs text-muted-foreground max-w-sm leading-relaxed mb-4">
                            The physical trajectory simulation video is currently processing or has not been fully initiated.
                          </p>
                          <button 
                            className="px-4 py-2 border border-border rounded-lg text-xs font-bold hover:bg-muted transition-all flex items-center gap-2 bg-card"
                            onClick={() => {
                              setLoadingSimulation(true);
                              getSimulationStatus(claimId || "")
                                .then((status) => {
                                  setSimulationStatus(status);
                                  setLoadingSimulation(false);
                                })
                                .catch(() => setLoadingSimulation(false));
                            }}
                          >
                            <span className="material-symbols-outlined text-sm animate-spin">refresh</span>
                            Check Status
                          </button>
                        </div>
                      )}
                    </div>

                    {/* Simulation logs sidebar stacked below */}
                    <div className="space-y-4 w-full">
                      <div className="p-4 bg-muted/20 border border-border/80 rounded-xl flex flex-col justify-center text-center w-full">
                        <p className="text-[10px] font-black text-muted-foreground uppercase tracking-widest mb-1">Simulation Trajectory Data</p>
                        <div className="flex justify-between items-center py-2 border-b border-dashed text-xs font-semibold">
                          <span className="text-muted-foreground">Video Status</span>
                          <span className={`font-bold uppercase ${simulationStatus?.video_ready ? 'text-emerald-600' : 'text-amber-500'}`}>
                            {simulationStatus?.video_ready ? 'Generated' : 'Pending'}
                          </span>
                        </div>
                        <div className="flex justify-between items-center py-2 border-b border-dashed text-xs font-semibold">
                          <span className="text-muted-foreground">Physics Verdict</span>
                          <span className="font-bold text-foreground">{simulationStatus?.physics_verdict || "NOT_RUN"}</span>
                        </div>
                        <div className="flex justify-between items-center py-2 text-xs font-semibold">
                          <span className="text-muted-foreground">Simulator Risk Penalty</span>
                          <span className="font-bold text-primary">{simulationStatus?.physics_fraud_score ?? pr.physics_fraud_score}/100</span>
                        </div>
                      </div>

                      {pr.warnings?.length > 0 && (
                        <div className="p-4 bg-amber-500/5 border border-amber-500/20 rounded-xl space-y-2 text-xs font-semibold w-full">
                          <p className="text-[10px] font-black text-amber-800 uppercase tracking-widest flex items-center gap-1">
                            <span className="material-symbols-outlined text-xs">info</span>
                            Model Inference Warnings ({pr.warnings.length})
                          </p>
                          {pr.warnings.map((w: string, idx: number) => (
                            <p key={idx} className="text-[10px] font-medium text-amber-900/80 leading-relaxed">• {w}</p>
                          ))}
                        </div>
                      )}
                    </div>
                  </div>

                  {/* Scientific McHenry calculation explanation Stacked Vertically */}
                  <div className="flex flex-col gap-6 pt-4 border-t border-dashed w-full">
                    <div className="w-full space-y-4">
                      <p className="text-[10px] font-black text-muted-foreground uppercase tracking-widest">Kinetic Forensics Report</p>
                      <div className="p-5 bg-muted/40 rounded-2xl border italic text-xs leading-relaxed text-foreground/90 whitespace-pre-wrap">
                        {pr.physics_explanation || "No physical reconstruction document generated."}
                      </div>
                    </div>

                    <div className="w-full">
                      <div className="p-4 bg-muted/25 rounded-xl border flex flex-col justify-center text-center w-full">
                        <p className="text-[10px] font-black text-muted-foreground uppercase tracking-widest mb-1">Physics Fraud Index</p>
                        <p className="text-3xl font-black text-foreground">{pr.physics_fraud_score}/100</p>
                        <p className="text-xs text-muted-foreground font-semibold mt-1">{pr.verdict_reason}</p>
                      </div>
                    </div>
                  </div>

                  {/* Assessor measurement discrepancy: a colluding assessor can type in
                      whatever crush depth/angle produces the outcome they want -- this
                      surfaces when that number was cross-checked against the narrative
                      and the CV-detected photo severity and found inconsistent. */}
                  {pr.has_measurement_discrepancy && pr.measurement_flags?.length > 0 && (
                    <div className="p-5 bg-destructive/5 border-2 border-destructive/30 rounded-2xl space-y-3 w-full">
                      <p className="text-xs font-black text-destructive uppercase tracking-widest flex items-center gap-2">
                        <span className="material-symbols-outlined">gpp_maybe</span>
                        Assessor Measurement Discrepancy Detected
                      </p>
                      <p className="text-[11px] text-muted-foreground font-medium leading-relaxed">
                        The assessor's on-site measurement was cross-checked against the narrative and the
                        trained CV model's read of the photos. It did not match.
                      </p>
                      {pr.measurement_flags.map((f: string, idx: number) => (
                        <div key={idx} className="p-3 bg-card border border-destructive/20 rounded-xl text-xs font-semibold text-foreground/90 leading-relaxed">
                          {f}
                        </div>
                      ))}
                    </div>
                  )}

                  {/* Assessor's historical measurement-discrepancy rate across all
                      their claims -- a repeated pattern is a stronger fraud signal
                      than any single flagged claim. */}
                  {report.assessor_track_record && report.assessor_track_record.total_assessed_claims > 0 && (
                    <div className={`p-5 rounded-2xl border-2 space-y-3 w-full ${
                      report.assessor_track_record.flagged_rate_pct >= 30
                        ? "bg-destructive/5 border-destructive/30"
                        : "bg-muted/20 border-border"
                    }`}>
                      <p className={`text-xs font-black uppercase tracking-widest flex items-center gap-2 ${
                        report.assessor_track_record.flagged_rate_pct >= 30 ? "text-destructive" : "text-foreground"
                      }`}>
                        <span className="material-symbols-outlined">badge</span>
                        Assessor Track Record — {report.assessor_track_record.assessor_id}
                      </p>
                      <div className="grid grid-cols-3 gap-3 w-full">
                        <div className="p-3 bg-card border rounded-xl text-center">
                          <p className="text-lg font-black">{report.assessor_track_record.total_assessed_claims}</p>
                          <p className="text-[9px] font-black uppercase text-muted-foreground mt-1">Claims Assessed</p>
                        </div>
                        <div className="p-3 bg-card border rounded-xl text-center">
                          <p className="text-lg font-black">{report.assessor_track_record.flagged_measurement_discrepancies}</p>
                          <p className="text-[9px] font-black uppercase text-muted-foreground mt-1">Flagged Claims</p>
                        </div>
                        <div className="p-3 bg-card border rounded-xl text-center">
                          <p className="text-lg font-black">{report.assessor_track_record.flagged_rate_pct}%</p>
                          <p className="text-[9px] font-black uppercase text-muted-foreground mt-1">Flag Rate</p>
                        </div>
                      </div>
                      {report.assessor_track_record.flagged_rate_pct >= 30 && (
                        <p className="text-[11px] font-bold text-destructive">
                          Flag rate is elevated — recommend supervisor review of this assessor's recent claims.
                        </p>
                      )}
                    </div>
                  )}

                  {/* Physics contradictions Stacked Vertically */}
                  {pr.inconsistencies?.length > 0 && (
                    <div className="space-y-3 pt-3 border-t w-full">
                      <p className="text-[10px] font-black text-destructive uppercase tracking-widest">Kinetic Discrepancies</p>
                      {pr.inconsistencies.map((inc: any, idx: number) => (
                        <div key={idx} className="p-4 bg-destructive/5 border border-destructive/20 rounded-xl flex gap-3 w-full">
                          <span className="material-symbols-outlined text-destructive text-xl mt-0.5">report_problem</span>
                          <div>
                            <p className="text-xs font-black uppercase text-destructive">
                              {inc.type?.replace(/_/g, " ")} ({inc.severity})
                            </p>
                            <p className="text-xs text-foreground/90 leading-relaxed font-semibold mt-1">
                              {inc.description}
                            </p>
                            {inc.confidence && (
                              <p className="text-[9px] font-bold text-muted-foreground uppercase mt-1">
                                Reconstruction Confidence: {Math.round(inc.confidence * 100)}%
                              </p>
                            )}
                          </div>
                        </div>
                      ))}
                    </div>
                  )}

                </div>
              ) : (
                <div className="text-center py-12 text-muted-foreground text-sm italic bg-muted/25 rounded-2xl border border-dashed w-full">
                  Trajectory Simulation details are not configured for this claim type.
                </div>
              )}

            </div>
          )}

          {/* TAB 4: MEDIA & PHOTO ANOMALIES */}
          {activeTab === "media" && (
            <div className="space-y-8 animate-in fade-in duration-300">
              
              {/* Photo anomaly summary Stacked Vertically */}
              <div className="flex flex-col gap-4 w-full">
                <div className="p-4 bg-muted/20 border rounded-xl text-center w-full">
                  <p className="text-3xl font-black text-foreground">{photoAnomalies.length}</p>
                  <p className="text-[10px] font-black uppercase text-muted-foreground mt-1">Visual Anomalies Logged</p>
                </div>
                <div className="p-4 bg-muted/20 border rounded-xl text-center w-full">
                  <p className="text-3xl font-black text-foreground">{rb.photo_risk}/100</p>
                  <p className="text-[10px] font-black uppercase text-muted-foreground mt-1">Overall Media Risk Penalty</p>
                </div>
                <div className="p-4 bg-muted/20 border rounded-xl flex items-center justify-center w-full">
                  <Badge variant="outline" className="font-mono text-xs uppercase px-4 py-1.5 border-dashed">
                    Photos verified: {Object.values(report.parties_analyzed).filter(Boolean).length} Parties
                  </Badge>
                </div>
              </div>

              {/* Photo verification lists */}
              <Card className="overflow-hidden border-none shadow-sm w-full">
                <CardHeader className="bg-primary/5 border-b border-primary/10">
                  <CardTitle className="text-sm font-black uppercase tracking-widest flex items-center gap-2 text-primary">
                    <span className="material-symbols-outlined">photo_library</span>
                    Image Signature & Metadata Verifications
                  </CardTitle>
                </CardHeader>
                <CardContent className="pt-6">
                  {photoAnomalies.length > 0 ? (
                    <div className="space-y-4">
                      {photoAnomalies.map((ano: any, idx: number) => {
                        const isCritical = ano.severity?.toLowerCase() === "critical" || ano.severity?.toLowerCase() === "high";
                        return (
                          <div key={idx} className={`p-5 rounded-2xl border flex items-start gap-4 transition-all w-full ${
                            isCritical ? "bg-destructive/5 border-destructive/20" : "bg-amber-50/50 border-amber-100"
                          }`}>
                            <span className={`material-symbols-outlined text-xl mt-0.5 ${isCritical ? "text-destructive" : "text-amber-500"}`}>
                              {isCritical ? "report_problem" : "warning"}
                            </span>
                            <div className="flex-1 space-y-1">
                              <div className="flex flex-wrap gap-2 items-center">
                                <span className={`text-[10px] font-black uppercase px-2 py-0.5 rounded ${
                                  isCritical ? "bg-destructive/15 text-destructive" : "bg-amber-100 text-amber-800"
                                }`}>
                                  {ano.severity} SEVERITY
                                </span>
                                <span className="text-xs text-muted-foreground font-bold capitalize">Party Origin: {ano.party}</span>
                                {ano.confidence && (
                                  <span className="text-[9px] font-black text-muted-foreground border px-1.5 rounded bg-card">
                                    {ano.confidence}% confidence
                                  </span>
                                )}
                              </div>
                              <p className="text-xs font-black uppercase text-foreground/90 tracking-tight pt-1">{ano.type?.replace(/_/g, " ")}</p>
                              <p className="text-xs leading-relaxed text-muted-foreground font-medium italic">"{ano.description}"</p>
                            </div>
                          </div>
                        );
                      })}
                    </div>
                  ) : (
                    <div className="text-center py-8 text-xs italic text-muted-foreground bg-muted/20 border border-dashed rounded-xl w-full">
                      No photographic structure or metadata anomalies verified. All signatures align.
                    </div>
                  )}
                </CardContent>
              </Card>

            </div>
          )}

          {/* TAB 5: ALIGNMENT DETAILS */}
          {activeTab === "alignment" && (
            <div className="space-y-8 animate-in fade-in duration-300">
              
              {/* Document Alignment control Stacked Vertically */}
              {cp && cp.verification_quality ? (
                <Card className="overflow-hidden border-none shadow-sm w-full">
                  <CardHeader className="bg-primary/5 border-b border-primary/10">
                    <div className="flex justify-between items-center w-full">
                      <CardTitle className="text-sm font-black uppercase tracking-widest flex items-center gap-2 text-primary">
                        <span className="material-symbols-outlined">compare_arrows</span>
                        Cross-Party Document Matching Audit
                      </CardTitle>
                      <Badge variant="outline" className="font-mono text-xs uppercase">{cp.verification_quality}</Badge>
                    </div>
                  </CardHeader>
                  <CardContent className="pt-6 space-y-6">
                    <div className="flex flex-col gap-4 w-full">
                      <div className="p-4 bg-muted/20 border rounded-xl text-center w-full">
                        <p className="text-xl font-black">{cp.inconsistency_count ?? 0}</p>
                        <p className="text-[10px] font-black uppercase text-muted-foreground mt-1">Total Inconsistency Count</p>
                      </div>
                      <div className="p-4 bg-muted/20 border rounded-xl text-center w-full">
                        <p className="text-xl font-black">{cp.cross_party_risk_score ?? 0}/100</p>
                        <p className="text-[10px] font-black uppercase text-muted-foreground mt-1">Alignment Risk Index Penalty</p>
                      </div>
                      <div className="p-4 bg-muted/20 border rounded-xl text-center w-full">
                        <p className="text-xl font-black">{cp.duplicate_photos_detected ?? 0}</p>
                        <p className="text-[10px] font-black uppercase text-muted-foreground mt-1">Duplicate Visual Assets</p>
                      </div>
                      <div className="p-4 bg-muted/20 border rounded-xl text-center flex flex-col justify-center items-center w-full">
                        <p className="text-xs font-black text-primary uppercase">
                          {cp.inconsistencies_found ? "CONFLICTS REVEALED" : "SIGNATURES ALIGNED"}
                        </p>
                        <p className="text-[10px] font-black uppercase text-muted-foreground mt-1">State</p>
                      </div>
                    </div>

                    {/* Discrepancy violations list */}
                    {crossPartyIssues.length > 0 ? (
                      <div className="space-y-3 w-full">
                        <p className="text-[10px] font-black text-destructive uppercase tracking-widest font-mono">Discrepancy Violations</p>
                        {crossPartyIssues.map((inc: any, idx: number) => {
                          const isCritical = inc.severity?.toLowerCase() === "critical" || inc.severity?.toLowerCase() === "high";
                          return (
                            <div key={idx} className={`p-4 rounded-xl border flex gap-3 w-full ${isCritical ? "bg-destructive/5 border-destructive/20" : "bg-amber-50/50 border-amber-100"}`}>
                              <span className={`material-symbols-outlined text-xl mt-0.5 ${isCritical ? "text-destructive" : "text-amber-500"}`}>
                                report_problem
                              </span>
                              <div>
                                <p className="text-xs font-black uppercase text-foreground/90 flex gap-2 items-center">
                                  <span>{inc.type?.replace(/_/g, " ")}</span>
                                  <span className={`text-[8px] px-1.5 py-0.5 rounded font-bold uppercase ${
                                    isCritical ? "bg-destructive/25 text-destructive" : "bg-amber-100 text-amber-800"
                                  }`}>{inc.severity}</span>
                                </p>
                                <p className="text-xs text-muted-foreground font-medium mt-1 italic leading-relaxed">"{inc.description}"</p>
                              </div>
                            </div>
                          );
                        })}
                      </div>
                    ) : (
                      <div className="text-center py-6 text-xs italic text-muted-foreground bg-muted/20 rounded-xl border border-dashed w-full">
                        No mismatches compiled. Member reports, Assessor sheets, and Workshop estimates are correlated.
                      </div>
                    )}

                    {/* Detailed logs */}
                    {cp.details?.length > 0 && (
                      <div className="space-y-2 pt-3 border-t w-full">
                        <p className="text-[10px] font-black text-muted-foreground uppercase tracking-widest">Cross-Party Verification Log</p>
                        {cp.details.map((detail: any, idx: number) => {
                          const message = typeof detail === "object" && detail !== null 
                            ? (detail.description || detail.message || JSON.stringify(detail)) 
                            : String(detail);
                          const severity = typeof detail === "object" && detail !== null ? detail.severity : undefined;
                          const isCritical = severity?.toLowerCase() === "critical" || severity?.toLowerCase() === "high";

                          return (
                            <div key={idx} className={`p-3 rounded-xl border text-xs font-medium flex items-start gap-2.5 w-full ${
                              isCritical 
                                ? "bg-destructive/5 border-destructive/20 text-destructive-foreground" 
                                : "bg-muted/40 border-border text-foreground/80"
                            }`}>
                              <span className={`material-symbols-outlined text-base mt-0.5 ${isCritical ? "text-destructive" : "text-primary"}`}>
                                {isCritical ? "report_problem" : "check_circle"}
                              </span>
                              <div className="flex-1 text-left">
                                {severity && (
                                  <span className={`inline-block text-[9px] font-black uppercase px-1.5 py-0.5 rounded mr-2 ${
                                    isCritical ? "bg-destructive/20 text-destructive" : "bg-muted text-muted-foreground"
                                  }`}>
                                    {severity}
                                  </span>
                                )}
                                <span className="text-foreground">{message}</span>
                              </div>
                            </div>
                          );
                        })}
                      </div>
                    )}
                  </CardContent>
                </Card>
              ) : (
                <div className="text-center py-12 text-muted-foreground text-sm italic bg-muted/25 rounded-2xl border border-dashed w-full">
                  No Document alignment analysis available.
                </div>
              )}

              {/* Supporting documents (police abstract / ID / garage quote) uploaded
                  by member or assessor, with their OCR-extracted data. */}
              <Card className="overflow-hidden border-none shadow-sm w-full">
                <CardHeader className="bg-primary/5 border-b border-primary/10">
                  <CardTitle className="text-sm font-black uppercase tracking-widest flex items-center gap-2 text-primary">
                    <span className="material-symbols-outlined">document_scanner</span>
                    Supporting Documents (OCR Extraction)
                  </CardTitle>
                </CardHeader>
                <CardContent className="pt-6">
                  {report.documents?.length > 0 ? (
                    <div className="space-y-4 w-full">
                      {report.documents.map((doc: any, idx: number) => {
                        const fields = doc.parsed_fields
                          ? (typeof doc.parsed_fields === "string" ? JSON.parse(doc.parsed_fields) : doc.parsed_fields)
                          : {};
                        const populatedFields = Object.entries(fields).filter(
                          ([, v]) => v !== null && v !== "" && !(Array.isArray(v) && v.length === 0)
                        );
                        const correctedFields = doc.corrected_fields
                          ? (typeof doc.corrected_fields === "string" ? JSON.parse(doc.corrected_fields) : doc.corrected_fields)
                          : null;
                        const populatedCorrections = correctedFields
                          ? Object.entries(correctedFields).filter(
                              ([, v]) => v !== null && v !== "" && !(Array.isArray(v) && v.length === 0)
                            )
                          : [];
                        const confidence = doc.extraction_confidence ?? 0;
                        return (
                          <div key={idx} className="p-4 border rounded-xl bg-muted/10 w-full space-y-3">
                            <div className="flex justify-between items-center flex-wrap gap-2">
                              <div className="flex items-center gap-2">
                                <Badge variant="outline" className="uppercase text-[9px] font-black">{doc.party}</Badge>
                                <p className="text-xs font-black uppercase tracking-wide">
                                  {doc.document_type?.replace(/_/g, " ")}
                                </p>
                              </div>
                              <div className="flex items-center gap-2">
                                {correctedFields && (
                                  <Badge variant="outline" className="text-[9px] font-bold uppercase border-amber-500/50 text-amber-700">
                                    Self-corrected
                                  </Badge>
                                )}
                                <Badge className={confidence >= 60 ? "bg-emerald-600" : confidence > 0 ? "bg-amber-500" : "bg-muted-foreground/40"}>
                                  {confidence}% confidence
                                </Badge>
                                <a
                                  href={`${BASE_URL}/api/analysis/documents/${doc.id}/file`}
                                  target="_blank"
                                  rel="noopener noreferrer"
                                  title="View original document"
                                  className="text-muted-foreground hover:text-primary transition-colors"
                                >
                                  <span className="material-symbols-outlined text-[18px] align-middle">visibility</span>
                                </a>
                              </div>
                            </div>
                            <p className="text-[10px] text-muted-foreground font-mono">{doc.filename}</p>

                            <div>
                              <p className="text-[9px] font-black uppercase text-muted-foreground mb-1">
                                {correctedFields ? "Original OCR Read" : "OCR Read"}
                              </p>
                              {populatedFields.length > 0 ? (
                                <div className="grid grid-cols-2 gap-2 pt-2 border-t border-dashed">
                                  {populatedFields.map(([k, v]) => (
                                    <div key={k} className="text-xs">
                                      <span className="text-muted-foreground uppercase text-[9px] font-black block">{k.replace(/_/g, " ")}</span>
                                      <span className="font-semibold text-foreground/90">
                                        {Array.isArray(v) ? v.join(", ") : String(v)}
                                      </span>
                                    </div>
                                  ))}
                                </div>
                              ) : (
                                <p className="text-[11px] italic text-muted-foreground pt-2 border-t border-dashed">
                                  No structured fields extracted — document may be illegible or extraction failed.
                                </p>
                              )}
                            </div>

                            {correctedFields && (
                              <div className="bg-amber-500/5 border border-amber-500/20 rounded-lg p-3">
                                <p className="text-[9px] font-black uppercase text-amber-800 mb-1">
                                  Corrected by {doc.corrected_by} on {doc.corrected_at?.split(" ")[0]}
                                </p>
                                <div className="grid grid-cols-2 gap-2">
                                  {populatedCorrections.map(([k, v]) => {
                                    const original = fields[k];
                                    const changed = JSON.stringify(original) !== JSON.stringify(v);
                                    return (
                                      <div key={k} className="text-xs">
                                        <span className="text-muted-foreground uppercase text-[9px] font-black block">{k.replace(/_/g, " ")}</span>
                                        <span className={`font-semibold ${changed ? "text-amber-800" : "text-foreground/90"}`}>
                                          {Array.isArray(v) ? v.join(", ") : String(v)}
                                        </span>
                                      </div>
                                    );
                                  })}
                                </div>
                              </div>
                            )}
                          </div>
                        );
                      })}
                    </div>
                  ) : (
                    <div className="text-center py-8 text-xs italic text-muted-foreground bg-muted/20 rounded-xl border border-dashed w-full">
                      No supporting documents uploaded for this claim.
                    </div>
                  )}
                </CardContent>
              </Card>

            </div>
          )}

        </div>
      </div>
    </AdminLayout>
  );
}