import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import AssessorLayout from "@/layouts/AssessorLayout";
import { getClaimDetails, getSimulationStatus, getDamageDecisions, BASE_URL } from "@/lib/api";
import DocumentsPanel from "@/components/DocumentsPanel";
import DamagePanel from "@/components/DamagePanel";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Separator } from "@/components/ui/separator";
import { Button } from "@/components/ui/button";
import PhysicsReconstructionViewer from "@/components/reconstruction/PhysicsReconstructionViewer";

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

  // Assessor's own repair/replace calls on AI-detected damage components
  const [damageDecisions, setDamageDecisions] = useState<any[]>([]);
  const refreshDamageDecisions = () => {
    if (!claimId) return;
    getDamageDecisions(claimId)
      .then((res) => setDamageDecisions(res.decisions || []))
      .catch(() => {});
  };

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
    refreshDamageDecisions();
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
  const recommendations = analysis?.recommendations ?? [];
  const photoResults = analysis?.photo_analysis?.results || [];
  const physics = analysis?.physics_reconstruction || {};

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
            { id: "member", label: "Member & Narrative", icon: "person" },
            { id: "physics", label: "Physics Simulation", icon: "architecture" },
            { id: "media", label: "Evidence & Photos", icon: "photo_library" },
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
              <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
                <div className="bg-card p-4 rounded-xl border flex flex-col justify-between min-h-[110px] shadow-sm">
                  <p className="text-[10px] font-black text-muted-foreground uppercase tracking-widest">Stated Estimate</p>
                  <p className="text-xl font-black text-foreground">KES {Number(claim_details.estimated_cost).toLocaleString()}</p>
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
                        <span className="text-primary font-black"></span>
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
              {/* Interactive reconstruction -- renders the real physics
                  timeline live in the browser (Canvas2D), rather than only
                  offering the flat exported MP4 below. `physics` here comes
                  straight from claim_details.analysis_result, which already
                  carries the full timeline (no server-side trimming). */}
              {/* Covers video export, verdict, score, explanation, comparison
                  table and warnings all in one place -- the separate video
                  player / "Claimed vs. Reconstructed" table / "Crash
                  Forensics Simulation" panels that used to sit below this
                  were showing the exact same information a second time and
                  have been removed rather than kept as a duplicate. */}
              {physics && physics.status ? (
                <PhysicsReconstructionViewer physics={physics as any} claimId={claimId!} />
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

                          {(result?.detections?.length > 0 || result?.damage_zones?.length > 0) ? (
                            <div>
                              <p className="text-[10px] font-black uppercase tracking-widest text-muted-foreground mb-3 flex items-center gap-1.5">
                                <span className="material-symbols-outlined text-sm text-primary">directions_car</span>
                                AI-Powered Vehicle Damage Detection
                              </p>
                              <DamagePanel
                                claimId={claimId || ""}
                                photoId={photo.id}
                                filename={photo.filename}
                                detections={result.detections || []}
                                damageZones={result.damage_zones || []}
                                assessorId={localStorage.getItem("assessorId") || ""}
                                existingDecisions={Object.fromEntries(
                                  damageDecisions
                                    .filter((d: any) => d.filename === photo.filename)
                                    .map((d: any) => [d.detection_index, d.assessor_decision])
                                )}
                                onDecided={refreshDamageDecisions}
                              />
                            </div>
                          ) : (
                            <div className="space-y-2">
                              {/* Non-fraud quality findings only (lighting, angle, etc.) --
                                  duplicate/AI-generation/manipulation checks are fraud
                                  signals and stay out of the assessor's view. */}
                              {result?.anomalies
                                ?.filter((ano: any) =>
                                  !["cv_detected_damage", "duplicate_photo", "cross_party_duplicate"].includes(ano.type) &&
                                  !ano.type.includes("ai_generat") && !ano.type.includes("manipulat")
                                )
                                .map((ano: any, idx: number) => (
                                  <div key={idx} className="p-3 rounded-lg flex gap-3 items-start border bg-muted/20">
                                    <span className="material-symbols-outlined text-sm mt-0.5 text-muted-foreground">info</span>
                                    <div>
                                      <span className="font-black block uppercase text-[10px] tracking-tight">{ano.type.replace(/_/g, ' ')}</span>
                                      <span className="text-xs leading-relaxed font-semibold opacity-80">{ano.description}</span>
                                    </div>
                                  </div>
                                )) || <div className="text-center py-4 text-xs italic text-muted-foreground">No damage detections or quality notes for this photo.</div>}
                            </div>
                          )}
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
        </div>
      </div>
    </AssessorLayout>
  );
}