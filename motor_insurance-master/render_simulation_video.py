
import os
import math
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.animation import FuncAnimation, FFMpegWriter
from typing import Optional, Dict, Any, List


ROAD_COLOR   = "#1e1e1e"
LANE_COLOR   = "#2a2a2a"
GRASS_COLOR  = "#1a2e15"
V1_COLOR     = "#185FA5"
V2_COLOR     = "#A32D2D"
TEXT_COLOR   = "#ffffff"
IMPACT_COLOR = "#f0c030"
SKID_COLOR   = "#0a0a0a"


def _draw_vehicle(ax, cx, cy, angle_rad, width, height, color, label, alpha=1.0):
    rect = mpatches.FancyBboxPatch(
        (-width/2, -height/2), width, height,
        boxstyle="round,pad=0.05",
        facecolor=color, edgecolor='white', linewidth=0.6, alpha=alpha
    )
    t = matplotlib.transforms.Affine2D().rotate(angle_rad).translate(cx, cy)
    rect.set_transform(t + ax.transData)
    ax.add_patch(rect)
    ax.text(cx, cy, label, ha='center', va='center',
             fontsize=5.5, color='white', fontweight='bold',
             rotation=math.degrees(angle_rad), alpha=alpha, zorder=10)
    return rect


def _draw_junction(ax, half_extent):
    """
    The physics timeline this renders (ConfigurableMultiVehicleEngine) always
    has vehicle 1 approaching along the Y axis and vehicle 2 along the X axis,
    meeting at the origin — i.e. it models a right-angle junction collision,
    not parallel highway lanes. This draws that geometry literally, rather
    than the old stylized single-highway view that didn't match the data.
    """
    e = half_extent
    road_w = max(6.0, e * 0.12)

    ax.axhspan(-e, e, color=GRASS_COLOR, zorder=0)
    ax.axvspan(-e, e, color=GRASS_COLOR, zorder=0)

    # Horizontal road (vehicle 2's approach)
    ax.axhspan(-road_w/2, road_w/2, color=LANE_COLOR, zorder=1)
    ax.axhline(0, color='#d4a820', linewidth=1.5, linestyle='-', zorder=2)
    ax.axhline(road_w/2, color='white', linewidth=1.2, zorder=2)
    ax.axhline(-road_w/2, color='white', linewidth=1.2, zorder=2)

    # Vertical road (vehicle 1's approach)
    ax.axvspan(-road_w/2, road_w/2, color=LANE_COLOR, zorder=1)
    ax.axvline(0, color='#d4a820', linewidth=1.5, linestyle='-', zorder=2)
    ax.axvline(road_w/2, color='white', linewidth=1.2, zorder=2)
    ax.axvline(-road_w/2, color='white', linewidth=1.2, zorder=2)

    # Junction square (visually clean intersection)
    junction = mpatches.Rectangle(
        (-road_w/2, -road_w/2), road_w, road_w,
        facecolor=LANE_COLOR, edgecolor='none', zorder=1.5
    )
    ax.add_patch(junction)


def _build_telemetry_arrays(telemetry: List[Dict[str, Any]]):
    """Convert a vehicle's timeline entries into numpy arrays for interpolation."""
    t   = np.array([f["time_sec"] for f in telemetry], dtype=float)
    x   = np.array([f["position"][0] for f in telemetry], dtype=float)
    y   = np.array([f["position"][1] for f in telemetry], dtype=float)
    v   = np.array([f["velocity_kmh"] for f in telemetry], dtype=float)
    return t, x, y, v


def _interp_state(t_query, t_arr, x_arr, y_arr, v_arr):
    x = float(np.interp(t_query, t_arr, x_arr))
    y = float(np.interp(t_query, t_arr, y_arr))
    v = float(np.interp(t_query, t_arr, v_arr))
    return x, y, v


