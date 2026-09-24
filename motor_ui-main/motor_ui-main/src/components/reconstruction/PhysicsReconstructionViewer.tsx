import { useMemo, useRef, useState } from "react";
import { PhysicsReconstruction, VehicleInfo, DEFAULT_LAYERS, CameraMode, DamageZoneWithParty, vehicleInfoFromKey } from "./types";
import { telemetryBounds, deriveEvents } from "./interpolateTelemetry";
import { usePlaybackClock } from "./usePlaybackClock";
import ReconstructionCanvas, { CameraView } from "./ReconstructionCanvas";
import PhysicsHUD from "./PhysicsHUD";
import ReconstructionTimeline from "./ReconstructionTimeline";
import ReconstructionModeBadge from "./ReconstructionModeBadge";
import CameraControls, { CAMERA_PRESETS } from "./CameraControls";
import LayersControl from "./LayersControl";
import EvidencePanel from "./EvidencePanel";
import ComparisonTable from "./ComparisonTable";
import CollisionSummaryCard from "./CollisionSummaryCard";
import DamagePhotoOverlay, { OverlayPhoto } from "./DamagePhotoOverlay";
import LoadingSequence from "./LoadingSequence";
import { interpState } from "./interpolateTelemetry";

// Human-readable label for a data_sources value. Never surface the raw
// backend tag (e.g. "llm_extracted") or a vendor name -- this is
// deliberately model-agnostic since the actual model behind narrative
// extraction has changed before (Gemini -> a self-hosted Ollama model) and
// the label shouldn't have to change every time the backend swaps engines.
function provenanceLabel(value: string): string {
  const map: Record<string, string> = {
    assessor_measured: "Assessor measured",
    stationary_override: "Confirmed stationary",
    llm_extracted: "AI-extracted from narrative",
    // Legacy alias -- see the matching note in EvidencePanel.tsx.
    gemini_extracted: "AI-extracted from narrative",
    vision_estimated: "Vision estimate (CV)",
    keyword_inferred: "Inferred from wording",
    claimant_stated: "Claimant-confirmed",
    cv_detected: "Detected from photos",
    narrative_inferred: "Inferred from narrative",
    not_available: "Not available",
  };
  return map[value] || value.replace(/_/g, " ");
}

