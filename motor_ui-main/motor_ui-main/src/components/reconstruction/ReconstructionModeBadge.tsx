import { PhysicsReconstruction } from "./types";

export default function ReconstructionModeBadge({ physics }: { physics: PhysicsReconstruction }) {
  const isPathway1 = physics.pathway === "pathway_1";
  return (
    <div className="flex items-center gap-2 flex-wrap">
      <span className="inline-flex items-center gap-1.5 px-3 py-1 rounded-full bg-primary/10 text-primary text-[10px] font-black uppercase tracking-wide">
        <span className="material-symbols-outlined text-[14px]">{isPathway1 ? "sensors" : "description"}</span>
        Pathway {isPathway1 ? "1 · Telemetry Reconstruction" : "2 · Narrative Reconstruction"}
      </span>
      <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full bg-muted text-muted-foreground text-[10px] font-bold uppercase tracking-wide">
        <span className="material-symbols-outlined text-[13px]">memory</span>
        Simulation: {physics.simulation_method === "pybullet" ? "PyBullet" : "Analytical"}
      </span>
      {isPathway1 && (
        <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full bg-emerald-500/10 text-emerald-600 text-[10px] font-bold uppercase tracking-wide">
          <span className="material-symbols-outlined text-[13px]">verified_user</span>
          HMAC verified
        </span>
      )}
    </div>
  );
}
