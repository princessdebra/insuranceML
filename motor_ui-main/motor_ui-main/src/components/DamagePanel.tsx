import { useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { DamageDetection, DamageZone, saveDamageDecision, BASE_URL } from "@/lib/api";

/**
 * AI-powered vehicle damage detection: draws the trained CV model's
 * bounding boxes/polygons on the photo (solid outline) plus a broader
 * whole-photo scan for additional damaged parts the detector itself didn't
 * flag (dashed outline, approximate position only) -- lists each one with
 * its AI repair/replace recommendation, confidence, and reason. The
 * assessor confirms or overrides each one -- "AI recommends, assessor
 * decides," never the other way around. Nothing here is auto-applied to
 * the claim.
 *
 * Zone indices are offset by 100 in the saved-decision keying so they
 * never collide with YOLO detection indices in the same
 * (claim, filename, index) decision table.
 */
const ZONE_INDEX_OFFSET = 100;

type Row = {
  key: string;
  decisionIndex: number;
  component: string;
  finding: string;
  confidence: number;
  lowConfidence: boolean;
  recommendedAction: "repair" | "replace";
  reason: string;
  source: "yolo" | "scan";
  bboxNormalized: number[] | null; // [x1,y1,x2,y2] as 0-1 fractions
  bboxPixels: number[] | null;     // [x1,y1,x2,y2] in original image pixels (YOLO only)
  polygonPixels: number[][] | null;
};

export default function DamagePanel({
  claimId,
  photoId,
  filename,
  detections,
  damageZones,
  assessorId,
  existingDecisions,
  onDecided,
}: {
  claimId: string;
  photoId: number;
  filename: string;
  detections: DamageDetection[];
  damageZones?: DamageZone[];
  assessorId: string;
  existingDecisions: Record<number, string>;
  onDecided?: () => void;
}) {
  const imgRef = useRef<HTMLImageElement>(null);
  const [naturalSize, setNaturalSize] = useState<{ w: number; h: number } | null>(null);
  const [hoveredKey, setHoveredKey] = useState<string | null>(null);
  const [savingKey, setSavingKey] = useState<string | null>(null);
  const [decisions, setDecisions] = useState<Record<number, string>>(existingDecisions);
  const [error, setError] = useState("");

  const rows: Row[] = [
    ...(detections || []).map((det, i): Row => ({
      key: `yolo-${i}`,
      decisionIndex: i,
      component: det.component,
      finding: det.class.replace(/-/g, " "),
      confidence: det.confidence,
      lowConfidence: !!det.low_confidence,
      recommendedAction: det.recommended_action,
      reason: det.reason,
      source: "yolo",
      bboxNormalized: null,
      bboxPixels: det.bbox,
      polygonPixels: det.polygon || null,
    })),
    ...(damageZones || []).map((zone, i): Row => ({
      key: `scan-${i}`,
      decisionIndex: ZONE_INDEX_OFFSET + i,
      component: zone.part,
      finding: zone.damage_type,
      confidence: zone.confidence,
      lowConfidence: zone.confidence < 0.4,
      recommendedAction: zone.recommended_action,
      reason: zone.description,
      source: "scan",
      bboxNormalized: zone.bbox_normalized,
      bboxPixels: null,
      polygonPixels: null,
    })),
  ];

  const handleDecide = async (row: Row, decision: "repair" | "replace") => {
    setSavingKey(row.key);
    setError("");
    try {
      const res = await saveDamageDecision({
        claimId,
        filename,
        detectionIndex: row.decisionIndex,
        component: row.component,
        aiRecommendation: row.recommendedAction,
        assessorDecision: decision,
        assessorId,
      });
      if (res.success) {
        setDecisions((d) => ({ ...d, [row.decisionIndex]: decision }));
        onDecided?.();
      } else {
        setError(res.detail || "Could not save decision.");
      }
    } catch {
      setError("Could not save decision — check your connection.");
    } finally {
      setSavingKey(null);
    }
  };

  if (rows.length === 0) return null;

  const boxColor = (action: string) => (action === "replace" ? "#ef4444" : "#f59e0b");

  return (
    <div className="space-y-4">
      <div className="relative inline-block max-w-full border rounded-xl overflow-hidden bg-black/5">
        <img
          ref={imgRef}
          src={`${BASE_URL}/api/analysis/photos/${photoId}/file`}
          alt={filename}
          className="max-w-full h-auto block"
          onLoad={(e) => {
            const img = e.currentTarget;
            setNaturalSize({ w: img.naturalWidth, h: img.naturalHeight });
          }}
        />
        {/* YOLO detections: solid outline, traces the model's actual damage
            shape (segmentation polygon) when available, else the bbox.
            Scan zones: dashed outline, approximate position only -- the
            whole-photo scan estimates a rough box, not a real detection. */}
        {naturalSize && (
          <svg
            className="absolute inset-0 w-full h-full pointer-events-none"
            viewBox="0 0 100 100"
            preserveAspectRatio="none"
          >
            {rows.map((row) => {
              const active = hoveredKey === row.key;
              const color = boxColor(row.recommendedAction);
              const commonProps = {
                fill: color,
                fillOpacity: active ? 0.28 : 0.14,
                stroke: color,
                strokeWidth: active ? 1 : 0.6,
                strokeDasharray: row.source === "scan" ? "2 1.5" : undefined,
                vectorEffect: "non-scaling-stroke" as const,
                className: "cursor-pointer pointer-events-auto transition-all",
                onMouseEnter: () => setHoveredKey(row.key),
                onMouseLeave: () => setHoveredKey((h) => (h === row.key ? null : h)),
              };
              if (row.source === "yolo" && row.polygonPixels && row.polygonPixels.length >= 3) {
                const points = row.polygonPixels
                  .map(([x, y]) => `${(x / naturalSize.w) * 100},${(y / naturalSize.h) * 100}`)
                  .join(" ");
                return <polygon key={row.key} points={points} {...commonProps} />;
              }
              let x1 = 0, y1 = 0, x2 = 0, y2 = 0;
              if (row.source === "yolo" && row.bboxPixels) {
                [x1, y1, x2, y2] = row.bboxPixels;
                x1 = (x1 / naturalSize.w) * 100; x2 = (x2 / naturalSize.w) * 100;
                y1 = (y1 / naturalSize.h) * 100; y2 = (y2 / naturalSize.h) * 100;
              } else if (row.bboxNormalized) {
                [x1, y1, x2, y2] = row.bboxNormalized.map((v) => v * 100);
              } else {
                return null;
              }
              return <rect key={row.key} x={x1} y={y1} width={x2 - x1} height={y2 - y1} rx={1} {...commonProps} />;
            })}
          </svg>
        )}
        {naturalSize && rows.map((row) => {
          let leftPct: number, topPct: number;
          if (row.source === "yolo" && row.polygonPixels?.length) {
            const [x, y] = row.polygonPixels.reduce((min, p) => (p[1] < min[1] ? p : min));
            leftPct = (x / naturalSize.w) * 100;
            topPct = (y / naturalSize.h) * 100;
          } else if (row.source === "yolo" && row.bboxPixels) {
            leftPct = (row.bboxPixels[0] / naturalSize.w) * 100;
            topPct = (row.bboxPixels[1] / naturalSize.h) * 100;
          } else if (row.bboxNormalized) {
            leftPct = row.bboxNormalized[0] * 100;
            topPct = row.bboxNormalized[1] * 100;
          } else {
            return null;
          }
          const active = hoveredKey === row.key;
          // Alternate the label above/below its marker by index so two
          // zones that land close together don't both try to occupy the
          // same strip of space above the marker.
          const rowIdx = rows.findIndex((r) => r.key === row.key);
          const labelBelow = rowIdx % 2 === 1;
          return (
            <div key={row.key} className="absolute" style={{ left: `${leftPct}%`, top: `${topPct}%`, zIndex: active ? 20 : 2 }}>
              <span
                className="absolute block size-1.5 rounded-full border border-white shadow-sm pointer-events-none"
                style={{ transform: "translate(-50%, -50%)", backgroundColor: boxColor(row.recommendedAction) }}
              />
              {/* Always-visible full part name -- shown directly on the
                  photo rather than requiring hover, per assessor
                  preference for reading real names at a glance. */}
              <span
                className="absolute text-[9px] font-bold uppercase px-1.5 py-0.5 rounded whitespace-nowrap text-white pointer-events-auto cursor-pointer transition-transform"
                style={{
                  left: "50%",
                  ...(labelBelow ? { top: "calc(50% + 6px)" } : { bottom: "calc(50% + 6px)" }),
                  transform: `translateX(-50%) scale(${active ? 1.1 : 1})`,
                  backgroundColor: boxColor(row.recommendedAction),
                  zIndex: active ? 21 : undefined,
                }}
                onMouseEnter={() => setHoveredKey(row.key)}
                onMouseLeave={() => setHoveredKey((h) => (h === row.key ? null : h))}
              >
                {row.component}
              </span>
            </div>
          );
        })}
      </div>

      <div className="flex items-center gap-4 text-[10px] text-muted-foreground">
        <span className="flex items-center gap-1.5">
          <span className="inline-block w-4 h-2.5 border-2 border-foreground/60 rounded-sm" />
          Detector-confirmed region
        </span>
        <span className="flex items-center gap-1.5">
          <span className="inline-block w-4 h-2.5 border-2 border-dashed border-foreground/60 rounded-sm" />
          Whole-photo scan (approximate position)
        </span>
      </div>

      <div className="overflow-x-auto">
        <table className="w-full text-left border-collapse text-xs">
          <thead>
            <tr className="bg-muted/50">
              <th className="px-3 py-2 font-black uppercase text-[10px] text-muted-foreground">Component</th>
              <th className="px-3 py-2 font-black uppercase text-[10px] text-muted-foreground">AI Finding</th>
              <th className="px-3 py-2 font-black uppercase text-[10px] text-muted-foreground">Confidence</th>
              <th className="px-3 py-2 font-black uppercase text-[10px] text-muted-foreground">AI Recommendation</th>
              <th className="px-3 py-2 font-black uppercase text-[10px] text-muted-foreground">Assessor Decision</th>
            </tr>
          </thead>
          <tbody className="divide-y">
            {rows.map((row, rowIndex) => {
              const decided = decisions[row.decisionIndex];
              return (
                <tr
                  key={row.key}
                  className={`hover:bg-muted/30 transition-colors ${hoveredKey === row.key ? "bg-muted/40" : ""}`}
                  onMouseEnter={() => setHoveredKey(row.key)}
                  onMouseLeave={() => setHoveredKey((h) => (h === row.key ? null : h))}
                >
                  <td className="px-3 py-3 font-bold text-foreground whitespace-nowrap">
                    <span
                      className="inline-flex items-center justify-center size-5 rounded-full text-[10px] font-black text-white mr-1.5 align-middle"
                      style={{ backgroundColor: boxColor(row.recommendedAction) }}
                    >
                      {rowIndex + 1}
                    </span>
                    {row.component}
                    {row.source === "scan" && (
                      <span className="ml-1.5 text-[8px] font-bold uppercase text-muted-foreground align-middle" title="Found via whole-photo scan, not the trained detector">
                        (scan)
                      </span>
                    )}
                  </td>
                  <td className="px-3 py-3 text-muted-foreground whitespace-nowrap">{row.finding}</td>
                  <td className="px-3 py-3 whitespace-nowrap">
                    <span className={`font-mono font-semibold ${row.lowConfidence ? "text-amber-600" : ""}`}>
                      {Math.round(row.confidence * 100)}%
                    </span>
                    {row.lowConfidence && (
                      <span
                        className="ml-1.5 inline-flex items-center text-amber-600 align-middle"
                        title="Low-confidence finding — verify against the physical vehicle before relying on this"
                      >
                        <span className="material-symbols-outlined text-[14px]">warning</span>
                      </span>
                    )}
                  </td>
                  <td className="px-3 py-3 min-w-[220px]">
                    <Badge
                      className={`uppercase text-[9px] font-black mb-1 ${row.recommendedAction === "replace" ? "bg-destructive" : "bg-amber-500"}`}
                    >
                      {row.recommendedAction}
                    </Badge>
                    <p className="text-[10px] text-muted-foreground italic leading-snug mt-1">{row.reason}</p>
                  </td>
                  <td className="px-3 py-3 whitespace-nowrap">
                    {decided ? (
                      <span className={`inline-flex items-center gap-1 text-[10px] font-black uppercase ${decided === "replace" ? "text-destructive" : "text-emerald-600"}`}>
                        <span className="material-symbols-outlined text-[14px]">check_circle</span>
                        {decided}
                      </span>
                    ) : (
                      <div className="flex gap-1.5">
                        <Button
                          size="sm"
                          variant="outline"
                          disabled={savingKey === row.key}
                          className="h-7 text-[10px] px-2.5 border-amber-500/40 text-amber-700 hover:bg-amber-500/10"
                          onClick={() => handleDecide(row, "repair")}
                        >
                          Repair
                        </Button>
                        <Button
                          size="sm"
                          variant="outline"
                          disabled={savingKey === row.key}
                          className="h-7 text-[10px] px-2.5 border-destructive/40 text-destructive hover:bg-destructive/10"
                          onClick={() => handleDecide(row, "replace")}
                        >
                          Replace
                        </Button>
                      </div>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {error && <p className="text-xs text-destructive font-semibold">{error}</p>}
    </div>
  );
}
