import { useState } from "react";
import { PhysicsReconstruction } from "./types";
import CalculationModal, { CalculationDetail } from "./CalculationModal";

/** "Requires review", never "proven fraud" -- the physics engine produces a
 * risk indicator, not a verdict of guilt. Language mirrors what the rest of
 * the app already uses for AI-derived findings. */
function verdictCopy(verdict?: string): { label: string; sentence: string; className: string; icon: string } {
  switch (verdict) {
    case "CONSISTENT":
      return { label: "Consistent", sentence: "Physics simulation is consistent with the reported accident account.", className: "bg-primary/10 text-primary border-primary/20", icon: "verified" };
    case "SUSPICIOUS":
      return { label: "Requires review", sentence: "Physics simulation shows discrepancies with the reported account that are worth a closer look.", className: "bg-amber-500/10 text-amber-700 border-amber-500/20", icon: "warning" };
    case "INCONSISTENT":
      return { label: "Significant inconsistency — investigation recommended", sentence: "Physics simulation diverges materially from the reported account.", className: "bg-destructive/10 text-destructive border-destructive/20", icon: "report" };
    default:
      return { label: "Not yet determined", sentence: "Physics reconstruction has not produced a verdict for this claim.", className: "bg-muted text-muted-foreground border-border", icon: "help" };
  }
}

// How each source tag was actually arrived at -- shown in the provenance
// modal when a badge is clicked. Deliberately model-agnostic (no vendor
// names) since the backend model behind narrative extraction has changed
// before and this explanation shouldn't need to change with it.
const SOURCE_METHODOLOGY: Record<string, { method: string; note: string }> = {
  assessor_measured: {
    method: "On-site physical measurement",
    note: "The assigned assessor measured this directly during their vehicle inspection -- the most reliable source available, since it's an independent physical check rather than something either party described.",
  },
  stationary_override: {
    method: "Confirmed stationary in narrative",
    note: "The narrative clearly indicated this vehicle was stopped, parked, or stationary at the moment of impact, so this value was set directly (0 km/h) rather than estimated from vague wording.",
  },
  llm_extracted: {
    method: "AI narrative extraction",
    note: "An AI language model read the claimant's full narrative text and identified an explicitly stated figure or fact (e.g. a number, a named vehicle) -- not a guess from vague wording, but still only as reliable as what the claimant chose to write.",
  },
  // Legacy alias -- claims reconstructed before the backend tag was
  // renamed from "gemini_extracted" to the model-agnostic "llm_extracted"
  // still carry the old string in their stored data_sources until they're
  // next reprocessed. Kept so old claims render correctly without forcing
  // every historical claim through a fresh (Gemini-calling) reprocess.
  gemini_extracted: {
    method: "AI narrative extraction",
    note: "An AI language model read the claimant's full narrative text and identified an explicitly stated figure or fact (e.g. a number, a named vehicle) -- not a guess from vague wording, but still only as reliable as what the claimant chose to write.",
  },
  keyword_inferred: {
    method: "Keyword-based estimate",
    note: "No explicit figure was stated in the narrative. This was estimated by matching descriptive words or phrases (e.g. \"fast\", \"slow\", \"minor bump\") against a reference range for that kind of wording, then taking the midpoint of the range. This is the least reliable source tier -- treat it as a rough estimate, not a stated fact.",
  },
  vision_estimated: {
    method: "Computer-vision estimate",
    note: "No narrative or assessor figure was available. The trained computer-vision damage-detection model analyzed the claim's own uploaded photos and produced an estimate from visible damage severity in a single image -- a genuine estimate, not a precise measurement.",
  },
  claimant_stated: {
    method: "Claimant-confirmed at filing",
    note: "The claimant directly answered this as a specific question when filing the claim, rather than it being guessed from free-text wording. Still self-reported, so it's cross-checked against independent evidence (like CV-detected photo damage) where available rather than trusted blindly.",
  },
  cv_detected: {
    method: "Detected from uploaded photos",
    note: "The trained computer-vision model identified this directly from the claim's own uploaded photos -- independent of what either party wrote in their narrative, and harder to misrepresent since the photos were submitted separately.",
  },
  narrative_inferred: {
    method: "Inferred from narrative wording",
    note: "No photo evidence was available to confirm this. It was inferred from how the claimant described the incident in their narrative text.",
  },
  not_available: {
    method: "No source available",
    note: "Neither the narrative, an assessor measurement, nor photo evidence provided this value for this claim.",
  },
};

