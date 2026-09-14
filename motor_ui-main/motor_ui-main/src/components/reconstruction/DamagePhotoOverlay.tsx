import { useState } from "react";
import { BASE_URL } from "@/lib/api";
import { DamageZoneWithParty } from "./types";

export type OverlayPhoto = { id: number; filename: string; zones: DamageZoneWithParty[] };

/** Split view: reconstruction on one side (rendered by the caller), the
 * actual uploaded damage photo on this side with the AI-detected impact
 * zone(s) drawn over it using the real bbox_normalized + confidence already
 * produced by part_identifier.py -- no new CV work, just surfacing it. */
export default function DamagePhotoOverlay({ photos }: { photos: OverlayPhoto[] }) {
  const [activeIdx, setActiveIdx] = useState(0);
  if (!photos.length) {
    return (
      <div className="rounded-2xl border border-border bg-card shadow-sm p-6 text-center text-xs text-muted-foreground">
        No damage photos with detected zones are available for this claim.
      </div>
    );
  }
  const photo = photos[activeIdx];

  return (
    <div className="rounded-2xl border border-border bg-card shadow-sm overflow-hidden">
      <div className="px-4 py-3 border-b border-border flex items-center justify-between gap-2">
        <p className="text-[10px] font-black uppercase tracking-widest text-muted-foreground">AI Detected Impact Zone</p>
        {photos.length > 1 && (
          <div className="flex items-center gap-1">
            {photos.map((p, i) => (
              <button key={p.id} onClick={() => setActiveIdx(i)} className={`size-2 rounded-full ${i === activeIdx ? "bg-primary" : "bg-muted"}`} />
            ))}
          </div>
        )}
      </div>
      <div className="relative bg-black/90">
        <img src={`${BASE_URL}/api/analysis/photos/${photo.id}/file`} alt={photo.filename} className="w-full max-h-[420px] object-contain" />
        {photo.zones.map((z, i) => {
          if (!z.bbox_normalized || z.bbox_normalized.length !== 4) return null;
          const [x1, y1, x2, y2] = z.bbox_normalized;
          return (
            <div
              key={i}
              className="absolute border-2 border-amber-400"
              style={{ left: `${x1 * 100}%`, top: `${y1 * 100}%`, width: `${(x2 - x1) * 100}%`, height: `${(y2 - y1) * 100}%` }}
            >
              <span className="absolute -top-6 left-0 whitespace-nowrap bg-amber-400 text-black text-[9px] font-black uppercase px-1.5 py-0.5 rounded">
                {z.part} · {Math.round(z.confidence * 100)}%
              </span>
            </div>
          );
        })}
      </div>
      {photo.zones[0] && (
        <div className="px-4 py-3 text-xs text-foreground/80">
          <span className="font-bold">{photo.zones[0].part}</span> — {photo.zones[0].description}
        </div>
      )}
    </div>
  );
}
