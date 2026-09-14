import { PhysicsReconstruction, VehicleInfo } from "./types";
import { BASE_URL } from "@/lib/api";

export default function CollisionSummaryCard({
  physics, v1Info, v2Info, claimId, onReplay, onViewEvidence,
}: {
  physics: PhysicsReconstruction; v1Info?: VehicleInfo; v2Info?: VehicleInfo; claimId: string;
  onReplay: () => void; onViewEvidence: () => void;
}) {
  const c = physics.comparison;
  const verdictOk = physics.physics_verdict === "CONSISTENT";

  return (
    <div className="rounded-2xl border border-border bg-card shadow-sm p-5 space-y-4">
      <div className="flex items-center justify-between">
        <p className="text-[10px] font-black uppercase tracking-widest text-muted-foreground">Reconstruction Complete</p>
        <span className={`text-[9px] font-black uppercase px-2 py-0.5 rounded-full ${verdictOk ? "bg-primary/10 text-primary" : "bg-amber-500/10 text-amber-700"}`}>
          {physics.physics_verdict || "N/A"}
        </span>
      </div>
      <p className="text-sm font-bold text-foreground">
        {(v1Info?.model || v1Info?.make || "Vehicle 1")} × {(v2Info?.model || v2Info?.make || "Vehicle 2")}
      </p>
      <div className="grid grid-cols-2 gap-x-4 gap-y-1.5 text-xs">
        <span className="text-muted-foreground">Impact angle</span><span className="text-right font-bold tabular-nums">{c?.approach_angle_deg != null ? `${Math.round(c.approach_angle_deg)}°` : "—"}</span>
        <span className="text-muted-foreground">V1 speed</span><span className="text-right font-bold tabular-nums">{c?.speed_v1_kmh?.reconstructed != null ? `${c.speed_v1_kmh.reconstructed} km/h` : "—"}</span>
        <span className="text-muted-foreground">V2 speed</span><span className="text-right font-bold tabular-nums">{c?.speed_v2_kmh?.reconstructed != null ? `${c.speed_v2_kmh.reconstructed} km/h` : "—"}</span>
        <span className="text-muted-foreground">ΔV</span><span className="text-right font-bold tabular-nums">{physics.delta_v_kmh != null ? `${physics.delta_v_kmh.toFixed(1)} km/h` : "—"}</span>
        <span className="text-muted-foreground">Crush depth</span><span className="text-right font-bold tabular-nums">{c?.crush_depth_mm?.claimed != null ? `${c.crush_depth_mm.claimed} mm` : "—"}</span>
      </div>
      <div className="pt-3 border-t border-dashed border-border flex items-center justify-between">
        <span className="text-xs font-bold text-foreground">Physics score</span>
        <span className="text-sm font-black text-foreground tabular-nums">{physics.physics_fraud_score ?? "—"} / 100</span>
      </div>
      {physics.confidence != null && (
        <div className="flex items-center justify-between">
          <span className="text-xs font-bold text-foreground">Confidence</span>
          <span className="text-sm font-black text-foreground tabular-nums">{Math.round(physics.confidence * 100)}%</span>
        </div>
      )}
      <div className="flex flex-wrap gap-2 pt-2">
        <button onClick={onReplay} className="flex items-center gap-1.5 px-3 py-1.5 rounded-full text-[10px] font-bold bg-primary text-primary-foreground hover:bg-primary/90 transition-colors">
          <span className="material-symbols-outlined text-[14px]">replay</span> Replay
        </button>
        <button onClick={onViewEvidence} className="flex items-center gap-1.5 px-3 py-1.5 rounded-full text-[10px] font-bold border border-border hover:bg-muted transition-colors">
          <span className="material-symbols-outlined text-[14px]">fact_check</span> View Evidence
        </button>
        <a
          href={`${BASE_URL}/api/analysis/claims/${claimId}/simulation-video`}
          download={`collision_simulation_${claimId}.mp4`}
          className="flex items-center gap-1.5 px-3 py-1.5 rounded-full text-[10px] font-bold border border-border hover:bg-muted transition-colors"
        >
          <span className="material-symbols-outlined text-[14px]">download</span> Export Video
        </a>
      </div>
    </div>
  );
}