def render_collision_video(
    timeline_output: Dict[str, Any],
    claim_id: str = "CLM-UNKNOWN",
    output_dir: str = "./output_videos",
    physics_data: Optional[Dict] = None,
    fps: int = 30,
    dpi: int = 180,
) -> str:
    """
    Renders the actual physics timeline (ConfigurableMultiVehicleEngine's
    v1_insured_telemetry / v2_third_party_telemetry arrays) — vehicle
    position/speed at every video frame is interpolated directly from that
    real data, not a separately hand-tuned animation curve.
    """
    os.makedirs(output_dir, exist_ok=True)
    physics_data = physics_data or {}

    v1_telemetry = timeline_output.get("v1_insured_telemetry", [])
    v2_telemetry = timeline_output.get("v2_third_party_telemetry", [])

    if not v1_telemetry or not v2_telemetry:
        raise ValueError(
            "render_collision_video requires v1_insured_telemetry and "
            "v2_third_party_telemetry from ConfigurableMultiVehicleEngine.generate_simulation_timeline()"
        )

    t1, x1, y1, v1v = _build_telemetry_arrays(v1_telemetry)
    t2, x2, y2, v2v = _build_telemetry_arrays(v2_telemetry)

    t_start = float(min(t1[0], t2[0]))
    t_end   = float(max(t1[-1], t2[-1]))
    real_duration_s = max(t_end - t_start, 0.5)
    total_frames = max(int(fps * real_duration_s), fps)  # never less than 1s of video

    # Camera auto-fits to the actual physics-derived travel distances instead
    # of a fixed guess — a boda boda collision and a highway truck collision
    # need very different scales.
    half_extent = max(
        float(np.max(np.abs(np.concatenate([x1, x2, y1, y2])))) * 1.15,
        15.0,
    )

    # Real vehicle footprints when available (from vehicle_registry.py via
    # service.py), falling back to reasonable saloon-car defaults.
    v1_len_m = float(physics_data.get("v1_length_m") or 4.4)
    v1_wid_m = float(physics_data.get("v1_width_m") or 1.7)
    v2_len_m = float(physics_data.get("v2_length_m") or 4.4)
    v2_wid_m = float(physics_data.get("v2_width_m") or 1.7)

    v1_label = (physics_data.get("v1_model") or physics_data.get("v1_make") or "Vehicle 1")[:14]
    v2_label = (physics_data.get("v2_model") or physics_data.get("v2_make") or "Vehicle 2")[:14]

    verdict = physics_data.get("physics_verdict", "")
    verdict_color = "#1D9E75" if verdict == "CONSISTENT" else "#E24B4A"

    fig, ax = plt.subplots(figsize=(12.8, 12.8))
    fig.patch.set_facecolor('#111111')
    ax.set_facecolor('#111111')
    ax.set_xlim(-half_extent, half_extent)
    ax.set_ylim(-half_extent, half_extent)
    ax.set_aspect('equal')
    ax.axis('off')

    drawn_patches = []
    drawn_texts = []
    rng = np.random.default_rng(42)
    skid_marks_v1 = []  # accumulate real skid positions as braking progresses
    skid_marks_v2 = []

    def update(frame_idx):
        nonlocal drawn_patches, drawn_texts
        for p in drawn_patches:
            p.remove()
        for tx in drawn_texts:
            tx.remove()
        drawn_patches.clear()
        drawn_texts.clear()

        prog = frame_idx / max(total_frames - 1, 1)
        t_sim = t_start + prog * (t_end - t_start)

        v1x, v1y, v1_spd = _interp_state(t_sim, t1, x1, y1, v1v)
        v2x, v2y, v2_spd = _interp_state(t_sim, t2, x2, y2, v2v)

        _draw_junction(ax, half_extent)

        # V1 travels along Y, V2 travels along X (see _draw_junction docstring).
        # Heading derived from actual direction of travel in the real data,
        # not a hardcoded rotation.
        v1_heading = math.pi / 2 if (y1[-1] - y1[0]) >= 0 else -math.pi / 2
        v2_heading = math.pi if (x2[-1] - x2[0]) <= 0 else 0.0

        # Real skid marks: only once each vehicle's speed has measurably
        # dropped from its pre-impact peak (i.e. braking/post-impact
        # deceleration is actually happening in the real data).
        v1_peak = float(np.max(v1v))
        v2_peak = float(np.max(v2v))
        if v1_peak > 1 and v1_spd < v1_peak * 0.92 and t_sim < 0.3:
            skid_marks_v1.append((v1x + rng.uniform(-0.4, 0.4), v1y))
        if v2_peak > 1 and v2_spd < v2_peak * 0.92 and t_sim < 0.3:
            skid_marks_v2.append((v2x, v2y + rng.uniform(-0.4, 0.4)))

        for sx, sy in skid_marks_v1[-40:]:
            skid = mpatches.Ellipse((sx, sy), 0.5, 2.2, angle=0,
                                     color=SKID_COLOR, alpha=0.3, zorder=2)
            ax.add_patch(skid)
            drawn_patches.append(skid)
        for sx, sy in skid_marks_v2[-40:]:
            skid = mpatches.Ellipse((sx, sy), 2.2, 0.5, angle=0,
                                     color=SKID_COLOR, alpha=0.3, zorder=2)
            ax.add_patch(skid)
            drawn_patches.append(skid)

        # Impact flash centered on the real moment of closest approach (t=0
        # in the timeline's convention), not an artificial phase fraction.
        flash = max(0.0, 1.0 - abs(t_sim) / 0.15)
        if flash > 0:
            fl = mpatches.Circle((0, 0), half_extent * 1.5, facecolor=IMPACT_COLOR,
                                  alpha=flash * 0.35, zorder=5)
            ax.add_patch(fl)
            drawn_patches.append(fl)
            txt = ax.text(0, half_extent * 0.5, 'IMPACT', ha='center', va='center',
                           fontsize=28, color=IMPACT_COLOR, fontweight='bold',
                           alpha=flash, zorder=6)
            drawn_texts.append(txt)

        crush = flash  # visual crumple synced to the same real impact window
        v1w = v1_wid_m * 2.2 * (1 - crush * 0.15)
        v1h = v1_len_m * 0.9
        v2w = v2_len_m * 0.9
        v2h = v2_wid_m * 2.2 * (1 - crush * 0.15)

        _draw_vehicle(ax, v1x, v1y, v1_heading, v1w, v1h, V1_COLOR, v1_label)
        _draw_vehicle(ax, v2x, v2y, v2_heading, v2w, v2h, V2_COLOR, v2_label)

        if flash == 0:
            for vx, vy, spd_ in [(v1x, v1y + half_extent * 0.06, v1_spd),
                                  (v2x + half_extent * 0.06, v2y, v2_spd)]:
                bg = mpatches.FancyBboxPatch(
                    (vx - half_extent * 0.045, vy - half_extent * 0.018),
                    half_extent * 0.09, half_extent * 0.045,
                    boxstyle="round,pad=0.1", facecolor='black', alpha=0.55, zorder=7)
                ax.add_patch(bg)
                drawn_patches.append(bg)
                st = ax.text(vx, vy, f'{max(0, spd_):.0f} km/h',
                             ha='center', va='center', fontsize=6.5, color='white', zorder=8)
                drawn_texts.append(st)

        phase = "IMPACT" if flash > 0.5 else ("Pre-impact" if t_sim < 0 else "Post-impact")
        hud_lines = [
            f"Claim: {claim_id}",
            f"Phase: {phase}  |  t={t_sim:+.2f}s",
        ]
        for i, ln in enumerate(hud_lines):
            t_ = ax.text(-half_extent * 0.97, half_extent * 0.93 - i * half_extent * 0.05,
                         ln, fontsize=8.5, color='white', alpha=0.8,
                         fontfamily='monospace', zorder=9)
            drawn_texts.append(t_)

        v1s = physics_data.get("v1_speed_kmh", "?")
        v2s = physics_data.get("v2_speed_kmh", "?")
        ang = physics_data.get("impact_angle_deg", "?")
        crd = physics_data.get("crush_depth_mm", "?")
        summary = [
            f"V1 stated {v1s} km/h  |  V2 stated {v2s} km/h",
            f"Angle {ang}°  |  Crush {crd} mm",
            f"Physics: {verdict}",
        ]
        for i, ln in enumerate(summary):
            col = verdict_color if i == 2 else 'white'
            t_ = ax.text(half_extent * 0.97, half_extent * 0.93 - i * half_extent * 0.05,
                         ln, fontsize=6.5, color=col, ha='right', alpha=0.85,
                         fontfamily='monospace', zorder=9)
            drawn_texts.append(t_)

        wm = ax.text(0, -half_extent * 0.97,
                     f'Insurance Physics Reconstruction · {claim_id} · For investigative use only',
                     ha='center', fontsize=5, color='white', alpha=0.3, zorder=9)
        drawn_texts.append(wm)

        pb_w = half_extent * 1.94
        pb_bg = mpatches.FancyBboxPatch((-half_extent * 0.97, -half_extent * 0.91), pb_w,
                                         half_extent * 0.02, boxstyle="square,pad=0",
                                         facecolor='#333333', alpha=0.6, zorder=8)
        pb_fill = mpatches.FancyBboxPatch((-half_extent * 0.97, -half_extent * 0.91), pb_w * prog,
                                           half_extent * 0.02, boxstyle="square,pad=0",
                                           facecolor='#185FA5', alpha=0.85, zorder=9)
        ax.add_patch(pb_bg)
        ax.add_patch(pb_fill)
        drawn_patches.extend([pb_bg, pb_fill])

        return drawn_patches + drawn_texts

    anim = FuncAnimation(fig, update, frames=total_frames, interval=1000 / fps, blit=False)

    out_path = os.path.join(output_dir, f"collision_{claim_id.replace('-', '_')}.mp4")

    writer = FFMpegWriter(fps=fps, metadata={
        "title": f"Collision Simulation — {claim_id}",
        "artist": "Insurance Physics Engine",
        "comment": f"Verdict: {physics_data.get('physics_verdict', 'UNKNOWN')}",
    }, bitrate=6000)

    print(f"Rendering {total_frames} frames ({real_duration_s:.1f}s of real timeline) to {out_path}...")
    anim.save(out_path, writer=writer, dpi=dpi)
    plt.close(fig)
    print(f"Done: {out_path}")
    return os.path.abspath(out_path)


