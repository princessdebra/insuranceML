import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import AssessorLayout from "@/layouts/AssessorLayout";
import { getClaimDetails, getSimulationStatus, getAiRolAuditTrail, recordAiRolAction, AiRolRecord, BASE_URL } from "@/lib/api";
import DocumentsPanel from "@/components/DocumentsPanel";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Progress } from "@/components/ui/progress";
import { Separator } from "@/components/ui/separator";
import { Button } from "@/components/ui/button";

export default function AssessorClaimDetails() {
  const { claimId } = useParams();
  const navigate = useNavigate();
  const [data, setData] = useState<any>(null);
  const [analysis, setAnalysis] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [activeTab, setActiveTab] = useState("overview");

  // Physics simulation state
  const [simulationStatus, setSimulationStatus] = useState<any>(null);
  const [loadingSimulation, setLoadingSimulation] = useState(false);

  // AI-ROL governance state
  const [aiRolTrail, setAiRolTrail] = useState<AiRolRecord[]>([]);
  const [loadingAiRol, setLoadingAiRol] = useState(false);
  const [submittingAction, setSubmittingAction] = useState(false);
  const [showOverrideInput, setShowOverrideInput] = useState(false);
  const [overrideReason, setOverrideReason] = useState("");
  const [actionError, setActionError] = useState("");

  const refetchClaimDetails = () => {
    const assessorId = localStorage.getItem("assessorId");
    if (!assessorId || !claimId) return;
    getClaimDetails(claimId, assessorId)
      .then((d) => {
        if (d.success) {
          setData(d);
          // Parse stringified JSON for the deep analysis
          if (d.claim_details?.analysis_result) {
            try {
              const parsed = JSON.parse(d.claim_details.analysis_result);
              setAnalysis(parsed);
            } catch (e) {
              console.error("Failed to parse analysis_result JSON", e);
            }
          }
        }
        setLoading(false);
      })
      .catch(() => setLoading(false));
  };

  useEffect(() => {
    const assessorId = localStorage.getItem("assessorId");
    if (!assessorId || !claimId) {
      navigate("/assessor/login");
      return;
    }
    refetchClaimDetails();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [claimId, navigate]);

  // Fetch simulation video status on tab shift
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

  const refreshAiRolTrail = () => {
    if (!claimId) return;
    setLoadingAiRol(true);
    getAiRolAuditTrail(claimId)
      .then((res) => setAiRolTrail(res.records || []))
      .catch((err) => console.error("Failed to fetch AI-ROL audit trail", err))
      .finally(() => setLoadingAiRol(false));
  };

  // Fetch AI-ROL governance audit trail on tab shift
  useEffect(() => {
    if (activeTab === "advisory" && claimId) {
      refreshAiRolTrail();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeTab, claimId]);

  const latestAdvisoryRecord = aiRolTrail
    .filter((r) => r.capability === "ai_advisory")
    .slice(-1)[0];

  const submitHandlerAction = async (action: "proceed" | "clarify" | "escalate" | "override") => {
    if (!claimId) return;
    const assessorId = localStorage.getItem("assessorId") || "unknown";
    if (action === "override" && !overrideReason.trim()) {
      setActionError("An override reason is required.");
      return;
    }
    setActionError("");
    setSubmittingAction(true);
    try {
      const res = await recordAiRolAction({
        claim_id: claimId,
        handler_id: assessorId,
        action,
        capability: "ai_advisory",
        reason: action === "override" ? overrideReason.trim() : undefined,
      });
      if (!res.success) {
        setActionError(res.detail || "Failed to record action.");
      } else {
        setOverrideReason("");
        setShowOverrideInput(false);
        refreshAiRolTrail();
      }
    } catch (err) {
      console.error("Failed to record AI-ROL action", err);
      setActionError("Failed to record action — check your connection and try again.");
    } finally {
      setSubmittingAction(false);
    }
  };

  if (loading) {
    return (
      <AssessorLayout>
        <div className="flex items-center justify-center h-full min-h-[400px]">
          <div className="flex flex-col items-center gap-2">
            <div className="size-8 border-4 border-primary border-t-transparent rounded-full animate-spin"></div>
            <p className="text-muted-foreground font-medium">Retrieving complete claims forensics...</p>
          </div>
        </div>
      </AssessorLayout>
    );
  }

  if (!data?.success) {
    return (
      <AssessorLayout>
        <div className="flex items-center justify-center h-full p-8 text-center">
          <div className="max-w-md space-y-4">
            <span className="material-symbols-outlined text-destructive text-6xl">error</span>
            <h2 className="text-2xl font-bold">Access Denied or Not Found</h2>
            <p className="text-muted-foreground">We couldn't retrieve the details for claim {claimId}. Ensure you are the assigned assessor for this claim.</p>
            <Link to="/assessor/dashboard" className="inline-block px-6 py-2 bg-primary text-primary-foreground rounded-lg font-bold">Return to Dashboard</Link>
          </div>
        </div>
      </AssessorLayout>
    );
  }

  const { claim_details, assignment, photos, member_info } = data;
  
  // Use parsed analysis if available, otherwise fallback to top-level fields
  const riskScore = analysis?.risk_scoring?.overall_score ?? claim_details.fraud_risk_score;
  const riskLevel = analysis?.risk_scoring?.risk_level ?? claim_details.risk_level;
  const recommendations = analysis?.recommendations ?? [];
  const photoResults = analysis?.photo_analysis?.results || [];
  const physics = analysis?.physics_reconstruction || {};
  const crossParty = analysis?.cross_party_verification || {};
  // Consolidated Narrative Intelligence + Computer Vision + Physics +
  // Cross-validation recommendation from claims-advisory-v1 (see
  // ClaimOrchestrator._build_ai_advisory in service.py).
  const aiAdvisory = analysis?.ai_advisory || null;

  const getActionBadgeColor = (action: string) => {
    const a = (action || "").toLowerCase();
    if (a.includes("reject") || a.includes("decline")) return "bg-destructive/10 text-destructive border-destructive/20";
    if (a.includes("escalate") || a.includes("investigat") || a.includes("refer")) return "bg-amber-500/10 text-amber-600 border-amber-500/20";
    if (a.includes("approve") || a.includes("proceed")) return "bg-emerald-500/10 text-emerald-600 border-emerald-500/20";
    return "bg-muted text-muted-foreground";
  };

  const getVerdictBadgeColor = (verdict: string) => {
    switch (verdict?.toUpperCase()) {
      case "CONSISTENT":
        return "bg-emerald-500/10 text-emerald-600 border-emerald-500/20";
      case "SUSPICIOUS":
        return "bg-amber-500/10 text-amber-600 border-amber-500/20";
      case "INCONSISTENT":
        return "bg-destructive/10 text-destructive border-destructive/20";
      default:
        return "bg-muted text-muted-foreground";
    }
  };

  return (
    <AssessorLayout>
      <div className="p-8 max-w-7xl mx-auto w-full space-y-8 pb-24">
        
        {/* Header Section */}
        <div className="flex flex-col xl:flex-row xl:items-center justify-between gap-6 pb-6 border-b">
          <div>
            <nav className="flex mb-2">
              <ol className="flex items-center space-x-2 text-xs text-muted-foreground">
                <li><Link to="/assessor/dashboard" className="hover:text-primary transition-colors">Dashboard</Link></li>
                <li><span className="material-symbols-outlined text-[12px]">chevron_right</span></li>
                <li className="text-primary font-bold">{claimId}</li>
              </ol>
            </nav>
            <div className="flex items-center gap-3">
              <h1 className="text-4xl font-black text-foreground tracking-tighter">Claim Audit Space</h1>
              <Badge className={`uppercase px-3 py-1 ${
                riskLevel === 'high' ? 'bg-destructive' : 
                riskLevel === 'medium' ? 'bg-amber-500' : 'bg-emerald-500'
              }`}>
                {riskLevel} Risk Index
              </Badge>
            </div>
            <p className="text-muted-foreground mt-1 flex items-center gap-2">
              <span className="material-symbols-outlined text-sm">location_on</span>
              {claim_details.location} · Assigned: {new Date(assignment.assigned_at).toLocaleDateString()}
            </p>
          </div>
          
          {/* Action Links */}
          <div className="flex flex-wrap gap-3">
            <Link to={`/assessor/schedule-inspection/${claimId}`} className="px-5 py-2.5 border-2 border-primary text-primary rounded-xl font-black text-xs uppercase tracking-widest hover:bg-primary/5 transition-all flex items-center gap-2 shadow-sm">
              <span className="material-symbols-outlined text-xl">calendar_month</span> Schedule
            </Link>
            <Link to={`/assessor/start-inspection/${claimId}`} className="px-5 py-2.5 bg-primary text-primary-foreground rounded-xl font-black text-xs uppercase tracking-widest hover:brightness-110 transition-all flex items-center gap-2 shadow-lg shadow-primary/20">
              <span className="material-symbols-outlined text-xl">assignment</span> Start Inspection
            </Link>
            <Link to={`/repair-shop/${claimId}`} className="px-5 py-2.5 bg-card border-2 border-border text-foreground rounded-xl font-black text-xs uppercase tracking-widest hover:bg-muted transition-all flex items-center gap-2 shadow-sm">
              <span className="material-symbols-outlined text-xl">build</span> Repair Shop
            </Link>
          </div>
        </div>

        {/* Diagnostic Tabs */}
        <div className="flex gap-2 border-b overflow-x-auto pb-px">
          {[
            { id: "overview", label: "Overview", icon: "dashboard" },
            { id: "advisory", label: "AI Advisory", icon: "psychology_alt" },
            { id: "member", label: "Member & Narrative", icon: "person" },
            { id: "physics", label: "Physics Simulation", icon: "architecture" },
            { id: "media", label: "Evidence & Photos", icon: "photo_library" },
            { id: "alignment", label: "Cross-Party Alignment", icon: "compare_arrows" },
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

        {/* Unified Full-Width Workspace Panel */}
        <div className="w-full space-y-8">
          
          {/* TAB: OVERVIEW */}
          {activeTab === "overview" && (
            <div className="space-y-8 animate-in fade-in duration-300">
              {/* Core Metrics Grid */}
              <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
                <div className="bg-card p-4 rounded-xl border flex flex-col justify-between min-h-[110px] shadow-sm">
                  <p className="text-[10px] font-black text-muted-foreground uppercase tracking-widest">Stated Estimate</p>
                  <p className="text-xl font-black text-foreground">KES {Number(claim_details.estimated_cost).toLocaleString()}</p>
                </div>
                <div className="bg-card p-4 rounded-xl border flex flex-col justify-between min-h-[110px] shadow-sm">
                  <p className="text-[10px] font-black text-muted-foreground uppercase tracking-widest">Fraud Risk Score</p>
                  <div className="flex items-baseline gap-1">
                    <p className="text-xl font-black text-foreground">{riskScore}</p>
                    <span className="text-[10px] text-muted-foreground font-bold">/100</span>
                  </div>
                </div>
                <div className="bg-card p-4 rounded-xl border flex flex-col justify-between min-h-[110px] shadow-sm">
                  <p className="text-[10px] font-black text-muted-foreground uppercase tracking-widest">Physics Verdict</p>
                  <p className={`text-xs font-black uppercase text-center py-1 rounded border px-3 w-fit ${getVerdictBadgeColor(physics.physics_verdict)}`}>
                    {physics.physics_verdict || "N/A"}
                  </p>
                </div>
                <div className="bg-card p-4 rounded-xl border flex flex-col justify-between min-h-[110px] shadow-sm">
                  <p className="text-[10px] font-black text-muted-foreground uppercase tracking-widest">Inspection Status</p>
                  <Badge variant="outline" className="w-fit text-xs font-bold uppercase mt-1 px-3 py-1">
                    {assignment?.status || "Pending"}
                  </Badge>
                </div>
              </div>

              {/* Fraud Risk Scoreboard & Weights Matrix */}
              <div className="grid grid-cols-1 lg:grid-cols-3 gap-8">
                {/* Risk Gauge Panel */}
                <div className="bg-card p-6 rounded-2xl border border-border shadow-sm flex flex-col justify-between">
                  <div>
                    <h4 className="text-xs font-black text-muted-foreground uppercase tracking-widest mb-4 flex items-center gap-1.5">
                      <span className="material-symbols-outlined text-sm text-primary">psychology</span>
                      Fraud Risk Intelligence
                    </h4>
                    <div className="flex items-center justify-center py-6">
                      <div className="relative size-32">
                        <svg className="size-full -rotate-90" viewBox="0 0 36 36">
                          <path d="M18 2.0845a 15.9155 15.9155 0 0 1 0 31.831a 15.9155 15.9155 0 0 1 0 -31.831" fill="none" stroke="hsl(var(--muted))" strokeWidth="3" />
                          <path d="M18 2.0845a 15.9155 15.9155 0 0 1 0 31.831a 15.9155 15.9155 0 0 1 0 -31.831" fill="none" stroke={riskScore >= 70 ? "hsl(var(--destructive))" : riskScore >= 50 ? "#f59e0b" : "hsl(var(--primary))"} strokeWidth="3" strokeDasharray={`${riskScore}, 100`} />
                        </svg>
                        <div className="absolute inset-0 flex flex-col items-center justify-center">
                          <span className="text-2xl font-black text-foreground">{riskScore}</span>
                          <span className="text-[9px] font-bold text-muted-foreground uppercase mt-0.5">Fraud Index</span>
                        </div>
                      </div>
                    </div>
                  </div>
                  <div className="p-4 bg-primary/10 rounded-xl border border-primary/20 mt-4">
                    <p className="text-[10px] font-black uppercase text-primary mb-1.5">Algorithm Calculation Explanation</p>
                    <p className="text-xs font-semibold leading-relaxed italic text-foreground/80">
                      {analysis?.risk_scoring?.explanation || "No scoring breakdown documentation compiled."}
                    </p>
                  </div>
                </div>

                {/* Sub-component metrics & Weights */}
                <div className="lg:col-span-2 bg-card p-6 rounded-2xl border border-border shadow-sm flex flex-col justify-between">
                  <div>
                    <h4 className="text-xs font-black text-muted-foreground uppercase tracking-widest mb-4 flex items-center gap-1.5">
                      <span className="material-symbols-outlined text-sm text-primary">analytics</span>
                      Component Scoring Matrix & Weights
                    </h4>
                    <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                      {analysis?.risk_scoring?.component_scores && Object.entries(analysis.risk_scoring.component_scores).map(([key, value]: [string, any]) => {
                        const weightKey = key.replace("_analysis", "").replace("_based", "").replace("_reconstruction", "").replace("patterns", "historical");
                        const weight = analysis?.risk_scoring?.weights?.[weightKey];

                        return (
                          <div key={key} className="space-y-1.5 p-3.5 bg-muted/20 border rounded-xl">
                            <div className="flex justify-between text-[10px] font-black uppercase tracking-tighter">
                              <span className="opacity-70">{key.replace(/_/g, ' ')}</span>
                              <span className="text-primary font-bold">
                                {Math.round(value)}% {weight !== undefined && `(wt: ${Math.round(weight * 100)}%)`}
                              </span>
                            </div>
                            <Progress value={value} className="h-1.5" />
                          </div>
                        );
                      })}
                    </div>
                  </div>
                </div>
              </div>

              {/* Assignment Logistics & Dispatch history */}
              <div className="grid grid-cols-1 lg:grid-cols-3 gap-8">
                {/* Dispatch Details */}
                <div className="bg-card p-6 rounded-2xl border shadow-sm space-y-4 lg:col-span-2">
                  <h4 className="text-xs font-black text-muted-foreground uppercase tracking-widest flex items-center gap-1.5">
                    <span className="material-symbols-outlined text-sm text-primary font-bold">assignment_ind</span>
                    Technical Assignment Tracking
                  </h4>
                  <div className="grid grid-cols-1 md:grid-cols-2 gap-4 text-xs font-semibold">
                    <div className="p-3 bg-muted/30 border rounded-xl flex justify-between items-center">
                      <span className="text-muted-foreground">Assignment ID</span>
                      <span className="font-mono font-bold text-foreground">{assignment?.assignment_id}</span>
                    </div>
                    <div className="p-3 bg-muted/30 border rounded-xl flex justify-between items-center">
                      <span className="text-muted-foreground">Assigned At</span>
                      <span>{new Date(assignment?.assigned_at).toLocaleString()}</span>
                    </div>
                    {assignment?.inspection_date && (
                      <div className="p-3 bg-muted/30 border rounded-xl flex justify-between items-center md:col-span-2">
                        <span className="text-muted-foreground">Scheduled Inspection Date</span>
                        <span>{new Date(assignment.inspection_date).toLocaleString()}</span>
                      </div>
                    )}
                    <div className="p-4 bg-muted/30 border rounded-xl md:col-span-2 space-y-1.5">
                      <p className="text-[10px] font-black text-muted-foreground uppercase">Internal Dispatch & Operational Notes</p>
                      <p className="text-xs italic text-muted-foreground font-semibold leading-relaxed">
                        {assignment?.notes || "No additional dispatch notes or instructions recorded."}
                      </p>
                    </div>
                  </div>
                </div>

                {/* AI Suggestions / Recommendations */}
                <div className="bg-card p-6 rounded-2xl border shadow-sm flex flex-col justify-between">
                  <h4 className="text-xs font-black text-muted-foreground uppercase tracking-widest mb-4 flex items-center gap-1.5">
                    <span className="material-symbols-outlined text-sm text-primary">list_alt</span>
                    Diagnostic Suggestions
                  </h4>
                  <div className="space-y-3">
                    {recommendations.map((rec: string, i: number) => (
                      <div key={i} className="flex gap-3 p-3 rounded-xl bg-muted/50 text-[11px] font-semibold border border-border">
                        <span className="text-primary font-black">✓</span>
                        <span>{rec}</span>
                      </div>
                    ))}
                  </div>
                </div>
              </div>

              {/* Member Info Summary */}
              {member_info && (
                <Card className="overflow-hidden border-none shadow-sm">
                  <CardHeader className="bg-primary/5 border-b border-primary/10">
                    <CardTitle className="text-sm font-black uppercase tracking-widest flex items-center gap-2 text-primary">
                      <span className="material-symbols-outlined">badge</span>
                      Member Account Profile
                    </CardTitle>
                  </CardHeader>
                  <CardContent className="pt-6 grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4 text-xs font-semibold">
                    <div className="space-y-1.5 p-3.5 bg-muted/30 rounded-xl border">
                      <p className="text-muted-foreground uppercase text-[9px] font-black">Full Legal Name</p>
                      <p className="text-sm font-bold text-foreground">{member_info.name}</p>
                    </div>
                    <div className="space-y-1.5 p-3.5 bg-muted/30 rounded-xl border">
                      <p className="text-muted-foreground uppercase text-[9px] font-black">Member ID Ref</p>
                      <p className="text-sm font-mono font-bold text-primary">{member_info.member_id}</p>
                    </div>
                    <div className="space-y-1.5 p-3.5 bg-muted/30 rounded-xl border">
                      <p className="text-muted-foreground uppercase text-[9px] font-black">Contact Email</p>
                      <p className="text-sm font-bold text-foreground">{member_info.email}</p>
                    </div>
                    <div className="space-y-1.5 p-3.5 bg-muted/30 rounded-xl border">
                      <p className="text-muted-foreground uppercase text-[9px] font-black">Phone Number</p>
                      <p className="text-sm font-bold text-foreground">{member_info.phone}</p>
                    </div>
                  </CardContent>
                </Card>
              )}

              {/* AI Performance Telemetry & Audits */}
              <div className="bg-card p-6 rounded-2xl border border-border shadow-sm flex flex-col md:flex-row justify-between items-center gap-4 text-xs font-semibold">
                <p className="text-[10px] font-black uppercase text-muted-foreground tracking-widest flex items-center gap-2 shrink-0">
                  <span className="material-symbols-outlined text-sm">settings_suggest</span>
                  AI Telemetry & Audits
                </p>
                <div className="flex flex-wrap gap-4 w-full justify-end">
                  {analysis?.analysis_quality && (
                    <div className="p-2.5 bg-muted/30 rounded-xl border">
                      <span className="text-muted-foreground font-semibold mr-2">Engine:</span>
                      <span className="font-bold text-primary capitalize">{analysis.analysis_quality.replace(/_/g, ' ')}</span>
                    </div>
                  )}
                  {claim_details.processing_time_ms && (
                    <div className="p-2.5 bg-muted/30 rounded-xl border font-mono">
                      <span className="text-muted-foreground font-semibold mr-2 font-display">Latency:</span>
                      <span className="font-bold">{(claim_details.processing_time_ms / 1000).toFixed(2)}s</span>
                    </div>
                  )}
                  {analysis?.timestamp && (
                    <div className="p-2.5 bg-muted/30 rounded-xl border font-mono">
                      <span className="text-muted-foreground font-semibold mr-2 font-display">Timestamp:</span>
                      <span className="font-bold">{new Date(analysis.timestamp).toISOString()}</span>
                    </div>
                  )}
                </div>
              </div>
            </div>
          )}

          {/* TAB: AI ADVISORY */}
          {activeTab === "advisory" && (
            <div className="space-y-6 animate-in fade-in duration-300">
              {aiAdvisory ? (
                <>
                  <Card className="overflow-hidden border-none shadow-sm">
                    <CardHeader className="bg-primary/5 border-b border-primary/10">
                      <div className="flex flex-wrap justify-between items-center gap-3 w-full">
                        <CardTitle className="text-sm font-black uppercase tracking-widest flex items-center gap-2 text-primary">
                          <span className="material-symbols-outlined">psychology_alt</span>
                          Consolidated AI Advisory Recommendation
                        </CardTitle>
                        <Badge variant="outline" className="font-mono text-[9px] uppercase">
                          {aiAdvisory.source === "claims-advisory-v1" ? "Live AI Advisory" : "Rule-Based Fallback"}
                        </Badge>
                      </div>
                    </CardHeader>
                    <CardContent className="pt-6 space-y-6">
                      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                        <div className="p-4 bg-muted/20 border rounded-xl">
                          <p className="text-[8px] font-black text-muted-foreground uppercase mb-1.5">Recommended Action</p>
                          <p className={`text-xs font-black uppercase text-center py-1.5 rounded border px-3 w-fit ${getActionBadgeColor(aiAdvisory.recommended_action)}`}>
                            {aiAdvisory.recommended_action || "N/A"}
                          </p>
                        </div>
                        <div className="p-4 bg-muted/20 border rounded-xl">
                          <p className="text-[8px] font-black text-muted-foreground uppercase mb-1.5">Early Risk Indicator</p>
                          <p className={`text-xs font-black uppercase text-center py-1.5 rounded border px-3 w-fit ${getVerdictBadgeColor(aiAdvisory.early_risk_indicator === "High" ? "INCONSISTENT" : aiAdvisory.early_risk_indicator === "Medium" ? "SUSPICIOUS" : "CONSISTENT")}`}>
                            {aiAdvisory.early_risk_indicator || "N/A"}
                          </p>
                        </div>
                        <div className="p-4 bg-muted/20 border rounded-xl">
                          <p className="text-[8px] font-black text-muted-foreground uppercase mb-1.5">Model Confidence</p>
                          <div className="flex items-baseline gap-1">
                            <p className="text-xl font-black text-foreground">{Math.round((aiAdvisory.confidence || 0) * 100)}</p>
                            <span className="text-[10px] text-muted-foreground font-bold">%</span>
                          </div>
                        </div>
                      </div>

                      <div className="space-y-2">
                        <p className="text-[10px] font-black text-muted-foreground uppercase tracking-widest">Explanation</p>
                        <div className="p-4 bg-primary/10 rounded-xl border border-primary/20 italic text-xs font-semibold leading-relaxed text-foreground/80">
                          {aiAdvisory.explanation || "No explanation provided."}
                        </div>
                      </div>

                      {/* Human oversight actions -- AI-ROL requires every AI
                          recommendation to end in a recorded, traceable
                          handler decision, not just be read and forgotten. */}
                      <div className="space-y-3 pt-2 border-t">
                        <p className="text-[10px] font-black text-muted-foreground uppercase tracking-widest">
                          Claims Handler Decision
                        </p>
                        {latestAdvisoryRecord?.handler_action ? (
                          <div className="p-4 bg-emerald-500/5 border border-emerald-500/20 rounded-xl flex items-start gap-3">
                            <span className="material-symbols-outlined text-emerald-600">verified</span>
                            <div>
                              <p className="text-xs font-black uppercase text-emerald-700">
                                {latestAdvisoryRecord.handler_action} — recorded
                              </p>
                              <p className="text-[10px] font-semibold text-muted-foreground mt-0.5">
                                By {latestAdvisoryRecord.handler_id} at{" "}
                                {latestAdvisoryRecord.decided_at ? new Date(latestAdvisoryRecord.decided_at).toLocaleString() : "—"}
                              </p>
                              {latestAdvisoryRecord.override_reason && (
                                <p className="text-xs font-semibold text-foreground/80 mt-2 italic">
                                  Reason: {latestAdvisoryRecord.override_reason}
                                </p>
                              )}
                            </div>
                          </div>
                        ) : (
                          <>
                            <div className="flex flex-wrap gap-2">
                              <Button
                                size="sm"
                                disabled={submittingAction}
                                className="bg-emerald-600 hover:bg-emerald-700 text-white font-bold text-xs gap-1.5"
                                onClick={() => submitHandlerAction("proceed")}
                              >
                                <span className="material-symbols-outlined text-sm">check_circle</span>
                                Proceed
                              </Button>
                              <Button
                                size="sm"
                                variant="outline"
                                disabled={submittingAction}
                                className="font-bold text-xs gap-1.5"
                                onClick={() => submitHandlerAction("clarify")}
                              >
                                <span className="material-symbols-outlined text-sm">help</span>
                                Request Clarification
                              </Button>
                              <Button
                                size="sm"
                                variant="outline"
                                disabled={submittingAction}
                                className="font-bold text-xs gap-1.5 border-amber-500/40 text-amber-700 hover:bg-amber-500/10"
                                onClick={() => submitHandlerAction("escalate")}
                              >
                                <span className="material-symbols-outlined text-sm">priority_high</span>
                                Escalate
                              </Button>
                              <Button
                                size="sm"
                                variant="outline"
                                disabled={submittingAction}
                                className="font-bold text-xs gap-1.5 border-destructive/40 text-destructive hover:bg-destructive/10"
                                onClick={() => setShowOverrideInput((v) => !v)}
                              >
                                <span className="material-symbols-outlined text-sm">gavel</span>
                                Override
                              </Button>
                            </div>
                            {showOverrideInput && (
                              <div className="space-y-2 p-4 bg-destructive/5 border border-destructive/20 rounded-xl">
                                <p className="text-[10px] font-black uppercase text-destructive">
                                  Override reason (required)
                                </p>
                                <textarea
                                  className="w-full text-xs p-3 rounded-lg border bg-background resize-none"
                                  rows={3}
                                  placeholder="Explain why this AI recommendation is being overridden..."
                                  value={overrideReason}
                                  onChange={(e) => setOverrideReason(e.target.value)}
                                />
                                <Button
                                  size="sm"
                                  disabled={submittingAction}
                                  className="bg-destructive hover:bg-destructive/90 text-white font-bold text-xs"
                                  onClick={() => submitHandlerAction("override")}
                                >
                                  Confirm Override
                                </Button>
                              </div>
                            )}
                            {actionError && (
                              <p className="text-xs font-bold text-destructive">{actionError}</p>
                            )}
                          </>
                        )}
                      </div>
                    </CardContent>
                  </Card>

                  <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                    {[
                      { key: "narrative_intelligence_observation", label: "Narrative Intelligence", icon: "chat" },
                      { key: "computer_vision_observation", label: "Computer Vision", icon: "photo_camera" },
                      { key: "physics_math_consistency_observation", label: "Physics & Mathematical Consistency", icon: "architecture" },
                      { key: "cross_validation_risk_observation", label: "Cross-Validation & Risk Intelligence", icon: "compare_arrows" },
                    ].map(({ key, label, icon }) => {
                      const observation = aiAdvisory[key];
                      const hasObservation = observation && observation !== "None";
                      return (
                        <Card key={key} className="overflow-hidden border shadow-sm">
                          <CardHeader className="bg-muted/30 border-b py-3 px-4">
                            <CardTitle className="text-[10px] font-black uppercase tracking-widest text-muted-foreground flex items-center gap-2">
                              <span className="material-symbols-outlined text-sm">{icon}</span>
                              {label}
                            </CardTitle>
                          </CardHeader>
                          <CardContent className="p-4">
                            {hasObservation ? (
                              <p className="text-xs font-semibold leading-relaxed text-foreground/90">{observation}</p>
                            ) : (
                              <p className="text-xs italic text-muted-foreground">No observation flagged.</p>
                            )}
                          </CardContent>
                        </Card>
                      );
                    })}
                  </div>
                </>
              ) : (
                <div className="text-center py-12 text-muted-foreground text-sm italic bg-muted/25 rounded-2xl border border-dashed">
                  AI Advisory consolidation not available for this claim (analyzed before this capability was added, or the underlying model call failed with no fallback recorded).
                </div>
              )}

              {/* AI-ROL: Governance & Audit Trail -- the single record of every
                  AI recommendation across all capabilities, its confidence and
                  evidence, and any handler decision, per the Blueprint's
                  requirement that every capability be explainable and traceable. */}
              <Card className="overflow-hidden border-none shadow-sm">
                <CardHeader className="bg-primary/5 border-b border-primary/10">
                  <CardTitle className="text-sm font-black uppercase tracking-widest flex items-center gap-2 text-primary">
                    <span className="material-symbols-outlined">verified_user</span>
                    AI Governance &amp; Regulatory Layer (AI-ROL) — Audit Trail
                  </CardTitle>
                </CardHeader>
                <CardContent className="pt-6">
                  {loadingAiRol ? (
                    <p className="text-xs text-muted-foreground italic">Loading audit trail...</p>
                  ) : aiRolTrail.length === 0 ? (
                    <p className="text-xs text-muted-foreground italic">
                      No AI-ROL records found for this claim yet.
                    </p>
                  ) : (
                    <div className="space-y-3">
                      {aiRolTrail.map((r) => (
                        <div key={r.record_id} className="p-4 border rounded-xl bg-muted/10">
                          <div className="flex flex-wrap items-center justify-between gap-2 mb-1.5">
                            <Badge variant="outline" className="text-[9px] font-black uppercase font-mono">
                              {r.capability.replace(/_/g, " ")}
                            </Badge>
                            <div className="flex items-center gap-2">
                              {typeof r.confidence === "number" && (
                                <span className="text-[10px] font-bold text-muted-foreground">
                                  {Math.round(r.confidence * 100)}% confidence
                                </span>
                              )}
                              <span className="text-[10px] text-muted-foreground">
                                {new Date(r.created_at).toLocaleString()}
                              </span>
                            </div>
                          </div>
                          <p className="text-xs font-semibold text-foreground/90">{r.recommendation}</p>
                          {r.handler_action && (
                            <div className="mt-2 pt-2 border-t flex items-center gap-2 text-[10px] font-bold">
                              <span className="material-symbols-outlined text-xs text-emerald-600">check_circle</span>
                              <span className="uppercase text-emerald-700">{r.handler_action}</span>
                              <span className="text-muted-foreground">by {r.handler_id}</span>
                              {r.override_reason && (
                                <span className="italic text-foreground/70 normal-case">— {r.override_reason}</span>
                              )}
                            </div>
                          )}
                        </div>
                      ))}
                    </div>
                  )}
                </CardContent>
              </Card>
            </div>
          )}

          {/* TAB: MEMBER & NARRATIVE */}
          {activeTab === "member" && (
            <div className="space-y-6 animate-in fade-in duration-300">
              <Card className="overflow-hidden border-none shadow-sm">
                <CardHeader className="bg-primary/5 border-b border-primary/10">
                  <CardTitle className="text-sm font-black uppercase tracking-widest flex items-center gap-2 text-primary">
                    <span className="material-symbols-outlined">person</span>
                    Member Submission Auditing
                  </CardTitle>
                </CardHeader>
                <CardContent className="pt-6 space-y-6">
                  <div className="space-y-2">
                    <p className="text-[10px] font-black text-muted-foreground uppercase tracking-widest">Extracted Narrative Metadata</p>
                    {analysis?.member_submission?.narrative_analysis && (
                      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                        <div className="p-3 bg-muted/30 rounded-xl border text-xs font-semibold">
                          <p className="text-[8px] font-black text-muted-foreground uppercase">Weather</p>
                          <p className="text-xs font-bold capitalize mt-0.5">{analysis.member_submission.narrative_analysis.extracted_data?.weather_conditions || "Unknown"}</p>
                        </div>
                        <div className="p-3 bg-muted/30 rounded-xl border text-xs font-semibold">
                          <p className="text-[8px] font-black text-muted-foreground uppercase">Impact Zone</p>
                          <p className="text-xs font-bold capitalize mt-0.5">{analysis.member_submission.narrative_analysis.extracted_data?.impact_type || "Unknown"}</p>
                        </div>
                        <div className="p-3 bg-muted/30 rounded-xl border text-xs font-semibold">
                          <p className="text-[8px] font-black text-muted-foreground uppercase">Quality Score</p>
                          <p className="text-xs font-bold mt-0.5">{analysis.member_submission.narrative_analysis.narrative_quality_score}/100</p>
                        </div>
                        <div className="p-3 bg-muted/30 rounded-xl border text-xs font-semibold">
                          <p className="text-[8px] font-black text-muted-foreground uppercase">Sentiment</p>
                          <p className="text-xs font-bold capitalize mt-0.5">{analysis.member_submission.narrative_analysis.sentiment || "Neutral"}</p>
                        </div>
                      </div>
                    )}
                  </div>

                  <div className="space-y-2">
                    <p className="text-[10px] font-black text-muted-foreground uppercase tracking-widest">Full Stated Statement</p>
                    <div className="p-4 bg-muted/30 rounded-xl border italic text-xs leading-relaxed">
                      "{analysis?.member_submission?.narrative || claim_details.narrative || "No narrative details recorded."}"
                    </div>
                  </div>

                  {/* AI Inconsistencies */}
                  {analysis?.member_submission?.narrative_analysis?.inconsistencies?.length > 0 && (
                    <div className="space-y-3">
                      <p className="text-[10px] font-black text-destructive uppercase tracking-widest font-mono">Narrative Alert Flags</p>
                      {analysis.member_submission.narrative_analysis.inconsistencies.map((inc: any, idx: number) => (
                        <Alert key={idx} variant={inc.severity === 'major' ? 'destructive' : 'default'} className="bg-destructive/5 border-destructive/20">
                          <span className="material-symbols-outlined text-sm">warning</span>
                          <AlertTitle className="text-xs font-black uppercase">{inc.type} Conflict</AlertTitle>
                          <AlertDescription className="text-xs font-medium leading-relaxed mt-1">
                            {inc.description}
                          </AlertDescription>
                        </Alert>
                      ))}
                    </div>
                  )}
                </CardContent>
              </Card>
            </div>
          )}

          {/* TAB: PHYSICS SIMULATION */}
          {activeTab === "physics" && (
            <div className="space-y-6 animate-in fade-in duration-300">
              {/* Physics Simulation Video Section */}
              <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
                <div className="lg:col-span-2 space-y-6">
                  {loadingSimulation ? (
                    <div className="bg-card p-8 rounded-2xl border flex flex-col items-center justify-center min-h-[300px] text-center">
                      <div className="size-8 border-4 border-primary border-t-transparent rounded-full animate-spin mb-4"></div>
                      <p className="text-sm font-bold text-muted-foreground">Checking collision trajectory status...</p>
                    </div>
                  ) : simulationStatus?.video_ready ? (
                    <Card className="overflow-hidden border border-border shadow-sm">
                      <CardHeader className="bg-primary/5 border-b border-primary/10 flex flex-row items-center justify-between py-4">
                        <CardTitle className="text-xs font-black uppercase tracking-widest flex items-center gap-2 text-primary">
                          <span className="material-symbols-outlined text-lg">videocam</span>
                          3D Trajectory Simulation Trajectory
                        </CardTitle>
                        <a 
                          href={`${BASE_URL}/api/analysis/claims/${claimId}/simulation-video`}
                          download={`collision_simulation_${claimId}.mp4`}
                          className="flex items-center gap-1.5 text-xs font-bold text-primary hover:underline bg-primary/10 px-3 py-1.5 rounded-lg transition-all"
                        >
                          <span className="material-symbols-outlined text-sm">download</span>
                          Download MP4
                        </a>
                      </CardHeader>
                      <CardContent className="p-0">
                        <div className="relative aspect-video bg-black flex items-center justify-center overflow-hidden">
                          <video 
                            controls 
                            className="w-full h-full"
                            src={`${BASE_URL}/api/analysis/claims/${claimId}/simulation-video`}
                          >
                            Your browser does not support HTML5 video streaming.
                          </video>
                        </div>
                      </CardContent>
                    </Card>
                  ) : (
                    <div className="bg-card p-8 rounded-2xl border text-center flex flex-col items-center justify-center min-h-[300px] border-dashed">
                      <span className="material-symbols-outlined text-muted-foreground text-5xl mb-4">video_settings</span>
                      <h4 className="text-base font-bold text-foreground mb-1">Simulation Video Pending</h4>
                      <p className="text-xs text-muted-foreground max-w-sm leading-relaxed mb-4">
                        The physical trajectory simulation video is currently processing or has not been fully initiated.
                      </p>
                      <Button 
                        variant="outline" 
                        size="sm" 
                        className="font-bold gap-2 text-xs"
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
                      </Button>
                    </div>
                  )}
                </div>

                <div className="space-y-6">
                  {/* Summary of status endpoint stats */}
                  <Card className="overflow-hidden border border-border shadow-sm">
                    <CardHeader className="bg-muted/50 border-b py-3 px-4">
                      <CardTitle className="text-[10px] font-black uppercase tracking-widest text-muted-foreground">Reconstruction Data</CardTitle>
                    </CardHeader>
                    <CardContent className="p-4 space-y-3 text-xs font-semibold">
                      <div className="flex justify-between items-center py-2 border-b border-dashed">
                        <span className="text-muted-foreground">Video Status</span>
                        <span className={`font-bold uppercase ${simulationStatus?.video_ready ? 'text-emerald-600' : 'text-amber-500'}`}>
                          {simulationStatus?.video_ready ? 'Generated' : 'Pending'}
                        </span>
                      </div>
                      <div className="flex justify-between items-center py-2 border-b border-dashed">
                        <span className="text-muted-foreground">Physics Verdict</span>
                        <span className="font-bold text-foreground">{simulationStatus?.physics_verdict || "NOT_RUN"}</span>
                      </div>
                      <div className="flex justify-between items-center py-2">
                        <span className="text-muted-foreground">Simulator Risk Penalty</span>
                        <span className="font-bold text-primary">{simulationStatus?.physics_fraud_score ?? 0}/100</span>
                      </div>
                    </CardContent>
                  </Card>
                </div>
              </div>

              {/* Claimed vs. Reconstructed comparison -- the actual numbers
                  side by side, so this doesn't require reading a moving
                  diagram or a physics score to understand. */}
              {physics?.comparison && (
                <Card className="overflow-hidden border-none shadow-sm">
                  <CardHeader className="bg-primary/5 border-b border-primary/10">
                    <CardTitle className="text-sm font-black uppercase tracking-widest flex items-center gap-2 text-primary">
                      <span className="material-symbols-outlined">compare</span>
                      Claimed vs. Reconstructed
                    </CardTitle>
                  </CardHeader>
                  <CardContent className="pt-6">
                    <div className="overflow-x-auto">
                      <table className="w-full text-xs">
                        <thead>
                          <tr className="border-b text-[10px] font-black uppercase text-muted-foreground tracking-widest">
                            <th className="text-left pb-2">What was measured</th>
                            <th className="text-left pb-2">Claimant said</th>
                            <th className="text-left pb-2">Simulation found</th>
                            <th className="text-left pb-2">Match?</th>
                          </tr>
                        </thead>
                        <tbody className="divide-y divide-dashed">
                          {[
                            {
                              label: "Vehicle 1 speed",
                              claimed: physics.comparison.speed_v1_kmh?.claimed,
                              reconstructed: physics.comparison.speed_v1_kmh?.reconstructed,
                              unit: "km/h",
                              flagged: physics.comparison.velocity_fraud_flag,
                              isEstimate: physics.comparison.v1_speed_is_inferred,
                              confidence: physics.comparison.v1_speed_confidence,
                            },
                            {
                              label: "Vehicle 2 speed",
                              claimed: physics.comparison.speed_v2_kmh?.claimed,
                              reconstructed: physics.comparison.speed_v2_kmh?.reconstructed,
                              unit: "km/h",
                              flagged: false,
                              isEstimate: physics.comparison.v2_speed_is_inferred,
                              confidence: physics.comparison.v2_speed_confidence,
                            },
                            {
                              label: "Vehicle damage depth",
                              claimed: physics.comparison.crush_depth_mm?.claimed,
                              reconstructed: physics.comparison.crush_depth_mm?.reconstructed,
                              unit: "mm",
                              flagged: (physics.comparison.crush_depth_mm?.delta ?? 0) > 80,
                              isEstimate: false,
                              confidence: undefined,
                            },
                          ].map((row, i) => (
                            <tr key={i}>
                              <td className="py-3 font-bold text-foreground">
                                {row.label}
                                {row.isEstimate && (
                                  <span className="block text-[9px] font-semibold normal-case text-muted-foreground mt-0.5">
                                    Not stated by claimant — estimated from wording in their account
                                    {typeof row.confidence === "number"
                                      ? ` (${Math.round(row.confidence * 100)}% confidence)`
                                      : ""}
                                  </span>
                                )}
                              </td>
                              <td className="py-3 font-semibold text-foreground/80">{row.claimed ?? "—"} {row.unit}</td>
                              <td className="py-3 font-semibold text-foreground/80">{row.reconstructed ?? "—"} {row.unit}</td>
                              <td className="py-3">
                                {row.isEstimate ? (
                                  <Badge className="bg-muted text-muted-foreground border-muted-foreground/20 gap-1">
                                    <span className="material-symbols-outlined text-xs">help</span> Too uncertain to compare
                                  </Badge>
                                ) : row.flagged ? (
                                  <Badge className="bg-destructive/10 text-destructive border-destructive/20 gap-1">
                                    <span className="material-symbols-outlined text-xs">close</span> Doesn't match
                                  </Badge>
                                ) : (
                                  <Badge className="bg-emerald-500/10 text-emerald-600 border-emerald-500/20 gap-1">
                                    <span className="material-symbols-outlined text-xs">check</span> Matches
                                  </Badge>
                                )}
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  </CardContent>
                </Card>
              )}

              {physics && physics.status ? (
                <Card className="overflow-hidden border-none shadow-sm">
                  <CardHeader className="bg-primary/5 border-b border-primary/10">
                    <div className="flex justify-between items-center w-full">
                      <CardTitle className="text-sm font-black uppercase tracking-widest flex items-center gap-2 text-primary">
                        <span className="material-symbols-outlined">architecture</span>
                        Crash Forensics Simulation
                      </CardTitle>
                      <Badge className={getVerdictBadgeColor(physics.physics_verdict)}>{physics.physics_verdict}</Badge>
                    </div>
                  </CardHeader>
                  <CardContent className="pt-6 space-y-6">
                    <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                      <div className="p-4 bg-muted/20 border rounded-xl text-xs font-semibold">
                        <p className="text-[8px] font-black text-muted-foreground uppercase">Simulation Model</p>
                        <p className="text-xs font-bold uppercase mt-1">{physics.simulation_method || "N/A"}</p>
                      </div>
                      <div className="p-4 bg-muted/20 border rounded-xl text-xs font-semibold">
                        <p className="text-[8px] font-black text-muted-foreground uppercase">Pathway Class</p>
                        <p className="text-xs font-bold uppercase mt-1">{physics.pathway || "N/A"}</p>
                      </div>
                      <div className="p-4 bg-muted/20 border rounded-xl text-xs font-semibold">
                        <p className="text-[8px] font-black text-muted-foreground uppercase">Kinetic Fraud Penalty</p>
                        <p className="text-xs font-bold mt-1">{physics.physics_fraud_score}/100</p>
                      </div>
                    </div>

                    <div className="space-y-2">
                      <p className="text-[10px] font-black text-muted-foreground uppercase tracking-widest">Reconstruction Forensic report</p>
                      <div className="p-5 bg-muted/30 rounded-2xl border italic text-xs leading-relaxed text-foreground/80 whitespace-pre-wrap">
                        {physics.physics_explanation || "No reconstruction report generated."}
                      </div>
                    </div>

                    {/* Physics Warnings */}
                    {physics.warnings?.length > 0 && (
                      <div className="p-4 bg-amber-500/5 border border-amber-500/20 rounded-xl space-y-2">
                        <p className="text-[10px] font-black text-amber-800 uppercase tracking-widest flex items-center gap-1">
                          <span className="material-symbols-outlined text-xs">info</span>
                          Simulated Constraints Warnings ({physics.warnings.length})
                        </p>
                        {physics.warnings.map((w: string, idx: number) => (
                          <p key={idx} className="text-[10px] font-semibold text-amber-900/80 leading-relaxed">• {w}</p>
                        ))}
                      </div>
                    )}

                    {/* Physics Discrepancies */}
                    {physics.inconsistencies?.length > 0 && (
                      <div className="space-y-3 pt-3 border-t">
                        <p className="text-[10px] font-black text-destructive uppercase tracking-widest font-mono">Physical Inconsistency Faults</p>
                        {physics.inconsistencies.map((inc: any, idx: number) => (
                          <div key={idx} className="p-4 bg-destructive/5 border border-destructive/20 rounded-xl flex gap-3">
                            <span className="material-symbols-outlined text-destructive text-xl mt-0.5">report_problem</span>
                            <div>
                              <p className="text-xs font-black uppercase text-destructive">
                                {inc.type?.replace(/_/g, " ")} ({inc.severity})
                              </p>
                              <p className="text-xs text-foreground/80 leading-relaxed font-semibold mt-1">
                                {inc.description}
                              </p>
                            </div>
                          </div>
                        ))}
                      </div>
                    )}
                  </CardContent>
                </Card>
              ) : (
                <div className="text-center py-12 text-muted-foreground text-sm italic bg-muted/25 rounded-2xl border border-dashed">
                  Physics Reconstruction engine not ran or completed for this claim.
                </div>
              )}
            </div>
          )}

          {/* TAB: MEDIA & PHOTOS */}
          {activeTab === "media" && (
            <div className="space-y-6 animate-in fade-in duration-300">
              <Card className="overflow-hidden border-none shadow-sm">
                <CardHeader className="bg-primary/5 border-b border-primary/10">
                  <CardTitle className="text-sm font-black uppercase tracking-widest flex items-center gap-2 text-primary">
                    <span className="material-symbols-outlined">photo_library</span>
                    Visual Forensic Image Analysis
                  </CardTitle>
                </CardHeader>
                <CardContent className="pt-6">
                  <div className="space-y-6">
                    {photos?.map((photo: any, i: number) => {
                      const result = photoResults.find((r: any) => r.filename === photo.filename);
                      const detectedParty = photo.party || result?.party || result?.anomalies?.find((a: any) => a.party)?.party || 'Unknown';

                      return (
                        <div key={i} className="group border border-border rounded-2xl overflow-hidden p-5 hover:border-primary/30 transition-all space-y-4">
                          <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2 pb-3 border-b border-border">
                            <div className="flex items-center gap-3">
                              {photo.id ? (
                                <a href={`${BASE_URL}/api/analysis/photos/${photo.id}/file`} target="_blank" rel="noopener noreferrer">
                                  <img
                                    src={`${BASE_URL}/api/analysis/photos/${photo.id}/file`}
                                    alt={photo.filename}
                                    className="size-14 rounded-lg object-cover border border-border hover:opacity-80 transition-opacity"
                                  />
                                </a>
                              ) : (
                                <span className="material-symbols-outlined text-muted-foreground">image</span>
                              )}
                              <div className="flex flex-col">
                                <span className="text-sm font-bold text-foreground">{photo.filename}</span>
                                <span className="text-xs text-muted-foreground">{(photo.file_size / 1024 / 1024).toFixed(2)} MB</span>
                              </div>
                            </div>
                            <div className="flex items-center gap-2">
                              <Badge variant="outline" className="font-mono text-[9px] uppercase">
                                Party: {detectedParty}
                              </Badge>
                              {photo.id && (
                                <a
                                  href={`${BASE_URL}/api/analysis/photos/${photo.id}/file`}
                                  target="_blank"
                                  rel="noopener noreferrer"
                                  title="View full photo"
                                  className="text-muted-foreground hover:text-primary transition-colors"
                                >
                                  <span className="material-symbols-outlined text-[18px] align-middle">visibility</span>
                                </a>
                              )}
                            </div>
                          </div>

                          {result && (
                            <div className="grid grid-cols-2 gap-4 bg-muted/30 p-4 rounded-xl text-xs font-semibold">
                              <div className="flex justify-between border-r pr-4">
                                <span className="text-muted-foreground">AI Verification Confidence:</span>
                                <span className="text-primary font-black">{result.analysis_confidence || 85}%</span>
                              </div>
                              <div className="flex justify-between pl-2">
                                <span className="text-muted-foreground">Photo Risk Score:</span>
                                <span className={`font-black ${result.risk_score > 60 ? 'text-destructive' : 'text-primary'}`}>
                                  {result.risk_score}/100
                                </span>
                              </div>
                            </div>
                          )}
                          
                          <div className="space-y-2">
                            {result?.anomalies?.map((ano: any, idx: number) => (
                              <div key={idx} className={`p-3 rounded-lg flex gap-3 items-start border ${
                                ano.severity === 'high' ? 'bg-destructive/5 border-destructive/10' : 'bg-amber-50 border-amber-100'
                              }`}>
                                <span className={`material-symbols-outlined text-sm mt-0.5 ${
                                  ano.severity === 'high' ? 'text-destructive' : 'text-amber-500'
                                }`}>
                                  {ano.severity === 'high' ? 'error' : 'warning'}
                                </span>
                                <div>
                                  <span className="font-black block uppercase text-[10px] tracking-tight">{ano.type.replace(/_/g, ' ')}</span>
                                  <span className="text-xs leading-relaxed font-semibold opacity-80">{ano.description}</span>
                                </div>
                              </div>
                            )) || <div className="text-center py-4 text-xs italic text-muted-foreground">No metadata or visual modifications found.</div>}
                          </div>
                        </div>
                      );
                    })}
                  </div>
                </CardContent>
              </Card>

              {/* Supporting documents (police abstract / ID / garage quote) uploaded
                  by either party, with their OCR-extracted data -- e.g. the member's
                  police abstract, useful context before or during inspection. */}
              <Card className="overflow-hidden border-none shadow-sm">
                <CardHeader className="bg-primary/5 border-b border-primary/10">
                  <CardTitle className="text-sm font-black uppercase tracking-widest flex items-center gap-2 text-primary">
                    <span className="material-symbols-outlined">document_scanner</span>
                    Supporting Documents
                  </CardTitle>
                </CardHeader>
                <CardContent className="pt-6">
                  <DocumentsPanel
                    documents={data?.documents || []}
                    viewerParty="assessor"
                    correctorId={localStorage.getItem("assessorId") || ""}
                    onCorrected={refetchClaimDetails}
                  />
                </CardContent>
              </Card>
            </div>
          )}

          {/* TAB: CROSS-PARTY ALIGNMENT */}
          {activeTab === "alignment" && (
            <div className="space-y-6 animate-in fade-in duration-300">
              {crossParty && crossParty.verification_quality ? (
                <Card className="overflow-hidden border-none shadow-sm">
                  <CardHeader className="bg-primary/5 border-b border-primary/10">
                    <div className="flex justify-between items-center w-full">
                      <CardTitle className="text-sm font-black uppercase tracking-widest flex items-center gap-2 text-primary">
                        <span className="material-symbols-outlined">compare_arrows</span>
                        Cross-Party Document Matching Audit
                      </CardTitle>
                      <Badge variant="outline" className="font-mono text-xs uppercase">{crossParty.verification_quality}</Badge>
                    </div>
                  </CardHeader>
                  <CardContent className="pt-6 space-y-6">
                    <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                      <div className="p-4 bg-muted/20 border rounded-xl text-center">
                        <p className="text-xl font-black">{crossParty.inconsistency_count ?? 0}</p>
                        <p className="text-[10px] font-black uppercase text-muted-foreground mt-1">Total Inconsistency Count</p>
                      </div>
                      <div className="p-4 bg-muted/20 border rounded-xl text-center">
                        <p className="text-xl font-black">{crossParty.cross_party_risk_score ?? 0}/100</p>
                        <p className="text-[10px] font-black uppercase text-muted-foreground mt-1">Cross-Party Verification Risk</p>
                      </div>
                    </div>

                    {crossParty.inconsistencies?.length > 0 ? (
                      <div className="space-y-3">
                        <p className="text-[10px] font-black text-destructive uppercase tracking-widest font-mono">Discrepancy Violations</p>
                        {crossParty.inconsistencies.map((inc: any, idx: number) => (
                          <div key={idx} className="p-4 bg-destructive/5 border border-destructive/20 rounded-xl flex gap-3">
                            <span className="material-symbols-outlined text-destructive text-xl mt-0.5">report_problem</span>
                            <div>
                              <p className="text-xs font-black uppercase tracking-tight text-destructive">
                                {inc.type?.replace(/_/g, ' ')} ({inc.severity})
                              </p>
                              <p className="text-xs text-foreground/80 leading-relaxed font-semibold mt-1">
                                {inc.description}
                              </p>
                            </div>
                          </div>
                        ))}
                      </div>
                    ) : (
                      <div className="text-center py-6 text-xs italic text-muted-foreground bg-muted/20 rounded-xl border border-dashed">
                        No discrepancies found. Member accounts, technical reports, and workshop estimates are aligned.
                      </div>
                    )}
                  </CardContent>
                </Card>
              ) : (
                <div className="text-center py-12 text-muted-foreground text-sm italic bg-muted/25 rounded-2xl border border-dashed">
                  No multi-party alignment details completed or available yet.
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </AssessorLayout>
  );
}