function buildSourceDetail(fieldLabel: string, source: string, value: string): CalculationDetail {
  const m = SOURCE_METHODOLOGY[source] || { method: source.replace(/_/g, " "), note: "" };
  return {
    title: `${fieldLabel} -- Source`,
    formula: m.method,
    formulaLabel: "How this was determined",
    formulaNote: m.note,
    steps: [],
    resultLabel: fieldLabel,
    resultValue: value,
  };
}

function SourceBadge({ source, fieldLabel, value, onOpen }: { source?: string; fieldLabel: string; value: string; onOpen: (d: CalculationDetail) => void }) {
  if (!source) return null;
  const map: Record<string, { label: string; className: string }> = {
    assessor_measured: { label: "OBSERVED", className: "bg-primary/10 text-primary" },
    stationary_override: { label: "OBSERVED", className: "bg-primary/10 text-primary" },
    llm_extracted: { label: "INFERRED", className: "bg-blue-500/10 text-blue-600" },
    gemini_extracted: { label: "INFERRED", className: "bg-blue-500/10 text-blue-600" },
    keyword_inferred: { label: "INFERRED", className: "bg-blue-500/10 text-blue-600" },
    vision_estimated: { label: "VISION ESTIMATE", className: "bg-violet-500/10 text-violet-600" },
    claimant_stated: { label: "STATED", className: "bg-primary/10 text-primary" },
    not_available: { label: "UNAVAILABLE", className: "bg-muted text-muted-foreground" },
  };
  const m = map[source] || { label: source.toUpperCase(), className: "bg-muted text-muted-foreground" };
  if (source === "not_available") {
    return <span className={`text-[8px] font-black uppercase px-1.5 py-0.5 rounded-full ${m.className}`}>{m.label}</span>;
  }
  return (
    <button
      type="button"
      onClick={() => onOpen(buildSourceDetail(fieldLabel, source, value))}
      className={`text-[8px] font-black uppercase px-1.5 py-0.5 rounded-full ${m.className} hover:brightness-95 cursor-pointer underline decoration-dotted underline-offset-2`}
      title="See where this figure came from"
    >
      {m.label}
    </button>
  );
}

function CalcBadge({ label, className, onClick }: { label: string; className: string; onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`text-[8px] font-black uppercase px-1.5 py-0.5 rounded-full ${className} hover:brightness-95 cursor-pointer underline decoration-dotted underline-offset-2`}
      title="See how this was calculated"
    >
      {label}
    </button>
  );
}

function Row({ label, value, badge, status }: { label: string; value: string; badge?: React.ReactNode; status?: { label: string; className: string } }) {
  return (
    <div className="flex items-center justify-between gap-2 py-1.5 text-xs">
      <span className="text-muted-foreground">{label}</span>
      <span className="flex items-center gap-1.5">
        <span className="font-bold text-foreground tabular-nums">{value}</span>
        {badge}
        {status && <span className={`text-[8px] font-black uppercase px-1.5 py-0.5 rounded-full ${status.className}`}>{status.label}</span>}
      </span>
    </div>
  );
}

