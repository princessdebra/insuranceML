import { VisibleLayers } from "./types";

const LAYER_META: { key: keyof VisibleLayers; label: string }[] = [
  { key: "trajectories", label: "Trajectories" },
  { key: "velocityVectors", label: "Velocity vectors" },
  { key: "impactForce", label: "Impact force" },
  { key: "skidMarks", label: "Skid marks" },
  { key: "impactZone", label: "Impact zone" },
  { key: "grid", label: "Coordinate grid" },
  { key: "labels", label: "Vehicle labels" },
  { key: "damageOverlay", label: "Damage overlay" },
];

export default function LayersControl({
  layers, onChange, measuring, onToggleMeasuring,
}: {
  layers: VisibleLayers; onChange: (l: VisibleLayers) => void; measuring: boolean; onToggleMeasuring: () => void;
}) {
  return (
    <div className="rounded-2xl border border-border bg-card shadow-sm p-4">
      <p className="text-[10px] font-black uppercase tracking-widest text-muted-foreground mb-3">Show Physics</p>
      <div className="space-y-2">
        {LAYER_META.map((l) => (
          <label key={l.key} className="flex items-center gap-2 text-xs text-foreground cursor-pointer">
            <input
              type="checkbox"
              checked={layers[l.key]}
              onChange={(e) => onChange({ ...layers, [l.key]: e.target.checked })}
              className="size-3.5 rounded border-border accent-primary"
            />
            {l.label}
          </label>
        ))}
      </div>
      <div className="mt-3 pt-3 border-t border-border">
        <button
          onClick={onToggleMeasuring}
          className={`w-full flex items-center justify-center gap-1.5 px-3 py-1.5 rounded-full text-[10px] font-bold transition-colors ${measuring ? "bg-primary text-primary-foreground" : "border border-border text-muted-foreground hover:bg-muted"}`}
        >
          <span className="material-symbols-outlined text-[14px]">straighten</span>
          {measuring ? "Click two points…" : "Measure Distance"}
        </button>
      </div>
    </div>
  );
}
