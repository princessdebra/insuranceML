import { CameraMode } from "./types";
import { CameraView } from "./ReconstructionCanvas";

const PRESETS: { mode: CameraMode; label: string; icon: string; view: (halfExtent: number, v1: { x: number; y: number }, v2: { x: number; y: number }) => CameraView }[] = [
  { mode: "overview", label: "Overview", icon: "visibility", view: () => ({ cx: 0, cy: 0, scale: 1 }) },
  { mode: "approach", label: "Approach", icon: "route", view: (_h, v1, v2) => ({ cx: (v1.x + v2.x) / 4, cy: (v1.y + v2.y) / 4, scale: 1.3 }) },
  { mode: "impact", label: "Impact", icon: "adjust", view: () => ({ cx: 0, cy: 0, scale: 2.6 }) },
  { mode: "v1", label: "Vehicle 1", icon: "directions_car", view: (_h, v1) => ({ cx: v1.x, cy: v1.y, scale: 2.2 }) },
  { mode: "v2", label: "Vehicle 2", icon: "local_shipping", view: (_h, _v1, v2) => ({ cx: v2.x, cy: v2.y, scale: 2.2 }) },
  { mode: "free", label: "Free", icon: "pan_tool", view: (_h, _v1, _v2) => ({ cx: 0, cy: 0, scale: 1 }) },
];

export default function CameraControls({
  active, onSelect,
}: {
  active: CameraMode; onSelect: (mode: CameraMode) => void;
}) {
  return (
    <div className="flex items-center gap-1.5 flex-wrap">
      {PRESETS.map((p) => (
        <button
          key={p.mode}
          onClick={() => onSelect(p.mode)}
          className={`flex items-center gap-1 px-2.5 py-1.5 rounded-full text-[10px] font-bold transition-colors ${active === p.mode ? "bg-primary text-primary-foreground" : "border border-border text-muted-foreground hover:bg-muted"}`}
        >
          <span className="material-symbols-outlined text-[14px]">{p.icon}</span>
          {p.label}
        </button>
      ))}
    </div>
  );
}

export { PRESETS as CAMERA_PRESETS };
