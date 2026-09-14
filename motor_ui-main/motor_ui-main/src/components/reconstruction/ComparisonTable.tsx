import { PhysicsComparison } from "./types";

export default function ComparisonTable({ comparison }: { comparison?: PhysicsComparison }) {
  if (!comparison) return null;

  const rows = [
    { label: "V1 Speed", claimed: comparison.speed_v1_kmh?.claimed, physics: comparison.speed_v1_kmh?.reconstructed, unit: "km/h", delta: comparison.speed_v1_kmh?.delta, flagged: comparison.velocity_fraud_flag },
    { label: "V2 Speed", claimed: comparison.speed_v2_kmh?.claimed, physics: comparison.speed_v2_kmh?.reconstructed, unit: "km/h", delta: comparison.speed_v2_kmh?.reconstructed != null && comparison.speed_v2_kmh?.claimed != null ? comparison.speed_v2_kmh.reconstructed - comparison.speed_v2_kmh.claimed : undefined, flagged: false },
    { label: "Crush Depth", claimed: comparison.crush_depth_mm?.claimed, physics: comparison.crush_depth_mm?.reconstructed, unit: "mm", delta: comparison.crush_depth_mm?.delta, flagged: Math.abs(comparison.crush_depth_mm?.delta ?? 0) > 80 },
    { label: "Impact Angle", claimed: comparison.approach_angle_deg, physics: comparison.approach_angle_deg, unit: "°", delta: 0, flagged: false },
  ].filter((r) => r.claimed != null || r.physics != null);

  if (rows.length === 0) return null;

  return (
    <div className="overflow-x-auto rounded-2xl border border-border bg-card shadow-sm">
      <table className="w-full text-xs">
        <thead>
          <tr className="border-b border-border bg-muted/30 text-[10px] font-black uppercase tracking-widest text-muted-foreground">
            <th className="text-left px-4 py-2.5">Parameter</th>
            <th className="text-right px-4 py-2.5">Reported</th>
            <th className="text-right px-4 py-2.5">Physics</th>
            <th className="text-right px-4 py-2.5">Difference</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.label} className="border-b border-border last:border-0">
              <td className="px-4 py-2.5 font-semibold text-foreground flex items-center gap-2">
                {r.label}
                {r.flagged && <span className="material-symbols-outlined text-[14px] text-destructive">flag</span>}
              </td>
              <td className="px-4 py-2.5 text-right tabular-nums text-muted-foreground">{r.claimed != null ? `${r.claimed}${r.unit}` : "—"}</td>
              <td className="px-4 py-2.5 text-right tabular-nums font-bold text-foreground">{r.physics != null ? `${r.physics}${r.unit}` : "—"}</td>
              <td className={`px-4 py-2.5 text-right tabular-nums font-bold ${r.flagged ? "text-destructive" : "text-muted-foreground"}`}>
                {r.delta != null ? `${r.delta >= 0 ? "+" : ""}${r.delta.toFixed(1)}${r.unit}` : "—"}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