export default function PhysicsReconstructionViewer({
  physics, claimId, v1Info, v2Info, damageZones, damagePhotos, loading,
}: {
  physics: PhysicsReconstruction;
  claimId: string;
  v1Info?: VehicleInfo;
  v2Info?: VehicleInfo;
  damageZones?: DamageZoneWithParty[];
  damagePhotos?: OverlayPhoto[];
  loading?: boolean;
}) {
  const resolvedV1Info = v1Info || vehicleInfoFromKey(physics.vehicle_1_key);
  const resolvedV2Info = v2Info || vehicleInfoFromKey(physics.vehicle_2_key);
  const [layers, setLayers] = useState(DEFAULT_LAYERS);
  // Default view is "approach" (moderately zoomed on the pair of vehicles),
  // not a flat 1x full-extent "overview" -- pre-crash telemetry can span
  // 100m+ (5s at highway speed), which made both vehicles render as
  // sub-pixel specks by default. The full-extent overview is still one
  // click away via the camera preset row.
  const [cameraMode, setCameraMode] = useState<CameraMode>("approach");
  const [camera, setCamera] = useState<CameraView>({ cx: 0, cy: 0, scale: 2.2 });
  const [measuring, setMeasuring] = useState(false);
  const [measurement, setMeasurement] = useState<number | null>(null);
  const [showEvidence, setShowEvidence] = useState(true);
  const evidenceRef = useRef<HTMLDivElement>(null);

  const timeline = physics.timeline;
  const hasTimeline = !!(timeline?.v1_insured_telemetry?.length && timeline?.v2_third_party_telemetry?.length);

  const { tStart, tEnd } = useMemo(() => (hasTimeline ? telemetryBounds(timeline!) : { tStart: 0, tEnd: 1 }), [timeline, hasTimeline]);
  const events = useMemo(() => (hasTimeline ? deriveEvents(timeline!) : []), [timeline, hasTimeline]);
  const clock = usePlaybackClock(tStart, tEnd);

  const applyCameraPreset = (mode: CameraMode) => {
    setCameraMode(mode);
    if (mode === "free" || !hasTimeline) return;
    const preset = CAMERA_PRESETS.find((p) => p.mode === mode);
    if (!preset) return;
    const s1 = interpState(clock.tSim, timeline!.v1_insured_telemetry);
    const s2 = interpState(clock.tSim, timeline!.v2_third_party_telemetry);
    setCamera(preset.view(0, s1, s2));
  };

  // ── Empty / error states -- never a blank screen ──────────────────────
  if (loading) return <LoadingSequence />;

  if (!physics || physics.status === "not_run") {
    return (
      <div className="rounded-2xl border border-dashed border-border bg-muted/20 p-10 text-center">
        <span className="material-symbols-outlined text-3xl text-muted-foreground mb-2">architecture</span>
        <p className="text-sm font-bold text-foreground">Run reconstruction to generate the accident timeline.</p>
      </div>
    );
  }
  if (physics.status === "skipped") {
    return (
      <div className="rounded-2xl border border-dashed border-border bg-muted/20 p-10 text-center">
        <span className="material-symbols-outlined text-3xl text-muted-foreground mb-2">info</span>
        <p className="text-sm font-bold text-foreground">Insufficient data for high-confidence reconstruction.</p>
      </div>
    );
  }
  if (!hasTimeline) {
    return (
      <div className="space-y-4">
        <div className="rounded-2xl border border-dashed border-border bg-muted/20 p-6 text-center">
          <span className="material-symbols-outlined text-2xl text-muted-foreground mb-1">route</span>
          <p className="text-xs font-bold text-foreground">Telemetry unavailable — using narrative-based reconstruction.</p>
          <p className="text-[11px] text-muted-foreground mt-1">The evidence and comparison below still reflect the physics engine's output.</p>
        </div>
        <EvidencePanel physics={physics} />
        <ComparisonTable comparison={physics.comparison} />
      </div>
    );
  }

  return (
    <div className="space-y-4 min-w-0">
      {/* Top bar */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
        <ReconstructionModeBadge physics={physics} />
        <CameraControls active={cameraMode} onSelect={applyCameraPreset} />
      </div>

      <div className="grid grid-cols-1 xl:grid-cols-[200px_1fr_320px] gap-4 min-w-0">
        {/* Left: layers + incident data. The Evidence column (right) is
            substantially taller than this one (verdict/speed/damage/impact/
            road/data-quality/why/warnings/comparison, all stacked) -- CSS
            grid doesn't stretch a shorter sibling to match a taller one, so
            once scrolled down far enough to see the bottom of Evidence,
            this column had already ended, reading as "empty space" even
            though nothing failed to render. xl:sticky keeps it in view
            alongside whatever part of Evidence is currently visible instead
            of scrolling away and leaving blank page behind it. */}
        <div className="order-2 xl:order-1 min-w-0 xl:flex xl:flex-col xl:h-[520px] xl:sticky xl:top-4 xl:self-start gap-4">
          <div className="shrink-0 space-y-4">
            <LayersControl layers={layers} onChange={setLayers} measuring={measuring} onToggleMeasuring={() => { setMeasuring((m) => !m); setMeasurement(null); }} />
            {measuring && measurement != null && (
              <div className="rounded-xl border border-cyan-500/30 bg-cyan-500/5 p-3 text-center">
                <p className="text-[9px] font-black uppercase text-cyan-700">Distance</p>
                <p className="text-lg font-black text-cyan-700 tabular-nums">{measurement.toFixed(1)} m</p>
              </div>
            )}
          </div>
          <div className="rounded-2xl border border-border bg-card shadow-sm p-4 xl:flex-1 xl:overflow-y-auto">
            <p className="text-[10px] font-black uppercase tracking-widest text-muted-foreground mb-3">Incident Data</p>
            <div className="space-y-3 text-xs">
              <div>
                <p className="text-[9px] font-bold uppercase text-blue-600 mb-0.5">Vehicle 1</p>
                <p className="font-semibold text-foreground truncate">{resolvedV1Info?.model || resolvedV1Info?.make || "Unknown"}</p>
              </div>
              <div>
                <p className="text-[9px] font-bold uppercase text-red-600 mb-0.5">Vehicle 2</p>
                <p className="font-semibold text-foreground truncate">{resolvedV2Info?.model || resolvedV2Info?.make || "Unknown"}</p>
              </div>
              <div className="pt-2 border-t border-border">
                <p className="text-[9px] font-bold uppercase text-muted-foreground mb-0.5">Impact Angle</p>
                <p className="font-semibold text-foreground">{physics.comparison?.approach_angle_deg != null ? `${Math.round(physics.comparison.approach_angle_deg)}°` : "—"}</p>
              </div>
              <div>
                <p className="text-[9px] font-bold uppercase text-muted-foreground mb-0.5">Crush Depth</p>
                <p className="font-semibold text-foreground">{physics.comparison?.crush_depth_mm?.claimed != null ? `${physics.comparison.crush_depth_mm.claimed} mm` : "—"}</p>
              </div>
              {physics.impact_zone_v1_detected_part && (
                <div>
                  <p className="text-[9px] font-bold uppercase text-muted-foreground mb-0.5">Detected Damage</p>
                  <p className="font-semibold text-foreground">{physics.impact_zone_v1_detected_part}</p>
                  <p className="text-[9px] text-muted-foreground mt-0.5">From uploaded photos (CV-detected)</p>
                </div>
              )}
              <div>
                <p className="text-[9px] font-bold uppercase text-muted-foreground mb-0.5">Consistency</p>
                <p className={`font-semibold ${physics.physics_verdict === "CONSISTENT" ? "text-primary" : physics.physics_verdict === "INCONSISTENT" ? "text-destructive" : "text-amber-600"}`}>
                  {physics.physics_verdict || "—"}
                </p>
              </div>
              {physics.data_sources && (
                <div className="pt-2 border-t border-border space-y-1.5">
                  <p className="text-[9px] font-bold uppercase text-muted-foreground mb-1">Data Provenance</p>
                  {Object.entries(physics.data_sources)
                    .filter(([k]) => k !== "mchenry_ran")
                    .map(([k, v]) => (
                      <div key={k} className="flex items-center justify-between gap-2">
                        <span className="text-muted-foreground capitalize">{k.replace(/_/g, " ")}</span>
                        <span className="font-semibold text-foreground text-right">{provenanceLabel(String(v))}</span>
                      </div>
                    ))}
                </div>
              )}
            </div>
          </div>
        </div>

        {/* Center: canvas + timeline */}
        <div className="order-1 xl:order-2 space-y-4 min-w-0 xl:sticky xl:top-4 xl:self-start">
          {/* Fixed heights at every breakpoint -- NOT aspect-square at full
              container width, which previously made the canvas as tall as
              the entire page was wide on any screen narrower than the xl
              breakpoint (a ~1900px-wide square). min-w-0 here and on every
              ancestor above prevents the canvas's own intrinsic pixel size
              from forcing its flex/grid ancestors wider than the viewport
              (the classic "min-width: auto" flex/grid overflow trap). */}
          <div className="relative min-w-0 h-[360px] sm:h-[440px] xl:h-[520px]">
            <ReconstructionCanvas
              timeline={timeline!}
              tSim={clock.tSim}
              v1Info={resolvedV1Info}
              v2Info={resolvedV2Info}
              physics={physics}
              layers={layers}
              camera={camera}
              onCameraChange={(c) => { setCamera(c); setCameraMode("free"); }}
              damageZones={layers.impactZone ? damageZones : undefined}
              measuring={measuring}
              onMeasurement={setMeasurement}
            />
            <PhysicsHUD timeline={timeline!} tSim={clock.tSim} v1Info={resolvedV1Info} v2Info={resolvedV2Info} physics={physics} />
          </div>
          <ReconstructionTimeline
            tStart={tStart}
            tEnd={tEnd}
            tSim={clock.tSim}
            events={events}
            playing={clock.playing}
            speed={clock.speed}
            onPlay={clock.play}
            onPause={clock.pause}
            onSeek={clock.seek}
            onStep={clock.stepFrame}
            onSpeedChange={clock.setSpeed}
            onImpactReplay={clock.impactReplay}
            onReplay={clock.replay}
          />

          {layers.damageOverlay && damagePhotos && damagePhotos.length > 0 && (
            <DamagePhotoOverlay photos={damagePhotos} />
          )}
        </div>

        {/* Right: evidence */}
        <div className="order-3 space-y-4 min-w-0">
          <CollisionSummaryCard
            physics={physics}
            v1Info={resolvedV1Info}
            v2Info={resolvedV2Info}
            claimId={claimId}
            onReplay={clock.replay}
            onViewEvidence={() => {
              // showEvidence already defaults to true (the panel is always
              // rendered), so setting it to true again was a no-op -- the
              // button appeared broken with nothing to reveal. What's
              // actually useful is scrolling it into view, which matters on
              // narrower screens where it can be well below the fold.
              setShowEvidence(true);
              evidenceRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
            }}
          />
          {showEvidence && <div ref={evidenceRef}><EvidencePanel physics={physics} /></div>}
          <ComparisonTable comparison={physics.comparison} />
        </div>
      </div>
    </div>
  );
}
