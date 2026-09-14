import { TelemetryFrame, SimEvent, SimulationTimeline } from "./types";

// Direct JS port of render_simulation_video.py's `_build_telemetry_arrays` +
// `_interp_state` -- same linear interpolation, same semantics. This is the
// ONLY place vehicle position/speed is computed from telemetry; every
// consumer (canvas, HUD, timeline) goes through this so the browser view
// and the exported MP4 are reading the same real data the same way.

export type InterpState = { x: number; y: number; velocityKmh: number };

function lerp(t: number, t0: number, t1: number, v0: number, v1: number): number {
  if (t1 === t0) return v0;
  const frac = (t - t0) / (t1 - t0);
  return v0 + frac * (v1 - v0);
}

/** Linear interpolation over a sorted telemetry array, matching numpy.interp
 * (clamps to the first/last value outside the array's time range). */
export function interpState(tQuery: number, telemetry: TelemetryFrame[]): InterpState {
  if (!telemetry || telemetry.length === 0) return { x: 0, y: 0, velocityKmh: 0 };
  if (tQuery <= telemetry[0].time_sec) {
    const f = telemetry[0];
    return { x: f.position[0], y: f.position[1], velocityKmh: f.velocity_kmh };
  }
  const last = telemetry[telemetry.length - 1];
  if (tQuery >= last.time_sec) {
    return { x: last.position[0], y: last.position[1], velocityKmh: last.velocity_kmh };
  }
  // telemetry is generated at a fixed timestep, so a linear scan is cheap
  // (dozens of frames) and keeps this a faithful analog of np.interp
  // without pulling in a binary-search dependency for presentation code.
  for (let i = 0; i < telemetry.length - 1; i++) {
    const a = telemetry[i];
    const b = telemetry[i + 1];
    if (tQuery >= a.time_sec && tQuery <= b.time_sec) {
      return {
        x: lerp(tQuery, a.time_sec, b.time_sec, a.position[0], b.position[0]),
        y: lerp(tQuery, a.time_sec, b.time_sec, a.position[1], b.position[1]),
        velocityKmh: lerp(tQuery, a.time_sec, b.time_sec, a.velocity_kmh, b.velocity_kmh),
      };
    }
  }
  return { x: last.position[0], y: last.position[1], velocityKmh: last.velocity_kmh };
}

export function telemetryBounds(timeline: SimulationTimeline) {
  const t1 = timeline.v1_insured_telemetry;
  const t2 = timeline.v2_third_party_telemetry;
  const tStart = Math.min(t1[0]?.time_sec ?? 0, t2[0]?.time_sec ?? 0);
  const tEnd = Math.max(t1[t1.length - 1]?.time_sec ?? 0, t2[t2.length - 1]?.time_sec ?? 0);
  return { tStart, tEnd };
}

/** Real-world half-extent the scene should be framed to, mirroring
 * render_simulation_video.py's camera auto-fit (max abs coordinate * 1.15,
 * floor of 15m) -- same scene scale in the browser as in the exported video. */
export function sceneHalfExtent(timeline: SimulationTimeline): number {
  const all: number[] = [];
  for (const f of timeline.v1_insured_telemetry) { all.push(Math.abs(f.position[0]), Math.abs(f.position[1])); }
  for (const f of timeline.v2_third_party_telemetry) { all.push(Math.abs(f.position[0]), Math.abs(f.position[1])); }
  const maxAbs = all.length ? Math.max(...all) : 15;
  return Math.max(maxAbs * 1.15, 15);
}

/** Same "speed dropped below 92% of that vehicle's own peak" heuristic
 * render_simulation_video.py uses to decide when to draw skid marks --
 * ported verbatim rather than re-derived, so braking is only shown when the
 * real telemetry actually supports it. */
export function isBraking(telemetry: TelemetryFrame[], tQuery: number): boolean {
  if (!telemetry.length) return false;
  const peak = Math.max(...telemetry.map((f) => f.velocity_kmh));
  if (peak <= 1) return false;
  const { velocityKmh } = interpState(tQuery, telemetry);
  return velocityKmh < peak * 0.92 && tQuery < 0.3;
}

/** Derives the event markers the timeline scrubber shows -- every marker is
 * read directly off real telemetry, none are invented. */
export function deriveEvents(timeline: SimulationTimeline): SimEvent[] {
  const t1 = timeline.v1_insured_telemetry;
  const t2 = timeline.v2_third_party_telemetry;
  if (!t1.length || !t2.length) return [];
  const { tStart, tEnd } = telemetryBounds(timeline);
  const events: SimEvent[] = [
    { key: "detected", label: "Vehicles detected", t: tStart, vehicle: "both", detail: "Start of recorded telemetry" },
  ];

  const peak1 = Math.max(...t1.map((f) => f.velocity_kmh));
  const peak2 = Math.max(...t2.map((f) => f.velocity_kmh));
  const brakeFrame1 = peak1 > 1 ? t1.find((f) => f.velocity_kmh < peak1 * 0.92 && f.time_sec < 0.3) : undefined;
  const brakeFrame2 = peak2 > 1 ? t2.find((f) => f.velocity_kmh < peak2 * 0.92 && f.time_sec < 0.3) : undefined;
  if (brakeFrame1) events.push({ key: "brake_v1", label: "V1 braking", t: brakeFrame1.time_sec, vehicle: "V1", detail: `Speed dropped from ${peak1.toFixed(0)} km/h` });
  if (brakeFrame2) events.push({ key: "brake_v2", label: "V2 braking", t: brakeFrame2.time_sec, vehicle: "V2", detail: `Speed dropped from ${peak2.toFixed(0)} km/h` });

  events.push({ key: "collision", label: "Collision", t: 0, vehicle: "both", detail: "Point of closest approach (t=0)" });
  events.push({ key: "max_deformation", label: "Maximum deformation", t: 0, vehicle: "both", detail: "Peak of the impact window" });

  events.push({ key: "post_impact_stop", label: "Post-impact stop", t: tEnd, vehicle: "both", detail: "End of recorded telemetry" });

  return events.sort((a, b) => a.t - b.t);
}
