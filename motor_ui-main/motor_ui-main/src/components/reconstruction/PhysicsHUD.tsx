import { useState } from "react";
import { SimulationTimeline, VehicleInfo, PhysicsReconstruction } from "./types";
import { interpState } from "./interpolateTelemetry";

/** Compact live overlay -- updates every frame from the same interpolated
 * state the canvas draws, so the HUD numbers and the vehicles on screen
 * never disagree. Prioritizes the handful of numbers that actually explain
 * the collision, per the spec's "don't show every variable at once" rule.
 * Light "map info card" style (white, dark text) to match the canvas's
 * Google-Maps-like theme, collapsible to a small badge since it previously
 * sat directly over the road/vehicles and blocked the view. */
export default function PhysicsHUD({
  timeline, tSim, v1Info, v2Info, physics,
}: {
  timeline: SimulationTimeline; tSim: number; v1Info?: VehicleInfo; v2Info?: VehicleInfo; physics: PhysicsReconstruction;
}) {
  const [collapsed, setCollapsed] = useState(false);
  const s1 = interpState(tSim, timeline.v1_insured_telemetry);
  const s2 = interpState(tSim, timeline.v2_third_party_telemetry);
  const angle = physics.comparison?.approach_angle_deg;
  const deltaV = physics.delta_v_kmh;
  const force = physics.impact_force_magnitude_n;

  if (collapsed) {
    return (
      <button
        onClick={() => setCollapsed(false)}
        className="absolute top-3 left-3 flex items-center gap-1.5 px-2.5 py-1.5 rounded-full bg-white/95 shadow-md border border-black/10 text-[10px] font-bold text-foreground"
      >
        <span className="material-symbols-outlined text-[14px] text-primary">speed</span>
        {Math.max(0, s1.velocityKmh).toFixed(0)} / {Math.max(0, s2.velocityKmh).toFixed(0)} km/h
      </button>
    );
  }

  return (
    <div className="absolute top-3 left-3 rounded-xl bg-white/95 backdrop-blur-sm shadow-md border border-black/10 px-3 py-2.5 text-foreground text-[11px] space-y-1 min-w-[175px] max-w-[210px]">
      <div className="flex items-center justify-between gap-2 mb-1">
        <p className="text-[9px] font-bold uppercase tracking-widest text-muted-foreground">Live Reconstruction</p>
        <button onClick={() => setCollapsed(true)} className="text-muted-foreground hover:text-foreground -mr-1 -mt-1 p-1">
          <span className="material-symbols-outlined text-[14px]">expand_less</span>
        </button>
      </div>
      <div className="flex items-center justify-between gap-3">
        <span className="text-blue-600 font-bold shrink-0">V1</span>
        <span className="truncate flex-1 text-muted-foreground">{v1Info?.model || v1Info?.make || "Vehicle 1"}</span>
        <span className="tabular-nums font-bold shrink-0">{Math.max(0, s1.velocityKmh).toFixed(0)} km/h</span>
      </div>
      <div className="flex items-center justify-between gap-3">
        <span className="text-red-600 font-bold shrink-0">V2</span>
        <span className="truncate flex-1 text-muted-foreground">{v2Info?.model || v2Info?.make || "Vehicle 2"}</span>
        <span className="tabular-nums font-bold shrink-0">{Math.max(0, s2.velocityKmh).toFixed(0)} km/h</span>
      </div>
      <div className="pt-1 mt-1 border-t border-border space-y-0.5">
        <div className="flex items-center justify-between">
          <span className="text-muted-foreground">Impact Angle</span>
          <span className="tabular-nums font-semibold">{angle != null ? `${Math.round(angle)}°` : "—"}</span>
        </div>
        <div className="flex items-center justify-between">
          <span className="text-muted-foreground">ΔV</span>
          <span className="tabular-nums font-semibold">{deltaV != null ? `${deltaV.toFixed(1)} km/h` : "—"}</span>
        </div>
        <div className="flex items-center justify-between">
          <span className="text-muted-foreground">Impact Force</span>
          <span className="tabular-nums font-semibold">
            {force != null ? `${physics.impact_force_is_estimated ? "~" : ""}${(force / 1000).toFixed(0)} kN` : "unavailable"}
          </span>
        </div>
      </div>
      <p className="pt-1 mt-1 border-t border-border text-muted-foreground/70 text-[9px]">t = {tSim >= 0 ? "+" : ""}{tSim.toFixed(2)}s</p>
    </div>
  );
}
