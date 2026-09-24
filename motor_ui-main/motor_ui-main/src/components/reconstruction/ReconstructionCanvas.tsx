import { useEffect, useRef, useState, useCallback } from "react";
import { SimulationTimeline, VisibleLayers, VehicleInfo, DamageZoneWithParty, PhysicsReconstruction } from "./types";
import { interpState, isBraking, sceneHalfExtent } from "./interpolateTelemetry";

export type CameraView = { cx: number; cy: number; scale: number };

// Light, map-style palette (Google-Maps-like: pale land, white/light roads,
// dark labels on white pill backgrounds) instead of the previous dark CAD
// scheme -- easier to read at a glance and matches what the user actually
// asked this to feel like.
const ROAD_COLOR = "#ffffff";
const ROAD_BORDER = "#d7dade";
const LANE_COLOR = "#e4b429";
const GROUND_COLOR = "#e8ebe3";
const GRID_COLOR = "rgba(60,64,67,0.08)";
const V1_COLOR = "#1a73e8";
const V2_COLOR = "#d33b27";
const IMPACT_COLOR = "#e8710a";
const SKID_COLOR = "#1a1a1a";
const TEXT_COLOR = "#202124";
const MUTED_TEXT = "rgba(32,33,36,0.55)";
const LABEL_BG = "rgba(255,255,255,0.92)";

function bodyTypePath(ctx: CanvasRenderingContext2D, halfLen: number, halfWid: number, bodyType?: string) {
  // Parametric top-down outline per body type -- a clean technical/CAD
  // silhouette (tapered nose/tail, not a plain rectangle), varied by the
  // real length/width ratio and body_type from vehicle_registry.py. No
  // raster assets exist for these, and the spec explicitly asks for
  // forensic-diagram silhouettes rather than cartoon vehicles.
  ctx.beginPath();
  const bt = (bodyType || "saloon").toLowerCase();
  const noseTaper = bt === "motorcycle" ? 0.75 : bt === "suv" || bt === "pickup" ? 0.35 : bt === "heavy_commercial" || bt === "matatu" ? 0.12 : 0.45;
  const tailTaper = bt === "motorcycle" ? 0.75 : 0.15;
  ctx.moveTo(-halfLen, -halfWid * (1 - noseTaper * 0.3));
  ctx.lineTo(halfLen - halfLen * noseTaper * 0.5, -halfWid);
  ctx.lineTo(halfLen, -halfWid * 0.25);
  ctx.lineTo(halfLen, halfWid * 0.25);
  ctx.lineTo(halfLen - halfLen * noseTaper * 0.5, halfWid);
  ctx.lineTo(-halfLen, halfWid * (1 - noseTaper * 0.3));
  ctx.lineTo(-halfLen - halfLen * tailTaper * 0.15, 0);
  ctx.closePath();
}