if __name__ == "__main__":
    # Minimal real-shaped timeline for a smoke test (matches
    # ConfigurableMultiVehicleEngine's actual output keys/shape).
    dummy_timeline = {
        "simulation_metadata": {"total_frames_generated": 0},
        "v1_insured_telemetry": [
            {"time_sec": t, "position": [0, -30 + (30 * (t + 5) / 5), 0],
             "velocity_kmh": 60.0, "is_colliding": t == 0.0}
            for t in np.arange(-5.0, 0.55, 0.05)
        ],
        "v2_third_party_telemetry": [
            {"time_sec": t, "position": [30 - (30 * (t + 5) / 5), 0, 0],
             "velocity_kmh": 50.0, "is_colliding": t == 0.0}
            for t in np.arange(-5.0, 0.55, 0.05)
        ],
    }
    render_collision_video(
        timeline_output=dummy_timeline,
        claim_id="CLM-2026-TEST-002",
        output_dir="./output_videos",
        physics_data={
            "v1_speed_kmh": 60,
            "v2_speed_kmh": 50,
            "impact_angle_deg": 90,
            "crush_depth_mm": 300,
            "physics_verdict": "CONSISTENT",
            "v1_model": "Toyota Premio",
            "v2_model": "Toyota Hiace Matatu",
        }
    )
