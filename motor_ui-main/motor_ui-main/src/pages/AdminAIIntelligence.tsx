import { ReactNode, useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import AdminLayout from "@/layouts/AdminLayout";
import {
  getAiIntelligenceOverview, AiIntelligenceOverview,
  getAiVsAssessorComparison, AiComparisonData,
  getAiRiskFraudQueue, RiskQueueClaim,
  getAiDecisionTrace, DecisionTrace,
  ClaimPhoto,
  getClaimFullReport,
  getAdminClaims,
  getAdminClaimDetails,
  getClaimRecipients, ClaimRecipient,
  aiDraftClaimMessage,
  sendClaimMessage,
  BASE_URL,
} from "@/lib/api";

/**
 * Lightweight claim picker -- admins don't have claim IDs memorized, so
 * every "enter a claim ID" box also doubles as a searchable dropdown over
 * every claim in the system (fetched once, filtered client-side).
 */
type PickableClaim = { claim_id: string; location?: string; created_at?: string; estimated_cost?: number };

function useClaimList() {
  const [claims, setClaims] = useState<PickableClaim[]>([]);
  useEffect(() => {
    getAdminClaims({ limit: 200 })
      .then((d) => {
        const list = (d?.claims || []).map((c: any) => ({
          claim_id: c.claim_id,
          location: c.location || c.final_assessment?.location,
          created_at: c.created_at,
          estimated_cost: c.estimated_cost || c.final_assessment?.estimated_cost,
        }));
        setClaims(list);
      })
      .catch(() => {});
  }, []);
  return claims;
}

function ClaimPicker({
  value,
  onChange,
  onSelect,
  placeholder,
}: {
  value: string;
  onChange: (v: string) => void;
  onSelect: (claimId: string) => void;
  placeholder: string;
}) {
  const claims = useClaimList();
  const [open, setOpen] = useState(false);
  const boxRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const onClickOutside = (e: MouseEvent) => {
      if (boxRef.current && !boxRef.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onClickOutside);
    return () => document.removeEventListener("mousedown", onClickOutside);
  }, []);

  const filtered = useMemo(() => {
    const q = value.trim().toLowerCase();
    const base = q ? claims.filter((c) => c.claim_id.toLowerCase().includes(q) || (c.location || "").toLowerCase().includes(q)) : claims;
    return base.slice(0, 30);
  }, [claims, value]);

  return (
    <div ref={boxRef} className="relative flex-1">
      <div className="flex items-center gap-3 bg-card p-4 rounded-xl border border-border shadow-sm">
        <span className="material-symbols-outlined text-muted-foreground">search</span>
        <input
          value={value}
          onChange={(e) => { onChange(e.target.value); setOpen(true); }}
          onFocus={() => setOpen(true)}
          onKeyDown={(e) => e.key === "Enter" && value.trim() && onSelect(value.trim())}
          placeholder={placeholder}
          className="flex-1 bg-transparent outline-none text-sm"
        />
        <button onClick={() => value.trim() && onSelect(value.trim())} className="px-4 py-2 bg-primary text-primary-foreground rounded-lg text-sm font-bold hover:bg-primary/90 transition-colors shrink-0">
          Load
        </button>
      </div>
      {open && claims.length > 0 && (
        <div className="absolute z-20 top-full left-0 right-0 mt-1 bg-card border border-border rounded-xl shadow-lg max-h-80 overflow-y-auto">
          {filtered.length === 0 && <p className="px-4 py-3 text-sm text-muted-foreground">No claims match "{value}".</p>}
          {filtered.map((c) => (
            <button
              key={c.claim_id}
              onClick={() => { onChange(c.claim_id); setOpen(false); onSelect(c.claim_id); }}
              className="w-full text-left px-4 py-2.5 hover:bg-muted/50 transition-colors flex items-center justify-between gap-3 border-b border-border last:border-0"
            >
              <div>
                <p className="text-sm font-bold text-foreground">{c.claim_id}</p>
                <p className="text-xs text-muted-foreground">{c.location || "Location unknown"}</p>
              </div>
              <div className="text-right shrink-0">
                {c.estimated_cost ? <p className="text-xs font-semibold text-foreground">KES {Number(c.estimated_cost).toLocaleString()}</p> : null}
                <p className="text-[10px] text-muted-foreground">{c.created_at?.split(" ")[0]}</p>
              </div>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

type Tab = "overview" | "review" | "trace" | "comparison" | "risk" | "comms";

const TABS: { key: Tab; label: string; icon: string }[] = [
  { key: "overview", label: "AI Overview", icon: "insights" },
  { key: "review", label: "Assessment Review", icon: "compare" },
  { key: "trace", label: "Decision Trace", icon: "account_tree" },
  { key: "comparison", label: "AI vs Assessor", icon: "balance" },
  { key: "risk", label: "Risk & Fraud Centre", icon: "gpp_maybe" },
  { key: "comms", label: "AI Communications", icon: "forward_to_inbox" },
];

export type CommsPrefill = { claimId: string; recipientType?: string; instruction?: string; nonce: number };
export type ReviewPrefill = { claimId: string; nonce: number };

export default function AdminAIIntelligence() {
  const navigate = useNavigate();
  const [tab, setTab] = useState<Tab>("overview");
  const [commsPrefill, setCommsPrefill] = useState<CommsPrefill | null>(null);
  const [reviewPrefill, setReviewPrefill] = useState<ReviewPrefill | null>(null);

  useEffect(() => {
    const adminId = localStorage.getItem("adminId");
    if (!adminId) navigate("/admin/login");
  }, [navigate]);

  // Shared by both action buttons on Assessment Review -- lands on the
  // Communications tab with the claim's parties already loaded (and, for
  // the evidence-request path, the member pre-selected with a drafted
  // instruction) rather than dropping the admin on a blank tab they have
  // to re-search the claim into.
  const goToComms = (opts: { claimId: string; recipientType?: string; instruction?: string }) => {
    setCommsPrefill({ ...opts, nonce: Date.now() });
    setTab("comms");
  };

  // Switching tabs unmounts Assessment Review, so its loaded claim would
  // otherwise be lost -- this "Back" path re-loads the same claim rather
  // than dropping the admin on an empty search box.
  const goBackToReview = (claimId: string) => {
    setReviewPrefill({ claimId, nonce: Date.now() });
    setTab("review");
  };

  return (
    <AdminLayout>
      <div className="p-8 max-w-7xl mx-auto w-full">
        <div className="mb-8 flex items-center gap-3">
          <div className="size-11 rounded-xl bg-gradient-to-br from-primary to-primary/60 flex items-center justify-center text-primary-foreground shadow-sm">
            <span className="material-symbols-outlined">neurology</span>
          </div>
          <div>
            <h2 className="text-3xl font-bold text-foreground">AI Intelligence Centre</h2>
            <p className="text-muted-foreground mt-1">What the AI is actually doing, how well it's doing it, and where humans still lead.</p>
          </div>
        </div>

        <div className="flex items-center gap-1 mb-8 border-b border-border overflow-x-auto">
          {TABS.map((t) => (
            <button
              key={t.key}
              onClick={() => setTab(t.key)}
              className={`flex items-center gap-2 px-4 py-3 text-sm font-bold whitespace-nowrap border-b-2 transition-colors ${
                tab === t.key ? "border-primary text-primary" : "border-transparent text-muted-foreground hover:text-foreground"
              }`}
            >
              <span className="material-symbols-outlined text-[18px]">{t.icon}</span>
              {t.label}
            </button>
          ))}
        </div>

        {tab === "overview" && <AIOverviewTab />}
        {tab === "review" && <AssessmentReviewTab onGoToComms={goToComms} reviewPrefill={reviewPrefill} />}
        {tab === "trace" && <DecisionTraceTab />}
        {tab === "comparison" && <ComparisonTab />}
        {tab === "risk" && <RiskFraudTab />}
        {tab === "comms" && <CommunicationsTab prefill={commsPrefill} onBackToClaim={goBackToReview} />}
      </div>
    </AdminLayout>
  );
}

/* ───────────────────────── AI OVERVIEW ───────────────────────── */

function AIOverviewTab() {
  const [data, setData] = useState<AiIntelligenceOverview | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    getAiIntelligenceOverview().then((d) => { setData(d); setLoading(false); }).catch(() => setLoading(false));
  }, []);

  if (loading) return <LoadingBlock label="Loading AI performance data..." />;
  if (!data || !data.success) return <EmptyBlock label="Couldn't load AI performance data." />;

  const confidencePct = data.ai_confidence_avg ?? 0;
  const confirmationPct = data.assessor_confirmation_rate ?? 0;

  const tiles = [
    { label: "Images Analysed", value: data.images_analysed.toLocaleString(), icon: "image_search", color: "text-blue-500", bg: "bg-blue-500/10" },
    { label: "Damage Detections", value: data.damage_detections.toLocaleString(), icon: "search", color: "text-primary", bg: "bg-primary/10" },
    { label: "Assessor Confirmation Rate", value: data.assessor_confirmation_rate !== null ? `${data.assessor_confirmation_rate}%` : "—", icon: "verified", color: "text-emerald-600", bg: "bg-emerald-500/10" },
    { label: "Low-Confidence Cases", value: data.low_confidence_cases.toLocaleString(), icon: "priority_high", color: "text-amber-600", bg: "bg-amber-500/10" },
    { label: "Pending Assessor Decisions", value: data.pending_assessor_decisions.toLocaleString(), icon: "hourglass_top", color: "text-violet-500", bg: "bg-violet-500/10" },
    { label: "Decisions Logged", value: data.total_assessor_decisions_logged.toLocaleString(), icon: "fact_check", color: "text-muted-foreground", bg: "bg-muted" },
  ];

  return (
    <div className="space-y-6">
      <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
        <div className="bg-card p-6 rounded-2xl border border-border shadow-sm md:col-span-1">
          <p className="text-xs font-bold uppercase tracking-wider text-muted-foreground mb-4">AI Confidence</p>
          <RadialGauge value={confidencePct} color="#22c55e" />
        </div>
        <div className="bg-card p-6 rounded-2xl border border-border shadow-sm md:col-span-1">
          <p className="text-xs font-bold uppercase tracking-wider text-muted-foreground mb-4">Assessor Confirmation Rate</p>
          <RadialGauge value={confirmationPct} color="#3b82f6" />
        </div>
        <div className="bg-card p-6 rounded-2xl border border-border shadow-sm md:col-span-1">
          <p className="text-xs font-bold uppercase tracking-wider text-muted-foreground mb-4">Human Override Rate</p>
          <RadialGauge value={data.human_override_rate ?? 0} color="#f59e0b" />
        </div>
      </div>

      <div className="grid grid-cols-2 lg:grid-cols-3 gap-4">
        {tiles.map((t) => (
          <div key={t.label} className="bg-card p-5 rounded-xl border border-border shadow-sm">
            <span className={`material-symbols-outlined ${t.color} ${t.bg} p-2 rounded-lg text-[20px]`}>{t.icon}</span>
            <p className="text-2xl font-black text-foreground mt-3 tabular-nums">{t.value}</p>
            <p className="text-xs font-semibold text-muted-foreground mt-0.5">{t.label}</p>
          </div>
        ))}
      </div>

      <div className="bg-primary/5 border border-primary/20 rounded-xl p-5 flex items-start gap-3">
        <span className="material-symbols-outlined text-primary mt-0.5">info</span>
        <p className="text-sm text-foreground/80">
          Every figure above is computed live from stored detections and assessor decisions — nothing here is a fixed claim about
          accuracy. As more claims move through the pipeline, these numbers move with them.
        </p>
      </div>
    </div>
  );
}

function RadialGauge({ value, color }: { value: number; color: string }) {
  const clamped = Math.max(0, Math.min(100, value));
  const circumference = 2 * Math.PI * 42;
  const offset = circumference - (clamped / 100) * circumference;
  return (
    <div className="flex items-center justify-center">
      <svg width="120" height="120" viewBox="0 0 100 100">
        <circle cx="50" cy="50" r="42" fill="none" stroke="currentColor" strokeWidth="10" className="text-muted" />
        <circle
          cx="50" cy="50" r="42" fill="none" stroke={color} strokeWidth="10" strokeLinecap="round"
          strokeDasharray={circumference} strokeDashoffset={offset}
          transform="rotate(-90 50 50)" style={{ transition: "stroke-dashoffset 0.6s ease" }}
        />
        <text x="50" y="55" textAnchor="middle" fontSize="20" fontWeight="900" fill="currentColor" className="text-foreground">
          {clamped.toFixed(1)}%
        </text>
      </svg>
    </div>
  );
}

function LoadingBlock({ label }: { label: string }) {
  return <div className="bg-card p-16 rounded-xl border border-border shadow-sm text-center text-muted-foreground animate-pulse">{label}</div>;
}
function EmptyBlock({ label }: { label: string }) {
  return <div className="bg-card p-16 rounded-xl border border-border shadow-sm text-center text-muted-foreground">{label}</div>;
}

/* ───────────────────────── ASSESSMENT REVIEW ───────────────────────── */

function daysSince(iso?: string): number | null {
  if (!iso) return null;
  const then = new Date(iso.replace(" ", "T")).getTime();
  if (Number.isNaN(then)) return null;
  return Math.max(0, Math.floor((Date.now() - then) / 86_400_000));
}
function fmtDateTime(iso?: string): string {
  if (!iso) return "—";
  const d = new Date(iso.replace(" ", "T"));
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString("en-GB", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" });
}
function fmtKES(n: number | null | undefined): string {
  if (n === null || n === undefined) return "—";
  return `KES ${Number(n).toLocaleString()}`;
}
const SEVERITY_META: Record<string, { dot: string; label: string }> = {
  severe: { dot: "bg-destructive", label: "Severe" },
  moderate: { dot: "bg-amber-500", label: "Moderate" },
  minor: { dot: "bg-yellow-400", label: "Minor" },
};

function AssessmentReviewTab({
  onGoToComms,
  reviewPrefill,
}: {
  onGoToComms: (opts: { claimId: string; recipientType?: string; instruction?: string }) => void;
  reviewPrefill?: ReviewPrefill | null;
}) {
  const [claimId, setClaimId] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [photos, setPhotos] = useState<ClaimPhoto[]>([]);
  const [results, setResults] = useState<any[]>([]);
  const [activePhotoIdx, setActivePhotoIdx] = useState(0);
  const [claimSummary, setClaimSummary] = useState<any>(null);
  const [confFilter, setConfFilter] = useState<"all" | "high" | "low">("all");
  const [zoom, setZoom] = useState(1);
  const [expandedFinding, setExpandedFinding] = useState<string | null>(null);
  const photoWrapRef = useRef<HTMLDivElement>(null);

  // Arriving back here via Communications' "Back" button -- reload the
  // same claim rather than leaving the tab empty (switching tabs unmounts
  // this component, so its previous state is gone).
  useEffect(() => {
    if (!reviewPrefill) return;
    loadClaim(reviewPrefill.claimId);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [reviewPrefill?.nonce]);

  const loadClaim = async (idOverride?: string) => {
    const id = (idOverride ?? claimId).trim();
    if (!id) return;
    setClaimId(id);
    setLoading(true);
    setError("");
    setConfFilter("all");
    setZoom(1);
    setExpandedFinding(null);
    try {
      // The curated /full-report endpoint deliberately strips out raw
      // per-photo detections/damage_zones into a fraud-summary shape --
      // the admin claim-details endpoint carries the raw analysis_result
      // those live in, so both are fetched: one for photos, one for the
      // richer risk/fraud context an admin needs that an assessor never sees.
      const [detailsRes, reportRes] = await Promise.all([getAdminClaimDetails(id), getClaimFullReport(id).catch(() => null)]);
      if (!detailsRes.success) {
        setError("Couldn't find this claim.");
        setPhotos([]);
        setResults([]);
        setClaimSummary(null);
        return;
      }
      let analysisResult: any = {};
      try {
        analysisResult = typeof detailsRes.claim_details?.analysis_result === "string"
          ? JSON.parse(detailsRes.claim_details.analysis_result)
          : detailsRes.claim_details?.analysis_result || {};
      } catch {
        analysisResult = {};
      }
      const photoResults = analysisResult?.photo_analysis?.results || [];
      if (!detailsRes.photos?.length) {
        setError("No photos found for this claim.");
        setPhotos([]);
        setResults([]);
      } else {
        setPhotos(detailsRes.photos);
        setResults(photoResults);
        setActivePhotoIdx(0);
      }
      setClaimSummary({
        claimDetails: detailsRes.claim_details,
        policyInfo: detailsRes.policy_info,
        report: reportRes,
        member: detailsRes.member_info,
        assignments: detailsRes.assignments,
        documents: detailsRes.documents,
        decisions: detailsRes.damage_decisions || [],
        businessRules: analysisResult?.business_rules,
        photoResults,
      });
    } catch {
      setError("Couldn't load this claim. Check the claim ID and try again.");
    } finally {
      setLoading(false);
    }
  };

  const activePhoto = photos[activePhotoIdx];
  const activeResult = activePhoto ? results.find((r: any) => r.filename === activePhoto.filename) : null;
  const detections = activeResult?.detections || [];
  const zones = activeResult?.damage_zones || [];

  // Every finding across the WHOLE claim (not just the active photo),
  // matched to its logged assessor decision by (filename, detection_index)
  // -- zones use the same +100 index offset the save flow and decision
  // trace both already use, so this is an exact match, not a fuzzy one.
  const decisions: any[] = claimSummary?.decisions || [];
  const findDecision = (filename: string, idx: number) =>
    decisions.find((d) => d.filename === filename && d.detection_index === idx);

  const allClaimFindings = (claimSummary?.photoResults || []).flatMap((r: any) => [
    ...(r.detections || []).map((d: any, i: number) => ({
      ...d, part: d.component, vehicle: null, source: "detector", filename: r.filename, detectionIndex: i,
      decision: findDecision(r.filename, i),
    })),
    ...(r.damage_zones || []).map((z: any, i: number) => ({
      ...z, component: z.part, source: "scan", filename: r.filename, detectionIndex: 100 + i,
      decision: findDecision(r.filename, 100 + i),
    })),
  ]);

  const activeFindings = [
    ...detections.map((d: any, i: number) => ({ ...d, part: d.component, vehicle: null, source: "detector", filename: activePhoto?.filename, detectionIndex: i, decision: findDecision(activePhoto?.filename || "", i) })),
    ...zones.map((z: any, i: number) => ({ ...z, component: z.part, source: "scan", filename: activePhoto?.filename, detectionIndex: 100 + i, decision: findDecision(activePhoto?.filename || "", 100 + i) })),
  ];
  const filteredActiveFindings = activeFindings.filter((f) => {
    if (confFilter === "high") return (f.confidence || 0) >= 0.7;
    if (confFilter === "low") return (f.confidence || 0) < 0.4;
    return true;
  });

  // Vehicle colors assigned deterministically per distinct vehicle name.
  const vehicleColors = ["bg-rose-500/10 text-rose-700", "bg-blue-500/10 text-blue-700", "bg-violet-500/10 text-violet-700", "bg-amber-500/10 text-amber-700"];
  const vehicleColorMap = new Map<string, string>();
  activeFindings.forEach((f: any) => {
    if (f.vehicle && !vehicleColorMap.has(f.vehicle)) vehicleColorMap.set(f.vehicle, vehicleColors[vehicleColorMap.size % vehicleColors.length]);
  });

  // AI vs Assessor, claim-wide.
  const withDecision = allClaimFindings.filter((f: any) => f.decision);
  const agreements = withDecision.filter((f: any) => f.decision.ai_recommendation === f.decision.assessor_decision);
  const disagreements = withDecision.filter((f: any) => f.decision.ai_recommendation !== f.decision.assessor_decision);
  const pending = allClaimFindings.length - withDecision.length;
  const agreementRate = withDecision.length ? Math.round((agreements.length / withDecision.length) * 100) : null;
  const avgConfidence = allClaimFindings.length
    ? Math.round((allClaimFindings.reduce((s: number, f: any) => s + (f.confidence || 0), 0) / allClaimFindings.length) * 100)
    : null;

  const claimDetails = claimSummary?.claimDetails || {};
  const policyInfo = claimSummary?.policyInfo;
  const report = claimSummary?.report;
  const fa = report?.final_assessment || {};
  const rb = report?.risk_breakdown || {};
  const businessRules = claimSummary?.businessRules;
  const findings: any[] = businessRules?.findings || [];

  // Financial impact -- every figure here is a real stored column, not a
  // recomputed estimate, so it can go stale relative to a re-analysis but
  // never drifts from what's actually in the database.
  const insuredValue = policyInfo?.sum_insured;
  const estimatedRepairCost = claimDetails.estimated_cost;
  const assessorEstimate = claimDetails.assessor_estimated_cost;
  const potentialTotalLossPct = insuredValue && estimatedRepairCost ? (estimatedRepairCost / insuredValue) * 100 : null;
  const variancePct = assessorEstimate && estimatedRepairCost ? ((assessorEstimate - estimatedRepairCost) / estimatedRepairCost) * 100 : null;

  // Recommended actions -- built deterministically from what actually
  // fired, never AI-generated free text, so it can't invent a step.
  const actions: string[] = [];
  if (findings.some((f) => f.type?.includes("sum_insured"))) actions.push("Review the insured value against current vehicle market valuation.");
  if (findings.some((f) => f.type?.includes("repeat_claimant"))) actions.push("Review the policyholder's recent claim history.");
  if (findings.some((f) => f.type?.includes("minimum_evidence"))) actions.push("Request additional vehicle photographs from the policyholder or assessor.");
  if (disagreements.length > 0) actions.push(`Review the ${disagreements.length} AI/assessor disagreement${disagreements.length > 1 ? "s" : ""} in the findings table below.`);
  if ((report?.fraud_indicators?.narrative_inconsistencies?.length || 0) > 0) actions.push("Confirm whether the reported accident circumstances align with the photographic evidence.");
  if (actions.length === 0) actions.push("No specific risk indicators were flagged -- standard review process applies.");

  // Evidence completeness -- only categories genuinely derivable from
  // stored data (no fabricated "front/rear photo" angle detection, since
  // the system doesn't classify photo angle).
  const evidenceItems = [
    { label: `Photographs (${photos.length} uploaded, 2 recommended)`, done: photos.length >= 2 },
    { label: "Accident narrative", done: !!claimDetails.narrative?.trim() },
    { label: "Policy information on file", done: !!policyInfo },
    { label: "Assessor repair estimate", done: assessorEstimate != null },
    { label: "Supporting documents", done: (claimSummary?.documents?.length || 0) > 0 },
  ];
  const evidenceCompletePct = Math.round((evidenceItems.filter((e) => e.done).length / evidenceItems.length) * 100);

  // Timeline -- only real, stored timestamps; anything not yet recorded is
  // simply not shown rather than invented.
  const assignment = claimSummary?.assignments?.[0];
  const earliestPhotoUpload = photos.length ? photos.reduce((min, p: any) => (!min || p.uploaded_at < min ? p.uploaded_at : min), "") : null;
  const timelineEvents = [
    claimDetails.created_at && { at: claimDetails.created_at, label: "Claim submitted", icon: "note_add" },
    assignment?.assigned_at && { at: assignment.assigned_at, label: `Assigned to ${assignment.assessor_name || assignment.assessor_id}`, icon: "person_add" },
    earliestPhotoUpload && { at: earliestPhotoUpload, label: `Vehicle photographs uploaded (${photos.length})`, icon: "add_a_photo" },
    report?.analysis_timestamp && { at: report.analysis_timestamp, label: "AI analysis completed", icon: "auto_awesome" },
    assignment?.report_submitted_at && { at: assignment.report_submitted_at, label: "Assessor completed assessment", icon: "fact_check" },
  ].filter(Boolean) as { at: string; label: string; icon: string }[];
  timelineEvents.sort((a, b) => a.at.localeCompare(b.at));

  const claimAge = daysSince(claimDetails.created_at);
  const decisionColor = fa?.decision === "APPROVE_CLAIM" ? "text-emerald-600 bg-emerald-500/10" : fa?.decision === "DECLINE_CLAIM" ? "text-destructive bg-destructive/10" : "text-amber-600 bg-amber-500/10";
  const topFactorSentence = (() => {
    const factors = [
      { label: "photo analysis", points: rb.photo_risk != null && rb.weights_applied?.photo != null ? rb.photo_risk * rb.weights_applied.photo : 0 },
      { label: "business rules", points: rb.business_rules_risk != null && rb.weights_applied?.business_rules != null ? rb.business_rules_risk * rb.weights_applied.business_rules : 0 },
      { label: "the claim narrative", points: rb.narrative_risk != null && rb.weights_applied?.narrative != null ? rb.narrative_risk * rb.weights_applied.narrative : 0 },
    ].sort((a, b) => b.points - a.points);
    const top = factors[0];
    return top && top.points > 0 ? `${top.points.toFixed(1)} of ${rb.overall_score} risk points come from ${top.label} -- currently the strongest reason this claim was flagged.` : null;
  })();

  const toggleFinding = (key: string) => setExpandedFinding((cur) => (cur === key ? null : key));

  return (
    <div className="space-y-6">
      <div className="flex items-center gap-3">
        <ClaimPicker value={claimId} onChange={setClaimId} onSelect={(id) => loadClaim(id)} placeholder="Search or pick a claim to review..." />
      </div>

      {loading && <p className="text-sm text-muted-foreground px-1 animate-pulse">Loading claim...</p>}
      {error && <p className="text-sm text-destructive font-semibold px-1">{error}</p>}

      {photos.length > 0 && claimSummary && (
        <>
          {/* 1. CLAIM INTELLIGENCE SUMMARY -- the 10-second read */}
          <div className="bg-card rounded-xl border border-border shadow-sm overflow-hidden">
            <div className="px-5 py-3.5 border-b border-border flex items-center justify-between">
              <p className="text-xs font-black uppercase tracking-wider text-muted-foreground">Claim Intelligence Summary</p>
              <Link to={`/admin/claim/${claimId}`} className="text-xs font-bold text-primary hover:underline flex items-center gap-1">
                Full claim report <span className="material-symbols-outlined text-[14px]">arrow_forward</span>
              </Link>
            </div>
            <div className="p-5 grid grid-cols-2 md:grid-cols-4 gap-5">
              <SummaryField label="Decision" value={fa.decision ? <span className={`inline-flex text-xs font-black uppercase px-2.5 py-1 rounded-full ${decisionColor}`}>{fa.decision.replace(/_/g, " ")}</span> : "—"} />
              <SummaryField label="Risk Score" value={<span className="text-lg font-black text-foreground tabular-nums">{fa.fraud_risk_score ?? "—"}<span className="text-xs font-normal text-muted-foreground">/100 — {fa.risk_level || "unknown"}</span></span>} />
              <SummaryField label="Claim Value" value={fmtKES(insuredValue)} />
              <SummaryField label="Estimated Damage" value={fmtKES(estimatedRepairCost)} />
              <SummaryField label="Assessor" value={assignment?.assessor_name || "Unassigned"} />
              <SummaryField label="Policyholder" value={claimSummary.member?.name || "Unknown"} />
              <SummaryField label="Claim Age" value={claimAge !== null ? `${claimAge} day${claimAge === 1 ? "" : "s"}` : "—"} />
              <SummaryField label="AI Confidence" value={avgConfidence !== null ? `${avgConfidence}%` : "—"} />
            </div>
            {topFactorSentence && (
              <div className="px-5 pb-5">
                <p className="text-[10px] font-bold uppercase text-muted-foreground mb-1.5">AI Recommendation</p>
                <p className="text-sm text-foreground/90 leading-relaxed">{topFactorSentence}</p>
              </div>
            )}
            <div className="px-5 pb-5 flex items-center gap-3">
              <Link to={`/admin/claim/${claimId}`} className="text-xs font-bold px-3.5 py-2 rounded-lg bg-primary text-primary-foreground hover:bg-primary/90 transition-colors">View Full Claim</Link>
              <button onClick={() => onGoToComms({ claimId })} className="text-xs font-bold px-3.5 py-2 rounded-lg border border-border hover:bg-muted transition-colors">Message a Party</button>
            </div>
          </div>

          {/* 2. AI vs ASSESSOR -- where to focus */}
          <div className="bg-card rounded-xl border border-border shadow-sm overflow-hidden">
            <div className="px-5 py-3.5 border-b border-border">
              <p className="text-xs font-black uppercase tracking-wider text-muted-foreground">AI vs Assessor</p>
            </div>
            <div className="p-5 flex flex-wrap items-center gap-6">
              <div>
                <p className="text-2xl font-black text-foreground tabular-nums">{allClaimFindings.length}</p>
                <p className="text-[10px] font-bold uppercase text-muted-foreground">AI Findings</p>
              </div>
              <div className="flex items-center gap-1.5"><span className="material-symbols-outlined text-emerald-600 text-[18px]">check_circle</span><span className="text-sm font-bold text-foreground">{agreements.length}</span><span className="text-xs text-muted-foreground">Agreements</span></div>
              <div className="flex items-center gap-1.5"><span className="material-symbols-outlined text-amber-600 text-[18px]">warning</span><span className="text-sm font-bold text-foreground">{disagreements.length}</span><span className="text-xs text-muted-foreground">Disagreements</span></div>
              <div className="flex items-center gap-1.5"><span className="material-symbols-outlined text-muted-foreground text-[18px]">help</span><span className="text-sm font-bold text-foreground">{pending}</span><span className="text-xs text-muted-foreground">Awaiting Decision</span></div>
              {agreementRate !== null && <div className="ml-auto text-right"><p className="text-lg font-black text-foreground">{agreementRate}%</p><p className="text-[10px] font-bold uppercase text-muted-foreground">Agreement Rate</p></div>}
            </div>
            {disagreements.length > 0 && (
              <div className="px-5 pb-5 pt-1 border-t border-border">
                <p className="text-[10px] font-bold uppercase text-amber-600 mb-2 mt-3">Attention Required</p>
                <ul className="space-y-1.5">
                  {disagreements.slice(0, 4).map((f: any, i: number) => (
                    <li key={i} className="text-sm text-foreground/90">
                      <b>{f.component}</b> — AI recommends <span className="font-semibold capitalize">{f.decision.ai_recommendation}</span>; assessor recommends <span className="font-semibold capitalize">{f.decision.assessor_decision}</span>.
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </div>

          {/* 3. FINANCIAL IMPACT */}
          {(insuredValue || estimatedRepairCost) && (
            <div className="bg-card rounded-xl border border-border shadow-sm overflow-hidden">
              <div className="px-5 py-3.5 border-b border-border flex items-center gap-2">
                <span className="material-symbols-outlined text-[18px] text-primary">payments</span>
                <p className="text-xs font-black uppercase tracking-wider text-muted-foreground">Assessment Financial Summary</p>
              </div>
              <div className="p-5 grid grid-cols-2 md:grid-cols-3 gap-5">
                <SummaryField label="Insured Value" value={fmtKES(insuredValue)} />
                <SummaryField label="Estimated Repair Cost" value={fmtKES(estimatedRepairCost)} />
                <SummaryField label="Potential Total Loss" value={potentialTotalLossPct !== null ? `${potentialTotalLossPct.toFixed(1)}%` : "—"} />
                <SummaryField label="Assessor Estimate" value={fmtKES(assessorEstimate)} />
                <SummaryField label="Variance vs AI/Initial Estimate" value={variancePct !== null ? `${variancePct >= 0 ? "+" : ""}${variancePct.toFixed(1)}%` : "—"} />
              </div>
              {potentialTotalLossPct !== null && potentialTotalLossPct >= 70 && (
                <div className="px-5 pb-5 flex items-start gap-2 text-xs text-amber-700 bg-amber-500/5 mx-5 mb-5 p-3 rounded-lg border border-amber-500/20">
                  <span className="material-symbols-outlined text-[16px] mt-0.5">warning</span>
                  <span>Estimated repair cost is approaching the insured value ({potentialTotalLossPct.toFixed(0)}%). A total-loss assessment may be warranted -- actual threshold should follow your organization's configured policy rules.</span>
                </div>
              )}
            </div>
          )}

          {/* 4. WHY THIS CLAIM WAS FLAGGED */}
          {findings.length > 0 && (
            <div className="bg-card rounded-xl border border-border shadow-sm overflow-hidden">
              <div className="px-5 py-3.5 border-b border-border flex items-center gap-2">
                <span className="material-symbols-outlined text-[18px] text-muted-foreground">search</span>
                <p className="text-xs font-black uppercase tracking-wider text-muted-foreground">Why This Claim Was Flagged</p>
              </div>
              <div className="p-5 space-y-3">
                {(["high", "medium", "low"] as const).map((tier) => {
                  const tierFindings = findings.filter((f) => (f.severity || "low") === tier);
                  if (tierFindings.length === 0) return null;
                  const meta = tier ==="high"? { icon:"", label:"High-impact findings", why:"This significantly increases the claim's risk profile and warrants investigation before proceeding."}
                    : tier ==="medium"? { icon:"", label:"Review required", why:"This does not indicate wrongdoing by itself, but the pattern warrants a closer look."}
                    : { icon:"", label:"Additional context", why:"This has a smaller effect on the risk score but may still be worth checking."};
                  return (
                    <div key={tier}>
                      <p className="text-xs font-bold text-foreground mb-2">{meta.icon} {meta.label}</p>
                      <div className="space-y-2">
                        {tierFindings.map((f, i) => (
                          <div key={i} className="pl-4 border-l-2 border-border">
                            <p className="text-sm text-foreground/90">{f.description}</p>
                            <p className="text-xs text-muted-foreground mt-0.5 italic">Why it matters: {meta.why}</p>
                          </div>
                        ))}
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
          )}

          {/* 5. RISK SCORE BREAKDOWN */}
          {rb.overall_score !== undefined && <RiskBreakdownBars rb={rb} />}

          {/* 6. RECOMMENDED ADMIN ACTION */}
          <div className="bg-card rounded-xl border border-primary/20 shadow-sm overflow-hidden">
            <div className="px-5 py-3.5 border-b border-border flex items-center gap-2 bg-primary/5">
              <span className="material-symbols-outlined text-[18px] text-primary">flag</span>
              <p className="text-xs font-black uppercase tracking-wider text-primary">Recommended Admin Action</p>
            </div>
            <div className="p-5 space-y-1.5">
              {actions.map((a, i) => (
                <div key={i} className="flex items-start gap-2 text-sm text-foreground/90">
                  <span className="material-symbols-outlined text-[16px] text-muted-foreground mt-0.5">check_box_outline_blank</span>
                  <span>{a}</span>
                </div>
              ))}
            </div>
            <div className="px-5 pb-5 flex items-center gap-3 flex-wrap">
              <Link to={`/admin/claim/${claimId}`} className="text-xs font-bold px-3.5 py-2 rounded-lg bg-primary text-primary-foreground hover:bg-primary/90 transition-colors">View Full Claim &amp; Record Decision</Link>
              <button
                onClick={() => onGoToComms({ claimId, recipientType: "member", instruction: "Politely request additional vehicle photographs and any missing supporting documents needed to complete the review of this claim." })}
                className="text-xs font-bold px-3.5 py-2 rounded-lg border border-border hover:bg-muted transition-colors"
              >
                Request More Evidence / Message a Party
              </button>
            </div>
          </div>

          {/* 7. EVIDENCE COMPLETENESS */}
          <div className="bg-card rounded-xl border border-border shadow-sm overflow-hidden">
            <div className="px-5 py-3.5 border-b border-border">
              <p className="text-xs font-black uppercase tracking-wider text-muted-foreground">Evidence Completeness</p>
            </div>
            <div className="p-5">
              <div className="flex items-center gap-3 mb-3">
                <div className="flex-1 h-2 bg-muted rounded-full overflow-hidden">
                  <div className={`h-full rounded-full ${evidenceCompletePct >= 80 ? "bg-emerald-500" : evidenceCompletePct >= 50 ? "bg-amber-500" : "bg-destructive"}`} style={{ width: `${evidenceCompletePct}%` }} />
                </div>
                <span className="text-sm font-black text-foreground tabular-nums">{evidenceCompletePct}%</span>
              </div>
              <div className="space-y-1.5">
                {evidenceItems.map((e, i) => (
                  <div key={i} className="flex items-center gap-2 text-sm">
                    <span className={`material-symbols-outlined text-[16px] ${e.done ? "text-emerald-600" : "text-destructive"}`}>{e.done ? "check_circle" : "cancel"}</span>
                    <span className={e.done ? "text-foreground/90" : "text-foreground/70"}>{e.label}</span>
                  </div>
                ))}
              </div>
              {evidenceCompletePct < 100 && <p className="text-xs text-amber-700 mt-3 flex items-center gap-1.5"><span className="material-symbols-outlined text-[14px]">warning</span>Additional evidence recommended before a final decision.</p>}
            </div>
          </div>

          {/* 8. TIMELINE */}
          {timelineEvents.length > 0 && (
            <div className="bg-card rounded-xl border border-border shadow-sm overflow-hidden">
              <div className="px-5 py-3.5 border-b border-border">
                <p className="text-xs font-black uppercase tracking-wider text-muted-foreground">Assessment Timeline</p>
              </div>
              <div className="p-5">
                <div className="flex flex-col">
                  {timelineEvents.map((e, i) => (
                    <div key={i} className="flex items-start gap-3">
                      <div className="flex flex-col items-center">
                        <span className="flex items-center justify-center size-7 rounded-full bg-primary/10 text-primary"><span className="material-symbols-outlined text-[15px]">{e.icon}</span></span>
                        {i < timelineEvents.length - 1 && <span className="w-px flex-1 bg-border min-h-[16px]" />}
                      </div>
                      <div className="pb-4">
                        <p className="text-xs text-muted-foreground">{fmtDateTime(e.at)}</p>
                        <p className="text-sm font-semibold text-foreground">{e.label}</p>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            </div>
          )}

          {/* 9. EVIDENCE -- photo viewer */}
          <div className="flex gap-2 overflow-x-auto pb-1">
            {photos.map((p, i) => (
              <button
                key={p.id}
                onClick={() => { setActivePhotoIdx(i); setZoom(1); }}
                className={`shrink-0 rounded-lg overflow-hidden border-2 transition-all ${i === activePhotoIdx ? "border-primary" : "border-transparent opacity-70 hover:opacity-100"}`}
              >
                <img src={`${BASE_URL}/api/analysis/photos/${p.id}/file`} alt={p.filename} className="size-16 object-cover" />
              </button>
            ))}
          </div>

          <div className="flex items-center gap-2 flex-wrap">
            <div className="flex items-center gap-1 bg-card border border-border rounded-lg p-1">
              <button onClick={() => setZoom((z) => Math.max(1, z - 0.5))} className="p-1.5 rounded hover:bg-muted" title="Zoom out"><span className="material-symbols-outlined text-[16px]">zoom_out</span></button>
              <span className="text-xs font-bold text-foreground w-10 text-center">{Math.round(zoom * 100)}%</span>
              <button onClick={() => setZoom((z) => Math.min(3, z + 0.5))} className="p-1.5 rounded hover:bg-muted" title="Zoom in"><span className="material-symbols-outlined text-[16px]">zoom_in</span></button>
            </div>
            <button onClick={() => photoWrapRef.current?.requestFullscreen?.().catch(() => {})} className="flex items-center gap-1.5 px-3 py-1.5 bg-card border border-border rounded-lg text-xs font-bold hover:bg-muted transition-colors">
              <span className="material-symbols-outlined text-[16px]">fullscreen</span>Fullscreen
            </button>
            {activePhoto && (
              <a href={`${BASE_URL}/api/analysis/photos/${activePhoto.id}/file`} download={activePhoto.filename} className="flex items-center gap-1.5 px-3 py-1.5 bg-card border border-border rounded-lg text-xs font-bold hover:bg-muted transition-colors">
                <span className="material-symbols-outlined text-[16px]">download</span>Download
              </a>
            )}
            <div className="flex items-center gap-1 ml-auto">
              {(["all", "high", "low"] as const).map((f) => (
                <button
                  key={f}
                  onClick={() => setConfFilter(f)}
                  className={`px-3 py-1.5 rounded-lg text-xs font-bold transition-colors ${confFilter === f ? "bg-primary text-primary-foreground" : "bg-card border border-border hover:bg-muted"}`}
                >
                  {f === "all" ? `All (${activeFindings.length})` : f === "high" ? "High Confidence" : "Low Confidence"}
                </button>
              ))}
            </div>
          </div>

          <div ref={photoWrapRef} className="grid grid-cols-1 lg:grid-cols-2 gap-6 bg-background">
            <div className="bg-card rounded-xl border border-border shadow-sm overflow-hidden">
              <div className="px-4 py-3 border-b border-border">
                <p className="text-xs font-black uppercase tracking-wider text-muted-foreground">Original Photograph</p>
              </div>
              {activePhoto && (
                <div className="overflow-auto max-h-[600px]">
                  <img src={`${BASE_URL}/api/analysis/photos/${activePhoto.id}/file`} alt="original" className="w-full h-auto origin-top-left" style={{ transform: `scale(${zoom})` }} />
                </div>
              )}
            </div>

            <div className="bg-card rounded-xl border border-border shadow-sm overflow-hidden">
              <div className="px-4 py-3 border-b border-border flex items-center justify-between">
                <p className="text-xs font-black uppercase tracking-wider text-primary">AI Analysis</p>
                <span className="text-[10px] font-bold text-muted-foreground">{filteredActiveFindings.length} finding(s)</span>
              </div>
              {activePhoto && (
                <div className="overflow-auto max-h-[600px]">
                  <div style={{ transform: `scale(${zoom})`, transformOrigin: "top left" }}>
                    <AnnotatedPhoto photoId={activePhoto.id} findings={filteredActiveFindings} />
                  </div>
                </div>
              )}
              <div className="px-4 py-2.5 border-t border-border flex items-center gap-3 flex-wrap text-[10px] font-semibold text-muted-foreground">
                <span className="flex items-center gap-1"><span className="size-2 rounded-full bg-destructive" />Severe / Replace</span>
                <span className="flex items-center gap-1"><span className="size-2 rounded-full bg-amber-500" />Moderate / Repair</span>
                <span className="flex items-center gap-1"><span className="size-2 rounded-full bg-muted-foreground/40 border border-muted-foreground" />Low confidence</span>
              </div>
            </div>
          </div>

          {/* 10. RESTRUCTURED FINDINGS TABLE */}
          <div className="bg-card rounded-xl border border-border shadow-sm overflow-hidden">
            <div className="px-4 py-3 border-b border-border">
              <p className="text-xs font-black uppercase tracking-wider text-muted-foreground">Structured Findings</p>
            </div>
            {filteredActiveFindings.length === 0 && <p className="px-4 py-8 text-center text-sm text-muted-foreground">No findings match this filter.</p>}
            <div className="divide-y divide-border">
              {filteredActiveFindings.map((f: any, i: number) => {
                const key = `${f.filename}-${f.detectionIndex}`;
                const isOpen = expandedFinding === key;
                const lowConf = (f.confidence || 0) < 0.4;
                const sevMeta = SEVERITY_META[(f.severity || "moderate").toLowerCase()] || SEVERITY_META.moderate;
                const agreed = f.decision && f.decision.ai_recommendation === f.decision.assessor_decision;
                return (
                  <div key={key}>
                    <button onClick={() => toggleFinding(key)} className="w-full text-left px-4 py-3 flex flex-wrap items-center gap-x-4 gap-y-1.5 hover:bg-muted/30 transition-colors">
                      {f.vehicle ? (
                        <span className={`text-[9px] font-black uppercase px-1.5 py-0.5 rounded w-20 shrink-0 text-center truncate ${vehicleColorMap.get(f.vehicle)}`} title={f.vehicle}>{f.vehicle}</span>
                      ) : (
                        <span className="text-[9px] font-bold uppercase text-muted-foreground/50 w-20 shrink-0 text-center">—</span>
                      )}
                      <span className="text-sm font-bold text-foreground w-40 shrink-0">{f.component}</span>
                      <span className="flex items-center gap-1.5 text-xs text-muted-foreground w-32 shrink-0"><span className={`size-2 rounded-full ${sevMeta.dot}`} />{sevMeta.label}</span>
                      <span className={`text-xs font-bold tabular-nums w-16 shrink-0 ${lowConf ? "text-amber-600" : "text-foreground"}`}>{Math.round((f.confidence || 0) * 100)}%</span>
                      <span className={`text-[10px] font-black uppercase px-2 py-0.5 rounded shrink-0 ${f.recommended_action === "replace" ? "bg-destructive/10 text-destructive" : "bg-amber-500/10 text-amber-700"}`}>{f.recommended_action}</span>
                      {f.decision ? (
                        <span className={`text-[10px] font-black uppercase px-2 py-0.5 rounded shrink-0 flex items-center gap-1 ${agreed ? "bg-emerald-500/10 text-emerald-600" : "bg-amber-500/10 text-amber-700"}`}>
                          <span className="material-symbols-outlined text-[12px]">{agreed ? "check" : "priority_high"}</span>{agreed ? "Agree" : "Review"}
                        </span>
                      ) : (
                        <span className="text-[10px] font-bold uppercase text-muted-foreground/60 shrink-0">Pending</span>
                      )}
                      {lowConf && <span className="text-[10px] font-black uppercase px-2 py-0.5 rounded bg-amber-500/10 text-amber-700 flex items-center gap-1 shrink-0"><span className="material-symbols-outlined text-[12px]">warning</span>Low Confidence</span>}
                      <span className="material-symbols-outlined text-[16px] text-muted-foreground ml-auto shrink-0">{isOpen ? "expand_less" : "expand_more"}</span>
                    </button>
                    {isOpen && (
                      <div className="px-4 pb-4 pt-1 bg-muted/20 space-y-3">
                        {lowConf && (
                          <div className="flex items-start gap-2 text-xs text-amber-700 bg-amber-500/10 p-3 rounded-lg border border-amber-500/20">
                            <span className="material-symbols-outlined text-[16px] mt-0.5">warning</span>
                            <span>AI detected potential damage but confidence is low ({Math.round((f.confidence || 0) * 100)}%). Human confirmation recommended before acting on this recommendation.</span>
                          </div>
                        )}
                        <div>
                          <p className="text-[10px] font-bold uppercase text-muted-foreground mb-1">What AI Detected</p>
                          <p className="text-sm text-foreground/90">{f.description || `${f.damage_type || "Damage"} detected on ${f.component}.`}</p>
                        </div>
                        <div>
                          <p className="text-[10px] font-bold uppercase text-muted-foreground mb-1">Why AI Recommends {f.recommended_action === "replace" ? "Replacement" : "Repair"}</p>
                          <p className="text-sm text-foreground/90">
                            {f.recommended_action === "replace"
                              ? "The observed damage type/severity is significant enough that replacement should be considered rather than cosmetic repair."
                              : "The observed damage appears limited in extent, consistent with a standard repair rather than full replacement."}
                          </p>
                        </div>
                        <div className="flex items-center gap-4 text-xs text-muted-foreground">
                          <span>Evidence: <b className="text-foreground">{f.source === "scan" ? "Whole-photo scan" : "Detector"}</b></span>
                          {f.decision && <span>Assessor's assessment: <b className="text-foreground capitalize">{f.decision.assessor_decision}</b></span>}
                          {f.decision && <span className={agreed ?"text-emerald-600 font-bold":"text-amber-700 font-bold"}>{agreed ?"Agreement":"Disagreement"}</span>}
                        </div>
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          </div>
        </>
      )}

      {photos.length === 0 && !error && !loading && (
        <EmptyBlock label="Search or pick a claim above to review its AI photo analysis side by side." />
      )}
    </div>
  );
}

function SummaryField({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div>
      <p className="text-[10px] font-bold uppercase text-muted-foreground mb-1">{label}</p>
      <div className="text-sm font-semibold text-foreground">{value}</div>
    </div>
  );
}

function RiskBreakdownBars({ rb }: { rb: any }) {
  const META = [
    { key: "photo_analysis", rbField: "photo_risk", weightKey: "photo", label: "Photo Evidence", icon: "photo_camera" },
    { key: "business_rules", rbField: "business_rules_risk", weightKey: "business_rules", label: "Business Rules", icon: "rule" },
    { key: "narrative_analysis", rbField: "narrative_risk", weightKey: "narrative", label: "Claim Narrative", icon: "description" },
    { key: "amount_based", rbField: "amount_risk", weightKey: "amount", label: "Claim Amount", icon: "payments" },
    { key: "location_based", rbField: "location_risk", weightKey: "location", label: "Location", icon: "location_on" },
    { key: "historical_patterns", rbField: "historical_risk", weightKey: "historical", label: "Historical Pattern", icon: "history" },
  ];
  const factors = META.map((m) => {
    const weight = rb.weights_applied?.[m.weightKey];
    const raw = rb[m.rbField];
    if (weight == null || raw == null) return null;
    return { ...m, points: raw * weight, max: weight * 100 };
  }).filter((f): f is NonNullable<typeof f> => f !== null).sort((a, b) => b.points - a.points);
  const maxPoints = Math.max(1, ...factors.map((f) => f.max));

  return (
    <div className="bg-card rounded-xl border border-border shadow-sm overflow-hidden">
      <div className="px-5 py-3.5 border-b border-border flex items-center justify-between">
        <p className="text-xs font-black uppercase tracking-wider text-muted-foreground">Risk Score Breakdown</p>
        <span className={`text-sm font-black tabular-nums ${rb.overall_score >= 70 ? "text-destructive" : rb.overall_score >= 50 ? "text-amber-600" : "text-primary"}`}>{rb.overall_score} / 100 — {rb.overall_score >= 70 ? "HIGH RISK" : rb.overall_score >= 50 ? "MEDIUM RISK" : "LOW RISK"}</span>
      </div>
      <div className="p-5 space-y-2.5">
        {factors.map((f) => (
          <div key={f.key} className="flex items-center gap-3">
            <span className="material-symbols-outlined text-[16px] text-muted-foreground w-5 shrink-0">{f.icon}</span>
            <span className="text-xs font-semibold text-foreground w-32 shrink-0 truncate">{f.label}</span>
            <div className="flex-1 h-2.5 bg-muted rounded-full overflow-hidden">
              <div className="h-full rounded-full bg-primary" style={{ width: `${(f.points / maxPoints) * 100}%` }} />
            </div>
            <span className="text-xs font-bold text-foreground tabular-nums w-12 text-right shrink-0">{f.points.toFixed(1)}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

function AnnotatedPhoto({ photoId, findings }: { photoId: number; findings: any[] }) {
  // Detector bboxes are stored in raw original-image pixel coordinates, not
  // a fraction -- they need the image's actual natural dimensions to
  // convert into the SVG's 0-100 viewBox space (same pattern DamagePanel
  // already uses correctly). Whole-photo-scan zones are pre-normalized
  // fractions and need no such conversion.
  const [naturalSize, setNaturalSize] = useState<{ w: number; h: number } | null>(null);
  return (
    <div className="relative inline-block w-full">
      <img
        src={`${BASE_URL}/api/analysis/photos/${photoId}/file`}
        alt="annotated"
        className="w-full h-auto block"
        onLoad={(e) => {
          const img = e.currentTarget;
          setNaturalSize({ w: img.naturalWidth, h: img.naturalHeight });
        }}
      />
      {naturalSize && (
        <svg className="absolute inset-0 w-full h-full pointer-events-none" viewBox="0 0 100 100" preserveAspectRatio="none">
          {findings.map((f: any, i: number) => {
            const lowConf = (f.confidence || 0) < 0.4;
            const color = lowConf ? "#9ca3af" : f.recommended_action === "replace" ? "#ef4444" : "#f59e0b";
            if (f.source === "detector" && f.bbox) {
              const [x1, y1, x2, y2] = f.bbox;
              const px1 = (x1 / naturalSize.w) * 100, px2 = (x2 / naturalSize.w) * 100;
              const py1 = (y1 / naturalSize.h) * 100, py2 = (y2 / naturalSize.h) * 100;
              return <rect key={i} x={px1} y={py1} width={px2 - px1} height={py2 - py1} rx={1} fill={color} fillOpacity={0.18} stroke={color} strokeWidth={0.6} vectorEffect="non-scaling-stroke" />;
            }
            if (f.bbox_normalized) {
              const [x1, y1, x2, y2] = f.bbox_normalized.map((v: number) => v * 100);
              return <rect key={i} x={x1} y={y1} width={x2 - x1} height={y2 - y1} rx={1} fill={color} fillOpacity={0.18} stroke={color} strokeWidth={0.6} strokeDasharray="2 1.5" vectorEffect="non-scaling-stroke" />;
            }
            return null;
          })}
        </svg>
      )}
    </div>
  );
}

/* ───────────────────────── DECISION TRACE ───────────────────────── */

function DecisionTraceTab() {
  const [claimId, setClaimId] = useState("");
  const [loading, setLoading] = useState(false);
  const [traces, setTraces] = useState<DecisionTrace[]>([]);
  const [error, setError] = useState("");
  const [expandedIdx, setExpandedIdx] = useState<number | null>(null);
  const [filter, setFilter] = useState<"all" | "review">("all");

  const load = async (idOverride?: string) => {
    const id = (idOverride ?? claimId).trim();
    if (!id) return;
    setLoading(true);
    setError("");
    setExpandedIdx(null);
    try {
      const res = await getAiDecisionTrace(id);
      if (!res.success || !res.traces?.length) {
        setError("No AI findings recorded for this claim.");
        setTraces([]);
      } else {
        setTraces(res.traces);
      }
    } catch {
      setError("Couldn't load the decision trace for this claim.");
    } finally {
      setLoading(false);
    }
  };

  const needsReview = traces.filter((t) => t.low_confidence || !t.assessor_decision).length;
  const avgConfidence = traces.length ? Math.round((traces.reduce((s, t) => s + (t.confidence || 0), 0) / traces.length) * 100) : 0;
  const visible = filter === "review" ? traces.filter((t) => t.low_confidence || !t.assessor_decision) : traces;

  return (
    <div className="space-y-6">
      <div className="flex items-center gap-3">
        <ClaimPicker value={claimId} onChange={setClaimId} onSelect={(id) => load(id)} placeholder="Search or pick a claim to trace..." />
      </div>

      {loading && <p className="text-sm text-muted-foreground px-1 animate-pulse">Loading trace...</p>}
      {error && <p className="text-sm text-destructive font-semibold px-1">{error}</p>}

      {traces.length > 0 && (
        <>
          {/* At-a-glance summary -- the whole point is the admin shouldn't have
              to read every step of every finding to know where to look first */}
          <div className="grid grid-cols-3 gap-4">
            <div className="bg-card p-4 rounded-xl border border-border shadow-sm">
              <p className="text-2xl font-black text-foreground tabular-nums">{traces.length}</p>
              <p className="text-xs font-semibold text-muted-foreground">AI findings on this claim</p>
            </div>
            <button
              onClick={() => setFilter(filter === "review" ? "all" : "review")}
              className={`p-4 rounded-xl border shadow-sm text-left transition-colors ${filter === "review" ? "border-amber-500 bg-amber-500/5" : "border-border bg-card"}`}
            >
              <p className="text-2xl font-black text-amber-600 tabular-nums">{needsReview}</p>
              <p className="text-xs font-semibold text-muted-foreground">Need review {filter === "review" ? "(showing)" : "· click to filter"}</p>
            </button>
            <div className="bg-card p-4 rounded-xl border border-border shadow-sm">
              <p className="text-2xl font-black text-foreground tabular-nums">{avgConfidence}%</p>
              <p className="text-xs font-semibold text-muted-foreground">Average AI confidence</p>
            </div>
          </div>

          {/* Compact rows -- one line per finding, expand for the full pipeline */}
          <div className="bg-card rounded-xl border border-border shadow-sm overflow-hidden divide-y divide-border">
            {visible.map((t, i) => {
              const isOpen = expandedIdx === i;
              return (
                <div key={i}>
                  <button
                    onClick={() => setExpandedIdx(isOpen ? null : i)}
                    className="w-full px-5 py-3.5 flex items-center gap-4 text-left hover:bg-muted/30 transition-colors"
                  >
                    <span className={`material-symbols-outlined text-[18px] shrink-0 ${t.low_confidence ? "text-amber-600" : "text-emerald-600"}`}>
                      {t.low_confidence ? "warning" : "check_circle"}
                    </span>
                    <div className="min-w-0 flex-1">
                      <p className="text-sm font-bold text-foreground truncate">{t.component}</p>
                      <p className="text-[10px] text-muted-foreground truncate">{t.filename} · {t.source === "scan" ? "whole-photo scan" : "detector"}</p>
                    </div>
                    <StagePipeline steps={t.steps} compact />
                    <span className={`shrink-0 text-[10px] font-black uppercase px-2.5 py-1 rounded-full ${t.recommended_action === "replace" ? "bg-destructive/10 text-destructive" : "bg-amber-500/10 text-amber-700"}`}>
                      {t.recommended_action}
                    </span>
                    <span className="shrink-0 text-xs font-bold text-foreground tabular-nums w-10 text-right">{Math.round((t.confidence || 0) * 100)}%</span>
                    {t.assessor_decision ? (
                      <span className="shrink-0 text-[10px] font-bold uppercase text-emerald-600 flex items-center gap-1"><span className="material-symbols-outlined text-[14px]">how_to_reg</span>{t.assessor_decision}</span>
                    ) : (
                      <span className="shrink-0 text-[10px] font-bold uppercase text-muted-foreground">Pending</span>
                    )}
                    <span className="material-symbols-outlined text-[18px] text-muted-foreground shrink-0">{isOpen ? "expand_less" : "expand_more"}</span>
                  </button>
                  {isOpen && (
                    <div className="px-5 pb-5 pt-1 bg-muted/20">
                      <StagePipeline steps={t.steps} />
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </>
      )}

      {traces.length === 0 && !error && !loading && (
        <EmptyBlock label="Search or pick a claim above to see the AI's step-by-step reasoning for each damage finding." />
      )}
    </div>
  );
}

/** Horizontal pipeline of pill stages -- compact=true renders a tiny dot
 * row for the collapsed table view, otherwise a full labeled row like a
 * repair-stage tracker. Reading left-to-right beats scanning a tall
 * vertical checklist per finding when a claim has a dozen findings. */
function StagePipeline({ steps, compact = false }: { steps: { label: string; done: boolean; detail?: string }[]; compact?: boolean }) {
  if (compact) {
    return (
      <div className="hidden md:flex items-center gap-1 shrink-0">
        {steps.map((s, i) => (
          <span key={i} title={s.label} className={`size-1.5 rounded-full ${s.done ? "bg-emerald-500" : "bg-muted"}`} />
        ))}
      </div>
    );
  }
  return (
    <div className="flex items-stretch gap-1 overflow-x-auto pb-1">
      {steps.map((s, i) => (
        <div key={i} className="flex items-center">
          <div className={`shrink-0 rounded-lg px-3 py-2 min-w-[108px] text-center border ${s.done ? "bg-emerald-500/10 border-emerald-500/30" : "bg-muted border-border"}`}>
            <p className={`text-[9px] font-black uppercase tracking-wide ${s.done ? "text-emerald-700" : "text-muted-foreground"}`}>{s.label}</p>
            {s.detail && <p className="text-[10px] font-bold text-foreground mt-0.5 truncate">{s.detail}</p>}
          </div>
          {i < steps.length - 1 && <span className="material-symbols-outlined text-[16px] text-border mx-0.5 shrink-0">chevron_right</span>}
        </div>
      ))}
    </div>
  );
}

/* ───────────────────────── AI vs ASSESSOR ───────────────────────── */

function ComparisonTab() {
  const [data, setData] = useState<AiComparisonData | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    getAiVsAssessorComparison().then((d) => { setData(d); setLoading(false); }).catch(() => setLoading(false));
  }, []);

  if (loading) return <LoadingBlock label="Loading AI vs assessor comparison..." />;
  if (!data || !data.success) return <EmptyBlock label="Couldn't load comparison data." />;
  if (data.total_decisions === 0) {
    return <EmptyBlock label="No assessor decisions logged yet — this fills in as assessors confirm or override AI findings." />;
  }

  const maxDisagreement = Math.max(1, ...data.disagreement_by_component.map((c) => c.disagreement_rate));

  return (
    <div className="space-y-6">
      <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
        <div className="bg-card p-6 rounded-2xl border border-border shadow-sm md:col-span-1 flex flex-col items-center">
          <p className="text-xs font-bold uppercase tracking-wider text-muted-foreground mb-4">AI Agreement Rate</p>
          <RadialGauge value={data.agreement_rate ?? 0} color="#22c55e" />
          <p className="text-xs text-muted-foreground mt-3">{data.total_decisions} logged decisions</p>
        </div>

        <div className="bg-card p-6 rounded-2xl border border-border shadow-sm md:col-span-2">
          <p className="text-sm font-bold text-foreground mb-1">Disagreement by Component</p>
          <p className="text-xs text-muted-foreground mb-4">Where the AI and assessors diverge most often — tells you where the model still needs human eyes.</p>
          <div className="space-y-3">
            {data.disagreement_by_component.map((c) => (
              <div key={c.component} className="flex items-center gap-3">
                <span className="text-xs font-semibold text-foreground w-32 shrink-0 truncate">{c.component}</span>
                <div className="flex-1 h-2.5 bg-muted rounded-full overflow-hidden">
                  <div className="h-full bg-amber-500 rounded-full" style={{ width: `${(c.disagreement_rate / maxDisagreement) * 100}%` }} />
                </div>
                <span className="text-xs font-bold text-foreground tabular-nums w-14 text-right">{c.disagreement_rate}%</span>
                <span className="text-[10px] text-muted-foreground w-16 text-right">({c.total} total)</span>
              </div>
            ))}
          </div>
        </div>
      </div>

      <div className="bg-card rounded-xl border border-border shadow-sm overflow-hidden">
        <div className="px-5 py-3.5 border-b border-border">
          <p className="text-sm font-bold text-foreground">Recent AI ↔ Assessor Decisions</p>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-left border-collapse">
            <thead>
              <tr className="bg-muted/50">
                <th className="px-5 py-2.5 text-[10px] font-black uppercase text-muted-foreground">Claim</th>
                <th className="px-5 py-2.5 text-[10px] font-black uppercase text-muted-foreground">Component</th>
                <th className="px-5 py-2.5 text-[10px] font-black uppercase text-muted-foreground">AI</th>
                <th className="px-5 py-2.5 text-[10px] font-black uppercase text-muted-foreground">Assessor</th>
                <th className="px-5 py-2.5 text-[10px] font-black uppercase text-muted-foreground">Outcome</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {data.recent_decisions.map((d, i) => (
                <tr key={i} className="hover:bg-muted/30">
                  <td className="px-5 py-2.5 text-xs font-bold text-primary whitespace-nowrap">{d.claim_id}</td>
                  <td className="px-5 py-2.5 text-xs text-foreground whitespace-nowrap">{d.component}</td>
                  <td className="px-5 py-2.5 text-xs text-muted-foreground capitalize whitespace-nowrap">{d.ai_recommendation}</td>
                  <td className="px-5 py-2.5 text-xs text-muted-foreground capitalize whitespace-nowrap">{d.assessor_decision}</td>
                  <td className="px-5 py-2.5 whitespace-nowrap">
                    <span className={`text-[10px] font-black uppercase px-2 py-0.5 rounded-full ${d.agreed ? "bg-emerald-500/10 text-emerald-600" : "bg-destructive/10 text-destructive"}`}>
                      {d.agreed ? "Confirmed" : "Overridden"}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

/* ───────────────────────── RISK & FRAUD CENTRE ───────────────────────── */

const TIER_META: Record<string, { icon: string; color: string; bg: string; border: string; label: string }> = {
  critical: { icon: "error", color: "text-destructive", bg: "bg-destructive/5", border: "border-destructive/30", label: "Critical" },
  review: { icon: "warning", color: "text-amber-600", bg: "bg-amber-500/5", border: "border-amber-500/30", label: "Review" },
  normal: { icon: "check_circle", color: "text-emerald-600", bg: "bg-emerald-500/5", border: "border-emerald-500/30", label: "Normal" },
};

function RiskFraudTab() {
  const [data, setData] = useState<{ total_flagged: number; critical_count: number; review_count: number; queue: RiskQueueClaim[] } | null>(null);
  const [loading, setLoading] = useState(true);
  const [tierFilter, setTierFilter] = useState<string>("");

  useEffect(() => {
    getAiRiskFraudQueue(50).then((d) => { setData(d); setLoading(false); }).catch(() => setLoading(false));
  }, []);

  if (loading) return <LoadingBlock label="Loading risk queue..." />;
  if (!data) return <EmptyBlock label="Couldn't load the risk & fraud queue." />;

  const filtered = tierFilter ? data.queue.filter((q) => q.tier === tierFilter) : data.queue;

  return (
    <div className="space-y-6">
      <div className="bg-amber-500/5 border border-amber-500/20 rounded-xl p-4 flex items-start gap-3">
        <span className="material-symbols-outlined text-amber-600 mt-0.5">info</span>
        <p className="text-sm text-foreground/80">
          These are risk indicators requiring human review — never an automatic fraud determination. Every flag below traces back
          to a specific, explainable business rule.
        </p>
      </div>

      <div className="grid grid-cols-3 gap-4">
        {(["critical", "review", "normal"] as const).map((tier) => {
          const meta = TIER_META[tier];
          const count = tier === "critical" ? data.critical_count : tier === "review" ? data.review_count : data.queue.filter((q) => q.tier === "normal").length;
          return (
            <button
              key={tier}
              onClick={() => setTierFilter(tierFilter === tier ? "" : tier)}
              className={`p-4 rounded-xl border text-left transition-all ${meta.bg} ${tierFilter === tier ? meta.border : "border-border"}`}
            >
              <span className={`material-symbols-outlined ${meta.color}`}>{meta.icon}</span>
              <p className="text-2xl font-black text-foreground mt-2 tabular-nums">{count}</p>
              <p className="text-xs font-bold uppercase text-muted-foreground">{meta.label}</p>
            </button>
          );
        })}
      </div>

      <div className="space-y-3">
        {filtered.length === 0 && <EmptyBlock label="No claims in this tier." />}
        {filtered.map((q) => {
          const meta = TIER_META[q.tier];
          return (
            <div key={q.claim_id} className={`bg-card rounded-xl border shadow-sm overflow-hidden ${meta.border}`}>
              <Link
                to={`/admin/claim/${q.claim_id}`}
                className={`px-5 py-3 flex items-center justify-between ${meta.bg} hover:brightness-95 transition-all group`}
              >
                <div className="flex items-center gap-2.5">
                  <span className={`material-symbols-outlined ${meta.color}`}>{meta.icon}</span>
                  <span className="text-sm font-black text-foreground group-hover:underline">{q.claim_id}</span>
                  <span className={`text-[10px] font-black uppercase px-2 py-0.5 rounded-full ${meta.color} ${meta.bg} border ${meta.border}`}>{meta.label}</span>
                </div>
                <div className="flex items-center gap-4 text-xs text-muted-foreground">
                  {q.risk_score !== null && <span>Risk score: <b className="text-foreground">{q.risk_score}</b></span>}
                  <span>KES {Number(q.estimated_cost || 0).toLocaleString()}</span>
                  <span className="material-symbols-outlined text-[16px] opacity-0 group-hover:opacity-100 transition-opacity">chevron_right</span>
                </div>
              </Link>
              <ul className="px-5 py-3 space-y-1.5">
                {q.indicators.map((ind, i) => (
                  <li key={i} className="text-sm text-foreground/80 flex items-start gap-2">
                    <span className="text-muted-foreground mt-1">•</span>
                    <span>{ind.description}</span>
                  </li>
                ))}
              </ul>
              <div className="px-5 pb-4">
                <Link to={`/admin/claim/${q.claim_id}`} className="text-xs font-bold text-primary hover:underline inline-flex items-center gap-1">
                  View full claim <span className="material-symbols-outlined text-[14px]">arrow_forward</span>
                </Link>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

/* ───────────────────────── AI COMMUNICATIONS ───────────────────────── */

const RECIPIENT_META: Record<string, { label: string; icon: string }> = {
  member: { label: "Policyholder", icon: "person" },
  assessor: { label: "Assessor", icon: "engineering" },
  analyst: { label: "Claims Analyst", icon: "support_agent" },
  repair_shop: { label: "Repair Shop", icon: "build" },
};

function CommunicationsTab({ prefill, onBackToClaim }: { prefill?: CommsPrefill | null; onBackToClaim: (claimId: string) => void }) {
  const [claimId, setClaimId] = useState("");
  const [loadingClaim, setLoadingClaim] = useState(false);
  const [error, setError] = useState("");
  const [recipients, setRecipients] = useState<ClaimRecipient[]>([]);
  const [selected, setSelected] = useState<ClaimRecipient | null>(null);
  const [manualEmail, setManualEmail] = useState("");

  const [instruction, setInstruction] = useState("");
  const [drafting, setDrafting] = useState(false);
  const [subject, setSubject] = useState("");
  const [body, setBody] = useState("");
  const [drafted, setDrafted] = useState(false);

  const [sending, setSending] = useState(false);
  const [sendResult, setSendResult] = useState<"success" | "error" | null>(null);

  const loadClaim = async (idOverride?: string, autoSelectType?: string, autoInstruction?: string) => {
    const id = (idOverride ?? claimId).trim();
    if (!id) return;
    setLoadingClaim(true);
    setError("");
    setSelected(null);
    setSubject("");
    setBody("");
    setDrafted(false);
    setSendResult(null);
    if (autoInstruction) setInstruction(autoInstruction);
    try {
      const res = await getClaimRecipients(id);
      if (!res.success || !res.recipients?.length) {
        setError("No contactable parties found for this claim.");
        setRecipients([]);
      } else {
        setRecipients(res.recipients);
        if (autoSelectType) {
          const match = res.recipients.find((r) => r.type === autoSelectType);
          if (match) setSelected(match);
        }
      }
    } catch {
      setError("Couldn't load this claim's parties.");
    } finally {
      setLoadingClaim(false);
    }
  };

  // Arriving here via "Message a Party" / "Request More Evidence" on
  // Assessment Review -- load that claim's parties immediately (and, for
  // the evidence-request path, pre-select the recipient and drop in the
  // drafted instruction) instead of landing on a blank tab.
  useEffect(() => {
    if (!prefill) return;
    setClaimId(prefill.claimId);
    loadClaim(prefill.claimId, prefill.recipientType, prefill.instruction);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [prefill?.nonce]);

  const draftMessage = async () => {
    if (!selected) return;
    setDrafting(true);
    setSendResult(null);
    try {
      const res = await aiDraftClaimMessage(claimId.trim(), {
        recipientType: selected.type,
        recipientName: selected.name,
        instruction,
      });
      if (res.success) {
        setSubject(res.subject);
        setBody(res.body);
        setDrafted(true);
      }
    } catch {
      setError("AI drafting failed -- try again, or write the message manually below.");
    } finally {
      setDrafting(false);
    }
  };

  const send = async () => {
    const toEmail = selected?.email || manualEmail.trim();
    if (!toEmail || !subject.trim() || !body.trim()) return;
    setSending(true);
    setSendResult(null);
    try {
      const res = await sendClaimMessage(claimId.trim(), { toEmail, subject, body, recipientType: selected?.type });
      setSendResult(res.success ? "success" : "error");
    } catch {
      setSendResult("error");
    } finally {
      setSending(false);
    }
  };

  return (
    <div className="space-y-6">
      <div className="flex items-center gap-3">
        {claimId && recipients.length > 0 && (
          <button
            onClick={() => onBackToClaim(claimId)}
            className="shrink-0 flex items-center gap-1.5 px-4 py-4 bg-card border border-border rounded-xl text-sm font-bold text-foreground hover:bg-muted transition-colors"
            title={`Back to ${claimId} in Assessment Review`}
          >
            <span className="material-symbols-outlined text-[18px]">arrow_back</span>Back to Claim
          </button>
        )}
        <ClaimPicker value={claimId} onChange={setClaimId} onSelect={(id) => loadClaim(id)} placeholder="Search or pick a claim to message someone about..." />
      </div>

      {loadingClaim && <p className="text-sm text-muted-foreground px-1 animate-pulse">Loading claim parties...</p>}
      {error && <p className="text-sm text-destructive font-semibold px-1">{error}</p>}

      {recipients.length > 0 && (
        <div className="grid grid-cols-1 lg:grid-cols-[280px_1fr] gap-6">
          {/* Recipient picker */}
          <div className="bg-card rounded-xl border border-border shadow-sm overflow-hidden h-fit">
            <div className="px-4 py-3 border-b border-border">
              <p className="text-xs font-black uppercase tracking-wider text-muted-foreground">Send to</p>
            </div>
            <div className="p-2 space-y-1">
              {recipients.map((r) => {
                const meta = RECIPIENT_META[r.type];
                const isSel = selected?.type === r.type && selected?.id === r.id;
                return (
                  <button
                    key={`${r.type}-${r.id}`}
                    onClick={() => { setSelected(r); setSubject(""); setBody(""); setDrafted(false); setSendResult(null); }}
                    className={`w-full flex items-center gap-3 px-3 py-2.5 rounded-lg text-left transition-colors ${isSel ? "bg-primary/10 border border-primary/30" : "hover:bg-muted/50 border border-transparent"}`}
                  >
                    <span className={`material-symbols-outlined text-[18px] ${isSel ? "text-primary" : "text-muted-foreground"}`}>{meta.icon}</span>
                    <div className="min-w-0">
                      <p className="text-sm font-bold text-foreground truncate">{r.name}</p>
                      <p className="text-[10px] text-muted-foreground truncate">{meta.label}{r.email ? ` · ${r.email}` : " · no email on file"}</p>
                    </div>
                  </button>
                );
              })}
            </div>
          </div>

          {/* Compose card -- mirrors the "AI drafts, human approves and sends" pattern */}
          <div className="bg-card rounded-xl border border-border shadow-sm overflow-hidden">
            <div className="px-5 py-3.5 border-b border-border flex items-center gap-2">
              <span className="material-symbols-outlined text-primary text-[20px]">auto_awesome</span>
              <p className="text-sm font-black text-foreground">Xenova AI Compose</p>
            </div>

            {!selected ? (
              <div className="p-10 text-center text-sm text-muted-foreground">Pick a recipient on the left to start composing.</div>
            ) : (
              <div className="p-5 space-y-4">
                <div className="flex items-center gap-2 flex-wrap">
                  <span className="text-xs font-bold text-muted-foreground">To:</span>
                  <span className="inline-flex items-center gap-1.5 bg-primary/10 text-primary text-xs font-bold px-2.5 py-1 rounded-full">
                    {selected.name}
                    <button onClick={() => setSelected(null)} className="hover:text-destructive"><span className="material-symbols-outlined text-[14px] align-middle">close</span></button>
                  </span>
                  {!selected.email && (
                    <input
                      value={manualEmail}
                      onChange={(e) => setManualEmail(e.target.value)}
                      placeholder="No email on file -- enter one"
                      className="flex-1 min-w-[180px] text-xs px-2.5 py-1.5 border border-border rounded-lg bg-background outline-none focus:ring-2 focus:ring-primary/40"
                    />
                  )}
                </div>

                <div className="flex items-center gap-2">
                  <input
                    value={instruction}
                    onChange={(e) => setInstruction(e.target.value)}
                    onKeyDown={(e) => e.key === "Enter" && draftMessage()}
                    placeholder="What should this email say? e.g. 'ask for a repair invoice'"
                    className="flex-1 text-sm px-3 py-2.5 border border-border rounded-lg bg-background outline-none focus:ring-2 focus:ring-primary/40"
                  />
                  <button
                    onClick={draftMessage}
                    disabled={drafting}
                    className="shrink-0 flex items-center gap-1.5 px-4 py-2.5 bg-primary text-primary-foreground rounded-lg text-sm font-bold hover:bg-primary/90 transition-colors disabled:opacity-50"
                  >
                    <span className="material-symbols-outlined text-[18px]">{drafting ? "hourglass_top" : "auto_awesome"}</span>
                    {drafting ? "Drafting..." : "AI Draft"}
                  </button>
                </div>

                {(subject || body) && (
                  <div className="border border-border rounded-xl overflow-hidden">
                    {drafted && (
                      <div className="px-4 py-2 bg-primary/5 border-b border-border flex items-center gap-1.5">
                        <span className="material-symbols-outlined text-[14px] text-primary">check_circle</span>
                        <span className="text-[10px] font-bold uppercase text-primary">Drafted by AI · review before sending</span>
                      </div>
                    )}
                    <div className="p-4 space-y-3">
                      <input
                        value={subject}
                        onChange={(e) => setSubject(e.target.value)}
                        placeholder="Subject"
                        className="w-full text-sm font-bold px-3 py-2 border border-border rounded-lg bg-background outline-none focus:ring-2 focus:ring-primary/40"
                      />
                      <textarea
                        value={body}
                        onChange={(e) => setBody(e.target.value)}
                        rows={8}
                        placeholder="Message body"
                        className="w-full text-sm px-3 py-2.5 border border-border rounded-lg bg-background outline-none focus:ring-2 focus:ring-primary/40 resize-y"
                      />
                    </div>
                    <div className="px-4 py-3 bg-muted/30 border-t border-border flex items-center justify-between">
                      <p className="text-[10px] text-muted-foreground">
                        {sendResult === "success" && <span className="text-emerald-600 font-bold">Sent successfully.</span>}
                        {sendResult === "error" && <span className="text-destructive font-bold">Failed to send -- check email configuration.</span>}
                        {!sendResult && "Nothing sends until you approve it."}
                      </p>
                      <button
                        onClick={send}
                        disabled={sending || (!selected.email && !manualEmail.trim()) || !subject.trim() || !body.trim()}
                        className="flex items-center gap-1.5 px-4 py-2 bg-primary text-primary-foreground rounded-lg text-sm font-bold hover:bg-primary/90 transition-colors disabled:opacity-50"
                      >
                        <span className="material-symbols-outlined text-[18px]">send</span>
                        {sending ? "Sending..." : "Approve and Send"}
                      </button>
                    </div>
                  </div>
                )}
              </div>
            )}
          </div>
        </div>
      )}

      {recipients.length === 0 && !error && !loadingClaim && (
        <EmptyBlock label="Search or pick a claim above to draft and send AI-assisted messages to its member, assessor, analyst, or repair shop." />
      )}
    </div>
  );
}