function drawVehicle(
  ctx: CanvasRenderingContext2D,
  xM: number, yM: number, headingRad: number,
  lengthM: number, widthM: number,
  color: string, label: string, subLabel: string,
  toPx: (m: number) => number,
  bodyType?: string,
) {
  // A car this small on screen when zoomed out on a long pre-impact
  // approach (real telemetry spans 100m+) previously rendered as a near-
  // invisible sliver a couple of pixels wide -- floor the ON-SCREEN size so
  // a vehicle is always a recognizable shape, at the cost of exact-scale
  // accuracy when very zoomed out (the world-accurate trajectory line still
  // shows the true path either way).
  const MIN_HALF_LEN_PX = 18;
  const MIN_HALF_WID_PX = 8;
  const halfLen = Math.max(MIN_HALF_LEN_PX, toPx(lengthM) / 2);
  const halfWid = Math.max(MIN_HALF_WID_PX, toPx(widthM) / 2);

  ctx.save();
  ctx.translate(xM, yM);
  ctx.rotate(headingRad);

  // Wheels -- four dark rounded rects near each corner, the single biggest
  // visual cue that reads as "car" rather than "rounded rectangle" at a
  // glance.
  ctx.fillStyle = "rgba(10,10,12,0.9)";
  const wheelInsetX = halfLen * 0.62, wheelInsetY = halfWid * 1.08, wheelLen = halfLen * 0.34, wheelWid = halfWid * 0.22;
  [[-wheelInsetX, -wheelInsetY], [wheelInsetX, -wheelInsetY], [-wheelInsetX, wheelInsetY], [wheelInsetX, wheelInsetY]].forEach(([wx, wy]) => {
    ctx.beginPath();
    ctx.roundRect(wx - wheelLen / 2, wy - wheelWid / 2, wheelLen, wheelWid, 2);
    ctx.fill();
  });

  bodyTypePath(ctx, halfLen, halfWid, bodyType);
  ctx.fillStyle = color;
  ctx.globalAlpha = 0.95;
  ctx.fill();
  ctx.globalAlpha = 1;
  ctx.strokeStyle = "rgba(255,255,255,0.9)";
  ctx.lineWidth = 1.4;
  ctx.stroke();

  // Roof panel -- a visibly narrower inset rectangle reads as a cabin from
  // directly above, the second key "this is a car, not a blob" cue.
  ctx.beginPath();
  ctx.roundRect(-halfLen * 0.22, -halfWid * 0.72, halfLen * 0.85, halfWid * 1.44, Math.min(halfWid * 0.3, 4));
  ctx.fillStyle = "rgba(0,0,0,0.22)";
  ctx.fill();

  // Windshield + rear-screen hints on the roof panel's short edges.
  ctx.fillStyle = "rgba(255,255,255,0.24)";
  ctx.fillRect(halfLen * 0.5, -halfWid * 0.68, halfLen * 0.14, halfWid * 1.36);
  ctx.fillRect(-halfLen * 0.24, -halfWid * 0.68, halfLen * 0.1, halfWid * 1.36);
  ctx.restore();

  if (!label && !subLabel) return;

  // Label -- kept upright and constant size regardless of zoom/heading, a
  // small white "map pin" pill behind the text rather than a drop shadow
  // (a dark-background shadow trick that stopped working once the canvas
  // moved to a light map-style background).
  ctx.save();
  ctx.translate(xM, yM);
  const text = label && subLabel ? `${label} · ${subLabel}` : label || subLabel;
  ctx.font = "bold 10px ui-sans-serif, system-ui";
  const textW = ctx.measureText(text).width;
  const pillY = -halfWid - 22;
  ctx.fillStyle = LABEL_BG;
  ctx.beginPath();
  ctx.roundRect(-textW / 2 - 6, pillY - 9, textW + 12, 18, 9);
  ctx.fill();
  ctx.strokeStyle = "rgba(0,0,0,0.08)";
  ctx.lineWidth = 1;
  ctx.stroke();
  ctx.fillStyle = TEXT_COLOR;
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  ctx.fillText(text, 0, pillY);
  ctx.restore();
}

/** A fixed object (wall/pillar/barrier) is not a vehicle and must never be
 * drawn as one -- a car-shaped V2 that's actually a stationary wall reads as
 * "another car sitting in the road" to an assessor, not "the thing this
 * vehicle hit". Draws a concrete-colored block spanning the road's width
 * instead, with a hatched texture and a plain label. */
function drawBarrier(
  ctx: CanvasRenderingContext2D,
  xM: number, yM: number, headingRad: number,
  toPx: (m: number) => number,
  label: string,
) {
  // No pixel-floor here (unlike drawVehicle's minimum-size floor) -- a
  // fixed 60px minimum regardless of zoom made the wall visually swallow
  // the approaching car while they were genuinely still meters apart in
  // the real trajectory data, since 8m of real wall was being forced to
  // look far wider on screen than the current zoom level's actual scale.
  // A wall is already wide (8m) at true scale; it doesn't need an
  // artificial floor the way a small vehicle silhouette does.
  const halfLen = toPx(0.6);
  const halfWid = toPx(4);

  ctx.save();
  ctx.translate(xM, yM);
  ctx.rotate(headingRad);

  ctx.fillStyle = "#9aa0a6";
  ctx.beginPath();
  ctx.roundRect(-halfLen, -halfWid, halfLen * 2, halfWid * 2, 3);
  ctx.fill();
  ctx.strokeStyle = "#5f6368";
  ctx.lineWidth = 1.5;
  ctx.stroke();

  // Diagonal hatch -- the standard "solid obstruction" cartographic/
  // construction-drawing convention, immediately reads as "structure" rather
  // than "vehicle" even before the label is read.
  ctx.save();
  ctx.beginPath();
  ctx.rect(-halfLen, -halfWid, halfLen * 2, halfWid * 2);
  ctx.clip();
  ctx.strokeStyle = "rgba(95,99,104,0.5)";
  ctx.lineWidth = 1;
  const step = 10;
  const diag = halfLen + halfWid;
  for (let o = -diag; o < diag; o += step) {
    ctx.beginPath();
    ctx.moveTo(o - halfWid, -halfWid);
    ctx.lineTo(o + halfWid, halfWid);
    ctx.stroke();
  }
  ctx.restore();
  ctx.restore();

  ctx.save();
  ctx.translate(xM, yM);
  ctx.font = "bold 10px ui-sans-serif, system-ui";
  const textW = ctx.measureText(label).width;
  const pillY = -halfWid - 18;
  ctx.fillStyle = LABEL_BG;
  ctx.beginPath();
  ctx.roundRect(-textW / 2 - 6, pillY - 9, textW + 12, 18, 9);
  ctx.fill();
  ctx.fillStyle = TEXT_COLOR;
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  ctx.fillText(label, 0, pillY);
  ctx.restore();
}