export default function EvidencePanel({ physics }: { physics: PhysicsReconstruction }) {
  const [whyOpen, setWhyOpen] = useState(false);
  const [calc, setCalc] = useState<CalculationDetail | null>(null);
  const v = verdictCopy(physics.physics_verdict);
  const c = physics.comparison;
  const ds = physics.data_sources;

  const fmtN = (n?: number | null, suffix = "") => (n == null ? "Not available for this claim" : `${n.toLocaleString(undefined, { maximumFractionDigits: 1 })}${suffix}`);

  const speedCalc = (vehicle: "V1" | "V2"): CalculationDetail => ({
    title: `${vehicle} Physics-Derived Speed`,
    formula: "v = √(2 × Ecrush / m)",
    formulaNote: "The McHenry crush-energy method: energy absorbed by the vehicle's crumple zone is converted back to an equivalent pre-impact speed, using the vehicle's own crumple-zone stiffness constants (A, B) from the vehicle registry.",
    steps: [
      { label: "Crush energy absorbed (Ecrush)", value: fmtN(physics.crush_energy_j, " J") },
      { label: "Vehicle mass (m)", value: "From vehicle registry (kerb weight + assumed occupant load)" },
      { label: "Crumple-zone stiffness (A, B)", value: "From vehicle registry, by make/model/body type" },
    ],
    resultLabel: `${vehicle} physics-derived speed`,
    resultValue: c?.[vehicle === "V1" ? "speed_v1_kmh" : "speed_v2_kmh"]?.reconstructed != null
      ? `${c![vehicle === "V1" ? "speed_v1_kmh" : "speed_v2_kmh"]!.reconstructed} km/h`
      : "—",
    caveat: physics.crush_energy_j == null
      ? "This claim's telemetry didn't carry a separate crush-energy breakdown, so the exact intermediate value isn't shown here -- the result itself is still computed by this formula server-side."
      : undefined,
  });

  const crushCalc = (): CalculationDetail => ({
    title: "Expected Crush Depth",
    formula: "KE = ½ × m × v² = (A×C + B×C²) × L",
    formulaNote: "Solves for crush depth C: given the vehicle's kinetic energy at the stated impact speed, how deep would the crumple zone need to deform (per the vehicle's own A/B stiffness constants and impact width L) to absorb that energy.",
    steps: [
      { label: "Stated impact speed", value: c?.speed_v1_kmh?.claimed != null ? `${c.speed_v1_kmh.claimed} km/h` : "—" },
      { label: "Vehicle mass (m)", value: "From vehicle registry (kerb weight + assumed occupant load)" },
      { label: "Crumple-zone stiffness (A, B) & impact width (L)", value: "From vehicle registry / detected impact zone" },
    ],
    resultLabel: "Expected crush depth",
    resultValue: c?.crush_depth_mm?.reconstructed != null ? `${c.crush_depth_mm.reconstructed} mm` : "—",
    caveat: "Compared against the observed crush depth (measured, extracted, or vision-estimated) to flag whether the stated speed is physically consistent with the actual damage.",
  });

  const deltaVCalc = (): CalculationDetail => ({
    title: "Delta-V (ΔV)",
    formula: "ΔV = |v(before) − v(after)|",
    formulaNote: "The change in each vehicle's velocity across the impact, taken directly from the simulated pre- and post-impact telemetry states.",
    steps: [
      { label: "V1 pre-impact speed", value: c?.speed_v1_kmh?.reconstructed != null ? `${c.speed_v1_kmh.reconstructed} km/h` : "—" },
      { label: "V2 pre-impact speed", value: c?.speed_v2_kmh?.reconstructed != null ? `${c.speed_v2_kmh.reconstructed} km/h` : "—" },
    ],
    resultLabel: "ΔV",
    resultValue: physics.delta_v_kmh != null ? `${physics.delta_v_kmh.toFixed(1)} km/h` : "—",
  });

  const impactForceCalc = (): CalculationDetail => ({
    title: "Impact Force" + (physics.impact_force_is_estimated ? " (Estimated)" : ""),
    formula: "F = m × ΔV / t",
    formulaNote: physics.impact_force_is_estimated
      ? "Impulse-momentum: force equals mass times the change in velocity, divided by an assumed crash-pulse duration (0.12s, a typical value for a rigid-body collision). This claim has no real sensor-measured force, so this is a genuine physics estimate, not a measurement."
      : "Impulse-momentum, computed directly from telemetry-measured mass, ΔV, and impact duration.",
    steps: [
      { label: "Vehicle mass (m)", value: "From vehicle registry (kerb weight + assumed occupant load)" },
      { label: "ΔV", value: physics.delta_v_kmh != null ? `${physics.delta_v_kmh.toFixed(1)} km/h` : "—" },
      { label: "Assumed crash-pulse duration (t)", value: physics.impact_force_is_estimated ? "0.12 s (typical value, not measured)" : "From telemetry" },
    ],
    resultLabel: "Impact force",
    resultValue: physics.impact_force_magnitude_n != null
      ? `${physics.impact_force_is_estimated ? "~" : ""}${(physics.impact_force_magnitude_n / 1000).toFixed(0)} kN`
      : "—",
    caveat: physics.impact_force_is_estimated
      ? "Treat as an order-of-magnitude estimate, not a precise measurement -- the actual crash-pulse duration varies by vehicle and impact type."
      : undefined,
  });

  // Computed directly from the actual delta, NOT from the backend's
  // `velocity_fraud_severity` -- that field is deliberately forced to
  // "none" whenever the input speed is a low-confidence narrative guess (so
  // a shaky guess doesn't drive the fraud score), which previously made
  // this badge claim "Matches claim" next to a 27km/h gap. Whether a number
  // is reliable enough to flag as fraud and whether it actually matches are
  // two different questions -- the [INFERRED]/[STATED] badge already covers
  // reliability, so this one should just report the real gap honestly.
  const speedStatus = (deltaKmh?: number | null) => {
    if (deltaKmh == null) return undefined;
    const abs = Math.abs(deltaKmh);
    if (abs <= 8) return { label: "Matches claim", className: "bg-primary/10 text-primary" };
    if (abs <= 20) return { label: "Minor discrepancy", className: "bg-blue-500/10 text-blue-600" };
    if (abs <= 40) return { label: "Moderate discrepancy", className: "bg-amber-500/10 text-amber-700" };
    return { label: "Major discrepancy", className: "bg-destructive/10 text-destructive" };
  };

  const crushDelta = c?.crush_depth_mm?.delta;
  const crushStatus = crushDelta == null ? undefined : Math.abs(crushDelta) <= 40
    ? { label: "Consistent", className: "bg-primary/10 text-primary" }
    : { label: "Worth reviewing", className: "bg-amber-500/10 text-amber-700" };

  return (
    <div className="space-y-4">
      {/* Verdict card */}
      <div className={`rounded-2xl border p-4 ${v.className}`}>
        <div className="flex items-center gap-2 mb-1">
          <span className="material-symbols-outlined text-[18px]">{v.icon}</span>
          <p className="text-[10px] font-black uppercase tracking-widest">Physics Reconstruction</p>
        </div>
        <p className="text-base font-black">{v.label}</p>
        <div className="flex items-center gap-4 mt-2 text-[11px] font-bold">
          <span>Fraud Signal: {physics.physics_fraud_score ?? "—"}/100</span>
          {physics.confidence != null && <span>Confidence: {Math.round(physics.confidence * 100)}%</span>}
        </div>
        <p className="text-xs mt-2 leading-relaxed opacity-90">{v.sentence}</p>
        <p className="text-[9px] mt-2 opacity-60 italic">This score is a risk indicator, not proof of fraud -- final decisions remain with the claims team.</p>
      </div>

      {/* Speed Analysis */}
      {c?.speed_v1_kmh && (
        <div className="rounded-2xl border border-border bg-card shadow-sm p-4">
          <p className="text-[10px] font-black uppercase tracking-widest text-muted-foreground mb-2">Speed Analysis</p>
          <Row label="V1 stated" value={c.speed_v1_kmh.claimed != null ? `${c.speed_v1_kmh.claimed} km/h` : "—"} badge={<SourceBadge source={ds?.v1_speed} fieldLabel="V1 Stated Speed" value={c.speed_v1_kmh.claimed != null ? `${c.speed_v1_kmh.claimed} km/h` : "—"} onOpen={setCalc} />} />
          <Row label="V1 physics-derived" value={c.speed_v1_kmh.reconstructed != null ? `${c.speed_v1_kmh.reconstructed} km/h` : "—"} badge={<CalcBadge label="CALCULATED" className="bg-violet-500/10 text-violet-600" onClick={() => setCalc(speedCalc("V1"))} />} status={speedStatus(c.speed_v1_kmh.delta)} />
          {c.speed_v2_kmh && (
            <>
              <Row label="V2 stated" value={c.speed_v2_kmh.claimed != null ? `${c.speed_v2_kmh.claimed} km/h` : "—"} badge={<SourceBadge source={ds?.v2_speed} fieldLabel="V2 Stated Speed" value={c.speed_v2_kmh.claimed != null ? `${c.speed_v2_kmh.claimed} km/h` : "—"} onOpen={setCalc} />} />
              <Row label="V2 physics-derived" value={c.speed_v2_kmh.reconstructed != null ? `${c.speed_v2_kmh.reconstructed} km/h` : "—"} badge={<CalcBadge label="CALCULATED" className="bg-violet-500/10 text-violet-600" onClick={() => setCalc(speedCalc("V2"))} />} />
            </>
          )}
        </div>
      )}

      {/* Damage / Crush Analysis */}
      {c?.crush_depth_mm && (
        <div className="rounded-2xl border border-border bg-card shadow-sm p-4">
          <p className="text-[10px] font-black uppercase tracking-widest text-muted-foreground mb-2">Damage / Crush Analysis</p>
          <Row label="Observed crush" value={c.crush_depth_mm.claimed != null ? `${c.crush_depth_mm.claimed} mm` : "—"} badge={<SourceBadge source={ds?.crush_depth} fieldLabel="Observed Crush Depth" value={c.crush_depth_mm.claimed != null ? `${c.crush_depth_mm.claimed} mm` : "—"} onOpen={setCalc} />} />
          <Row label="Expected crush" value={c.crush_depth_mm.reconstructed != null ? `${c.crush_depth_mm.reconstructed} mm` : "—"} badge={<CalcBadge label="CALCULATED" className="bg-violet-500/10 text-violet-600" onClick={() => setCalc(crushCalc())} />} status={crushStatus} />
          {crushDelta != null && <Row label="Difference" value={`${crushDelta >= 0 ? "+" : ""}${crushDelta.toFixed(0)} mm`} />}
        </div>
      )}

      {/* Impact Analysis */}
      <div className="rounded-2xl border border-border bg-card shadow-sm p-4">
        <p className="text-[10px] font-black uppercase tracking-widest text-muted-foreground mb-2">Impact Analysis</p>
        <Row label="Impact angle" value={c?.approach_angle_deg != null ? `${Math.round(c.approach_angle_deg)}°` : "—"} badge={<SourceBadge source={ds?.approach_angle} fieldLabel="Impact Angle" value={c?.approach_angle_deg != null ? `${Math.round(c.approach_angle_deg)}°` : "—"} onOpen={setCalc} />} />
        <Row label="ΔV" value={physics.delta_v_kmh != null ? `${physics.delta_v_kmh.toFixed(1)} km/h` : "—"} badge={physics.delta_v_kmh != null ? <CalcBadge label="CALCULATED" className="bg-violet-500/10 text-violet-600" onClick={() => setCalc(deltaVCalc())} /> : undefined} />
        <Row
          label="Impact force"
          value={physics.impact_force_magnitude_n != null ? `${physics.impact_force_is_estimated ? "~" : ""}${(physics.impact_force_magnitude_n / 1000).toFixed(0)} kN` : "Unavailable"}
          badge={
            physics.impact_force_magnitude_n == null ? undefined :
            physics.impact_force_is_estimated
              ? <CalcBadge label="ESTIMATED" className="bg-blue-500/10 text-blue-600" onClick={() => setCalc(impactForceCalc())} />
              : <CalcBadge label="CALCULATED" className="bg-violet-500/10 text-violet-600" onClick={() => setCalc(impactForceCalc())} />
          }
        />
        {physics.impact_force_is_estimated && physics.impact_force_magnitude_n != null && (
          <p className="text-[10px] text-muted-foreground italic mt-1">Estimated from mass and ΔV using an assumed crash-pulse duration -- not measured from telemetry.</p>
        )}
      </div>

      {/* Road Conditions -- only when the backend actually sent something */}
      {(physics.terrain_adjusted || physics.slope_adjustment_kmh != null) && (
        <div className="rounded-2xl border border-border bg-card shadow-sm p-4">
          <p className="text-[10px] font-black uppercase tracking-widest text-muted-foreground mb-2">Road Conditions</p>
          {physics.slope_adjustment_kmh != null && <Row label="Terrain adjustment" value={`${physics.slope_adjustment_kmh >= 0 ? "+" : ""}${physics.slope_adjustment_kmh.toFixed(1)} km/h`} />}
          <Row label="Terrain-adjusted" value={physics.terrain_adjusted ? "Yes" : "No"} />
        </div>
      )}

      {/* Data quality */}
      {c?.data_quality_score != null && (
        <div className="rounded-2xl border border-border bg-card shadow-sm p-4">
          <p className="text-[10px] font-black uppercase tracking-widest text-muted-foreground mb-2">Data Quality</p>
          <div className="flex items-center gap-3">
            <div className="flex-1 h-2 bg-muted rounded-full overflow-hidden">
              <div className="h-full bg-primary rounded-full" style={{ width: `${Math.round(c.data_quality_score * 100)}%` }} />
            </div>
            <span className="text-xs font-bold text-foreground tabular-nums">{Math.round(c.data_quality_score * 100)}%</span>
          </div>
          <p className="text-[10px] text-muted-foreground italic mt-1.5">A single overall score -- the underlying engine doesn't break this down per data category.</p>
        </div>
      )}

      {/* Why explanation */}
      {physics.physics_explanation && (
        <details className="rounded-2xl border border-border bg-card shadow-sm overflow-hidden" open={whyOpen} onToggle={(e) => setWhyOpen((e.target as HTMLDetailsElement).open)}>
          <summary className="cursor-pointer list-none px-4 py-3 flex items-center justify-between text-[10px] font-black uppercase tracking-widest text-muted-foreground">
            Why did the system reach this verdict?
            <span className="material-symbols-outlined text-[16px] transition-transform" style={{ transform: whyOpen ? "rotate(180deg)" : "none" }}>expand_more</span>
          </summary>
          <p className="px-4 pb-4 text-xs text-foreground/80 leading-relaxed whitespace-pre-wrap">{physics.physics_explanation}</p>
        </details>
      )}

      {physics.warnings && physics.warnings.length > 0 && (
        <div className="rounded-2xl border border-amber-500/20 bg-amber-500/5 p-4">
          <p className="text-[10px] font-black uppercase tracking-widest text-amber-700 mb-2">Simulation Warnings ({physics.warnings.length})</p>
          <ul className="space-y-1">
            {physics.warnings.map((w, i) => <li key={i} className="text-[11px] text-amber-900/80 leading-relaxed">• {w}</li>)}
          </ul>
        </div>
      )}

      {calc && <CalculationModal detail={calc} onClose={() => setCalc(null)} />}
    </div>
  );
}