/** Local (heading-relative) offset of the impact-zone highlight from the
 * vehicle's center, keyed off the SAME structural-zone vocabulary
 * physics_engine.py uses (front_bumper/rear_bumper/driver_door/
 * passenger_door/rear_driver/rear_passenger/roof) -- so when the zone came
 * from a real CV-detected photo, the highlight lands on the actual damaged
 * side of the car instead of always centering on the vehicle regardless of
 * which side was really hit. */
function impactZoneOffset(zone: string | null | undefined, halfLen: number, halfWid: number): { x: number; y: number } {
  switch (zone) {
    case "front_bumper": return { x: halfLen * 0.9, y: 0 };
    case "rear_bumper": return { x: -halfLen * 0.9, y: 0 };
    case "driver_door": return { x: 0, y: -halfWid * 0.9 };
    case "passenger_door": return { x: 0, y: halfWid * 0.9 };
    case "rear_driver": return { x: -halfLen * 0.5, y: -halfWid * 0.9 };
    case "rear_passenger": return { x: -halfLen * 0.5, y: halfWid * 0.9 };
    case "roof": return { x: 0, y: 0 };
    default: return { x: 0, y: 0 };
  }
}

export default function ReconstructionCanvas({
  timeline,
  tSim,
  v1Info,
  v2Info,
  physics,
  layers,
  camera,
  onCameraChange,
  damageZones,
  measuring,
  onMeasurement,
}: {
  timeline: SimulationTimeline;
  tSim: number;
  v1Info?: VehicleInfo;
  v2Info?: VehicleInfo;
  physics: PhysicsReconstruction;
  layers: VisibleLayers;
  camera: CameraView;
  onCameraChange: (c: CameraView) => void;
  damageZones?: DamageZoneWithParty[];
  measuring: boolean;
  onMeasurement?: (distanceM: number | null) => void;
}) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const wrapRef = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState({ w: 800, h: 800 });
  const [measurePoints, setMeasurePoints] = useState<{ x: number; y: number }[]>([]);
  const dragRef = useRef<{ startX: number; startY: number; camCx: number; camCy: number } | null>(null);

  useEffect(() => {
    if (!wrapRef.current) return;
    const el = wrapRef.current;
    const ro = new ResizeObserver(() => {
      const rect = el.getBoundingClientRect();
      // Clamped to a sane max as a safety net -- the canvas's own rendered
      // pixel size must never be able to feed back into how big its
      // ancestors measure themselves (that feedback loop is what previously
      // blew the whole page out to a page-wide horizontal scrollbar).
      setSize({ w: Math.max(200, Math.min(1400, rect.width)), h: Math.max(200, Math.min(1400, rect.height)) });
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const halfExtent = sceneHalfExtent(timeline);

  const toPx = useCallback((m: number) => (m / (halfExtent * 2)) * Math.min(size.w, size.h) * camera.scale, [halfExtent, size, camera.scale]);
  const worldToScreen = useCallback((xM: number, yM: number) => {
    const cx = size.w / 2 - toPx(camera.cx);
    const cy = size.h / 2 - toPx(camera.cy);
    return { x: cx + toPx(xM), y: cy + toPx(yM) };
  }, [size, camera, toPx]);
  const screenToWorld = useCallback((xPx: number, yPx: number) => {
    const cx = size.w / 2 - toPx(camera.cx);
    const cy = size.h / 2 - toPx(camera.cy);
    return { x: (xPx - cx) / toPx(1), y: (yPx - cy) / toPx(1) };
  }, [size, camera, toPx]);

  // Real skid-mark trail: accumulated positions while isBraking() is true,
  // sampled across the pre-impact window up to the current tSim -- same
  // "only if the data supports it" rule as the Python renderer.
  const skidTrail = useCallback((telemetry: SimulationTimeline["v1_insured_telemetry"]) => {
    const pts: { x: number; y: number }[] = [];
    for (const f of telemetry) {
      if (f.time_sec > tSim) break;
      if (isBraking(telemetry, f.time_sec)) pts.push({ x: f.position[0], y: f.position[1] });
    }
    return pts;
  }, [tSim]);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    const dpr = window.devicePixelRatio || 1;
    canvas.width = size.w * dpr;
    canvas.height = size.h * dpr;
    canvas.style.width = `${size.w}px`;
    canvas.style.height = `${size.h}px`;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

    // ── Background ──────────────────────────────────────────────
    ctx.fillStyle = GROUND_COLOR;
    ctx.fillRect(0, 0, size.w, size.h);

    // ── Coordinate grid ─────────────────────────────────────────
    if (layers.grid) {
      ctx.strokeStyle = GRID_COLOR;
      ctx.lineWidth = 1;
      const step = halfExtent > 40 ? 20 : halfExtent > 15 ? 10 : 5;
      for (let m = -Math.ceil(halfExtent / step) * step; m <= halfExtent; m += step) {
        const a = worldToScreen(m, -halfExtent * 3);
        const b = worldToScreen(m, halfExtent * 3);
        ctx.beginPath(); ctx.moveTo(a.x, a.y); ctx.lineTo(b.x, b.y); ctx.stroke();
        const c = worldToScreen(-halfExtent * 3, m);
        const d = worldToScreen(halfExtent * 3, m);
        ctx.beginPath(); ctx.moveTo(c.x, c.y); ctx.lineTo(d.x, d.y); ctx.stroke();
        if (m !== 0) {
          ctx.fillStyle = MUTED_TEXT;
          ctx.font = "8px ui-monospace, monospace";
          ctx.fillText(`${m}m`, a.x + 2, worldToScreen(0, 0).y - 4);
        }
      }
      // North arrow
      const na = worldToScreen(-halfExtent * 0.9, -halfExtent * 0.9);
      ctx.strokeStyle = MUTED_TEXT;
      ctx.beginPath(); ctx.moveTo(na.x, na.y + 14); ctx.lineTo(na.x, na.y); ctx.stroke();
      ctx.beginPath(); ctx.moveTo(na.x, na.y); ctx.lineTo(na.x - 3, na.y + 5); ctx.moveTo(na.x, na.y); ctx.lineTo(na.x + 3, na.y + 5); ctx.stroke();
      ctx.fillStyle = MUTED_TEXT;
      ctx.font = "9px ui-monospace, monospace";
      ctx.fillText("N", na.x - 3, na.y - 3);
    }

    // ── Road / junction (right-angle junction: V1 along Y, V2 along X --
    // this is the fixed convention ConfigurableMultiVehicleEngine's
    // telemetry is generated in; not an assumption made here). ──
    const roadWm = Math.max(6, halfExtent * 0.12);
    const groundTL = worldToScreen(-halfExtent * 3, -halfExtent * 3);
    const groundBR = worldToScreen(halfExtent * 3, halfExtent * 3);
    ctx.fillStyle = ROAD_COLOR;
    const hRoadTL = worldToScreen(-halfExtent * 3, -roadWm / 2);
    ctx.fillRect(groundTL.x, hRoadTL.y, groundBR.x - groundTL.x, toPx(roadWm));
    const vRoadTL = worldToScreen(-roadWm / 2, -halfExtent * 3);
    ctx.fillRect(vRoadTL.x, groundTL.y, toPx(roadWm), groundBR.y - groundTL.y);

    ctx.strokeStyle = LANE_COLOR;
    ctx.lineWidth = Math.max(1, toPx(0.15));
    ctx.setLineDash([toPx(1.5), toPx(1)]);
    const originPx = worldToScreen(0, 0);
    ctx.strokeStyle = "#d4a820";
    ctx.beginPath(); ctx.moveTo(groundTL.x, originPx.y); ctx.lineTo(groundBR.x, originPx.y); ctx.stroke();
    ctx.beginPath(); ctx.moveTo(originPx.x, groundTL.y); ctx.lineTo(originPx.x, groundBR.y); ctx.stroke();
    ctx.setLineDash([]);
    ctx.strokeStyle = ROAD_BORDER;
    ctx.lineWidth = 1.5;
    [-roadWm / 2, roadWm / 2].forEach((off) => {
      const a = worldToScreen(-halfExtent * 3, off);
      const b = worldToScreen(halfExtent * 3, off);
      ctx.beginPath(); ctx.moveTo(a.x, a.y); ctx.lineTo(b.x, b.y); ctx.stroke();
      const c = worldToScreen(off, -halfExtent * 3);
      const d = worldToScreen(off, halfExtent * 3);
      ctx.beginPath(); ctx.moveTo(c.x, c.y); ctx.lineTo(d.x, d.y); ctx.stroke();
    });

    // ── Telemetry-derived state ─────────────────────────────────
    const t1 = timeline.v1_insured_telemetry;
    const t2 = timeline.v2_third_party_telemetry;
    const s1 = interpState(tSim, t1);
    const s2 = interpState(tSim, t2);
    // Derived from the vehicle's actual net displacement rather than a
    // hardcoded axis assumption -- the backend used to always place V1 on
    // the Y axis and V2 on the X axis (a fixed 90-degree crossing), so the
    // old code only ever needed a sign check on one axis. Now that
    // multi_vehicle_simulation.py generates real 2D geometry from the
    // claim's actual approach angle/side, the heading has to be read off
    // the real vector. atan2(dy, dx) reproduces the exact same four
    // heading values the old sign-check code produced for its two
    // axis-locked cases (verified by hand), so this is a pure
    // generalization, not a behavior change for claims that still resolve
    // to a 90-degree T-bone.
    const dx1 = t1[t1.length - 1].position[0] - t1[0].position[0];
    const dy1 = t1[t1.length - 1].position[1] - t1[0].position[1];
    const heading1 = (dx1 === 0 && dy1 === 0) ? Math.PI / 2 : Math.atan2(dy1, dx1);
    const dx2 = t2[t2.length - 1].position[0] - t2[0].position[0];
    const dy2 = t2[t2.length - 1].position[1] - t2[0].position[1];
    const heading2 = (dx2 === 0 && dy2 === 0) ? Math.PI : Math.atan2(dy2, dx2);

    // heading1 is V1's direction of TRAVEL, not necessarily which way its
    // body faces -- those are the same thing for a normal forward impact,
    // but not when the member reversed into a fixed object (impact_zone_v1
    // is a rear zone): the car moves toward the barrier tail-first, so its
    // front/nose actually points AWAY from the barrier. Drawing the sprite
    // with heading1 directly put the nose facing the wall even for a
    // reversing claim, which is backwards. v1BodyHeading is what should
    // drive the sprite's rotation AND the impact-zone highlight's side
    // (front_bumper/rear_bumper are relative to the car's own body, not its
    // travel direction) -- heading1 itself stays untouched for anything
    // about the physical path (barrier contact-point nudge, trajectories).
    const v1RearImpactOnFixedObject =
      physics.v2_body_type === "fixed_object" &&
      (physics.impact_zone_v1 === "rear_bumper" || physics.impact_zone_v1 === "rear_driver" || physics.impact_zone_v1 === "rear_passenger");
    const v1BodyHeading = v1RearImpactOnFixedObject ? heading1 + Math.PI : heading1;

    // ── Trajectories (real telemetry positions up to tSim, not a fit curve) ──
    if (layers.trajectories) {
      [{ tl: t1, color: V1_COLOR }, { tl: t2, color: V2_COLOR }].forEach(({ tl, color }) => {
        ctx.strokeStyle = color;
        ctx.globalAlpha = 0.35;
        ctx.lineWidth = 1.5;
        ctx.beginPath();
        let started = false;
        for (const f of tl) {
          if (f.time_sec > tSim) break;
          const p = worldToScreen(f.position[0], f.position[1]);
          if (!started) { ctx.moveTo(p.x, p.y); started = true; } else ctx.lineTo(p.x, p.y);
        }
        ctx.stroke();
        ctx.globalAlpha = 1;
      });
    }

    // ── Skid marks (real braking only) ──────────────────────────
    if (layers.skidMarks) {
      ctx.fillStyle = SKID_COLOR;
      ctx.globalAlpha = 0.35;
      skidTrail(t1).forEach((p) => { const s = worldToScreen(p.x, p.y); ctx.beginPath(); ctx.ellipse(s.x, s.y, toPx(0.15), toPx(0.9), 0, 0, Math.PI * 2); ctx.fill(); });
      skidTrail(t2).forEach((p) => { const s = worldToScreen(p.x, p.y); ctx.beginPath(); ctx.ellipse(s.x, s.y, toPx(0.9), toPx(0.15), 0, 0, Math.PI * 2); ctx.fill(); });
      ctx.globalAlpha = 1;
    }

    // ── Impact zone highlight ────────────────────────────────────
    // Offset from the vehicle's own center/heading using the REAL
    // structural zone (physics.impact_zone_v1 -- see impactZoneOffset's
    // doc comment) when it's available, instead of always drawing the
    // highlight dead-center on the vehicle regardless of which side was
    // actually damaged. Falls back to center only if no zone is known.
    if (layers.impactZone && damageZones?.length) {
      const v1HalfLen = toPx((v1Info?.length_m || 4.4) / 2);
      const v1HalfWid = toPx((v1Info?.width_m || 1.7) / 2);
      const localOff = impactZoneOffset(physics.impact_zone_v1, v1HalfLen, v1HalfWid);
      const worldOffX = localOff.x * Math.cos(v1BodyHeading) - localOff.y * Math.sin(v1BodyHeading);
      const worldOffY = localOff.x * Math.sin(v1BodyHeading) + localOff.y * Math.cos(v1BodyHeading);
      damageZones.forEach((z) => {
        if (!z.bbox_normalized) return;
        const targetIsV2 = z.vehicle?.toLowerCase().includes("2");
        const basePx = targetIsV2 ? worldToScreen(s2.x, s2.y) : worldToScreen(s1.x, s1.y);
        const p = targetIsV2 ? basePx : { x: basePx.x + worldOffX, y: basePx.y + worldOffY };
        ctx.beginPath();
        ctx.arc(p.x, p.y, toPx(0.7), 0, Math.PI * 2);
        ctx.strokeStyle = z.confidence >= 0.6 ? IMPACT_COLOR : "rgba(232,113,10,0.4)";
        ctx.lineWidth = 2.5;
        ctx.setLineDash([4, 3]);
        ctx.stroke();
        ctx.setLineDash([]);
      });
      if (physics.impact_zone_v1_source === "cv_detected" && physics.impact_zone_v1_detected_part) {
        const labelPx = { x: worldToScreen(s1.x, s1.y).x + worldOffX, y: worldToScreen(s1.x, s1.y).y + worldOffY };
        ctx.font = "bold 9px ui-sans-serif, system-ui";
        const text = `Damage: ${physics.impact_zone_v1_detected_part}`;
        const tw = ctx.measureText(text).width;
        ctx.fillStyle = LABEL_BG;
        ctx.beginPath();
        ctx.roundRect(labelPx.x - tw / 2 - 5, labelPx.y + 10, tw + 10, 16, 8);
        ctx.fill();
        ctx.fillStyle = IMPACT_COLOR;
        ctx.textAlign = "center";
        ctx.textBaseline = "middle";
        ctx.fillText(text, labelPx.x, labelPx.y + 18);
      }
    }

    // ── Impact phase (same |t|<0.15 window as the Python exporter) ──
    const flash = Math.max(0, 1 - Math.abs(tSim) / 0.15);
    if (flash > 0) {
      const p = worldToScreen(0, 0);
      ctx.beginPath();
      ctx.arc(p.x, p.y, Math.min(size.w, size.h) * 0.5, 0, Math.PI * 2);
      ctx.fillStyle = IMPACT_COLOR;
      ctx.globalAlpha = flash * 0.22;
      ctx.fill();
      ctx.globalAlpha = 1;
      ctx.fillStyle = IMPACT_COLOR;
      ctx.font = "bold 22px ui-sans-serif, system-ui";
      ctx.textAlign = "center";
      ctx.globalAlpha = flash;
      ctx.fillText("IMPACT", size.w / 2, size.h * 0.14);
      ctx.globalAlpha = 1;

      // Impact force -- a spatial vector arrow is only drawn when a real
      // impact vertex exists (Pathway 1/telemetry only, since the arrow
      // needs a real anchor point in the scene). Every other claim still
      // gets a genuine impulse-momentum ESTIMATE (mass x Δv / an assumed
      // crash-pulse duration -- computed in physics_engine.py, not
      // fabricated here) shown as plain text instead of a spatial arrow,
      // clearly labeled "estimated" rather than presented the same as a
      // measured value.
      if (layers.impactForce) {
        if (physics.impact_force_magnitude_n && physics.v1_impact_vertex_xyz) {
          const [vx, vy] = physics.v1_impact_vertex_xyz;
          const origin = worldToScreen(vx, vy);
          const dirX = s2.x - s1.x, dirY = s2.y - s1.y;
          const mag = Math.hypot(dirX, dirY) || 1;
          const arrowLen = toPx(3);
          const tip = { x: origin.x + (dirX / mag) * arrowLen, y: origin.y + (dirY / mag) * arrowLen };
          ctx.strokeStyle = IMPACT_COLOR;
          ctx.lineWidth = 2.5;
          ctx.beginPath(); ctx.moveTo(origin.x, origin.y); ctx.lineTo(tip.x, tip.y); ctx.stroke();
          ctx.fillStyle = IMPACT_COLOR;
          ctx.font = "bold 10px ui-monospace, monospace";
          ctx.textAlign = "left";
          ctx.fillText(`${(physics.impact_force_magnitude_n / 1000).toFixed(0)} kN`, tip.x + 4, tip.y);
        } else if (physics.impact_force_magnitude_n) {
          ctx.fillStyle = IMPACT_COLOR;
          ctx.font = "bold 12px ui-sans-serif, system-ui";
          ctx.textAlign = "center";
          ctx.fillText(`~${(physics.impact_force_magnitude_n / 1000).toFixed(0)} kN (estimated)`, size.w / 2, size.h * 0.19);
        } else {
          ctx.fillStyle = MUTED_TEXT;
          ctx.font = "10px ui-monospace, monospace";
          ctx.textAlign = "center";
          ctx.fillText("Impact force unavailable", size.w / 2, size.h * 0.19);
        }
      }
    }

    // ── Vehicles ──────────────────────────────────────────────────
    const v1Len = v1Info?.length_m || 4.4, v1Wid = v1Info?.width_m || 1.7;
    const v2Len = v2Info?.length_m || 4.4, v2Wid = v2Info?.width_m || 1.7;
    const v2IsFixedObject = physics.v2_body_type === "fixed_object";
    const BARRIER_THICKNESS_M = 1.2; // must match drawBarrier's own halfLen*2

    let p1 = worldToScreen(s1.x, s1.y);
    // drawVehicle draws the car body CENTERED on p1, extending v1Len/2 in
    // both directions along its heading -- so even when the telemetry
    // CENTER point (s1) is still short of y=0, the body's own near edge can
    // already be past the barrier's face once the center gets within half
    // the car's length of it. This was previously only corrected for
    // tSim >= 0 (post-impact, where service.py pins V1 exactly at the
    // barrier's center) -- but at low reconstructed speeds the car can
    // brake to a stop well under one car-length from the barrier while
    // tSim is still negative (pre-impact), so the sprite visibly embedded
    // in the wall for however long it sat there before t=0. Applying the
    // same backward nudge at every tSim (not just post-impact) fixes that
    // for any claim, not just fast ones -- a uniform offset along the
    // whole pre-impact approach is imperceptible when the car is still far
    // out, and is exactly what's needed once it's close. Nudges the SPRITE
    // only (not s1 itself, which everything else -- skid marks, HUD speed,
    // trajectory -- still reads unmodified).
    if (v2IsFixedObject) {
      const backAlong = v1Len / 2 + BARRIER_THICKNESS_M / 2;
      const nudged = worldToScreen(s1.x - Math.cos(heading1) * backAlong, s1.y - Math.sin(heading1) * backAlong);
      p1 = nudged;
    }
    const p2 = worldToScreen(s2.x, s2.y);

    // A barrier blocks whichever road V1 is actually traveling on, so its
    // long edge must run PERPENDICULAR to V1's own heading. drawBarrier's
    // UNROTATED shape already has its long dimension (halfWid) running
    // along local Y, which coincides with world Y (the same axis V1 travels
    // on) at heading=0 -- i.e. parallel to the road, the wrong way, by
    // default. Since V1's own heading is already ±90° (it travels along Y),
    // using heading1 directly rotates that long edge onto world X, crossing
    // the road instead of running along it. (A previous attempt added a
    // further +90° on top of heading1, which landed back at 0°/180° --
    // vertical again -- undoing the correction entirely; using V2's own
    // heading before that was wrong for a different reason, since it's
    // meaningless for a pinned-stationary object.)
    const barrierHeading = heading1;

    if (layers.labels) {
      drawVehicle(ctx, p1.x, p1.y, v1BodyHeading, v1Len, v1Wid, V1_COLOR, `V1 ${v1Info?.model || v1Info?.make || ""}`.trim(), `${Math.max(0, s1.velocityKmh).toFixed(0)} km/h`, toPx, v1Info?.body_type);
      if (v2IsFixedObject) {
        drawBarrier(ctx, p2.x, p2.y, barrierHeading, toPx, v2Info?.model || "Fixed Object");
      } else {
        drawVehicle(ctx, p2.x, p2.y, heading2, v2Len, v2Wid, V2_COLOR, `V2 ${v2Info?.model || v2Info?.make || ""}`.trim(), `${Math.max(0, s2.velocityKmh).toFixed(0)} km/h`, toPx, v2Info?.body_type);
      }
    } else {
      drawVehicle(ctx, p1.x, p1.y, v1BodyHeading, v1Len, v1Wid, V1_COLOR, "", "", toPx, v1Info?.body_type);
      if (v2IsFixedObject) {
        drawBarrier(ctx, p2.x, p2.y, barrierHeading, toPx, "");
      } else {
        drawVehicle(ctx, p2.x, p2.y, heading2, v2Len, v2Wid, V2_COLOR, "", "", toPx, v2Info?.body_type);
      }
    }

    // ── Velocity vectors ─────────────────────────────────────────
    if (layers.velocityVectors) {
      [{ p: p1, heading: heading1, spd: s1.velocityKmh, color: V1_COLOR }, { p: p2, heading: heading2, spd: s2.velocityKmh, color: V2_COLOR }].forEach(({ p, heading, spd, color }) => {
        if (spd <= 0.5) return;
        const len = toPx(Math.min(3, spd / 25));
        const tip = { x: p.x + Math.cos(heading) * len, y: p.y + Math.sin(heading) * len };
        ctx.strokeStyle = color;
        ctx.globalAlpha = 0.8;
        ctx.lineWidth = 2;
        ctx.beginPath(); ctx.moveTo(p.x, p.y); ctx.lineTo(tip.x, tip.y); ctx.stroke();
        ctx.globalAlpha = 1;
      });
    }

    // ── Measurement tool ─────────────────────────────────────────
    if (measurePoints.length) {
      ctx.fillStyle = "#22d3ee";
      measurePoints.forEach((pt) => {
        const s = worldToScreen(pt.x, pt.y);
        ctx.beginPath(); ctx.arc(s.x, s.y, 4, 0, Math.PI * 2); ctx.fill();
      });
      if (measurePoints.length === 2) {
        const a = worldToScreen(measurePoints[0].x, measurePoints[0].y);
        const b = worldToScreen(measurePoints[1].x, measurePoints[1].y);
        ctx.strokeStyle = "#22d3ee";
        ctx.setLineDash([4, 3]);
        ctx.beginPath(); ctx.moveTo(a.x, a.y); ctx.lineTo(b.x, b.y); ctx.stroke();
        ctx.setLineDash([]);
        const dist = Math.hypot(measurePoints[1].x - measurePoints[0].x, measurePoints[1].y - measurePoints[0].y);
        ctx.fillStyle = "#22d3ee";
        ctx.font = "bold 11px ui-monospace, monospace";
        ctx.fillText(`${dist.toFixed(1)} m`, (a.x + b.x) / 2 + 6, (a.y + b.y) / 2 - 6);
      }
    }
  }, [timeline, tSim, v1Info, v2Info, physics, layers, camera, size, halfExtent, damageZones, worldToScreen, toPx, skidTrail, measurePoints]);

  const handleClick = (e: React.MouseEvent<HTMLCanvasElement>) => {
    if (!measuring) return;
    const rect = canvasRef.current!.getBoundingClientRect();
    const world = screenToWorld(e.clientX - rect.left, e.clientY - rect.top);
    setMeasurePoints((prev) => {
      const next = prev.length >= 2 ? [world] : [...prev, world];
      if (next.length === 2) onMeasurement?.(Math.hypot(next[1].x - next[0].x, next[1].y - next[0].y));
      else onMeasurement?.(null);
      return next;
    });
  };

  const handleWheel = (e: React.WheelEvent<HTMLCanvasElement>) => {
    e.preventDefault();
    const next = Math.max(0.3, Math.min(6, camera.scale * (e.deltaY > 0 ? 0.9 : 1.1)));
    onCameraChange({ ...camera, scale: next });
  };
  const handleMouseDown = (e: React.MouseEvent<HTMLCanvasElement>) => {
    if (measuring) return;
    dragRef.current = { startX: e.clientX, startY: e.clientY, camCx: camera.cx, camCy: camera.cy };
  };
  const handleMouseMove = (e: React.MouseEvent<HTMLCanvasElement>) => {
    if (!dragRef.current) return;
    const dx = (e.clientX - dragRef.current.startX) / toPx(1);
    const dy = (e.clientY - dragRef.current.startY) / toPx(1);
    onCameraChange({ ...camera, cx: dragRef.current.camCx - dx, cy: dragRef.current.camCy - dy });
  };
  const handleMouseUp = () => { dragRef.current = null; };

  return (
    <div ref={wrapRef} className="relative w-full h-full rounded-2xl overflow-hidden border border-border shadow-sm">
      <canvas
        ref={canvasRef}
        onClick={handleClick}
        onWheel={handleWheel}
        onMouseDown={handleMouseDown}
        onMouseMove={handleMouseMove}
        onMouseUp={handleMouseUp}
        onMouseLeave={handleMouseUp}
        className={measuring ? "cursor-crosshair" : "cursor-grab active:cursor-grabbing"}
      />
      {/* Always reachable regardless of how far the view has been panned/
          zoomed -- panning has no bounds clamp, so it was previously
          possible to drag the scene into empty space with no obvious way
          back short of finding the (easy to miss) camera preset row. */}
      <button
        onClick={() => onCameraChange({ cx: 0, cy: 0, scale: 2.2 })}
        title="Reset view"
        className="absolute bottom-3 right-3 flex items-center gap-1.5 px-3 py-1.5 rounded-full bg-white/95 hover:bg-white shadow-md backdrop-blur-sm border border-black/10 text-foreground text-[10px] font-bold uppercase tracking-wide transition-colors"
      >
        <span className="material-symbols-outlined text-[14px]">center_focus_strong</span>
        Reset View
      </button>
    </div>
  );
}
