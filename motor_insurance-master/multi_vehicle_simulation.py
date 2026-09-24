import numpy as np
import json


class ConfigurableMultiVehicleEngine:

    def __init__(self, vehicle_registry_json):
        self.registry = json.loads(vehicle_registry_json)["vehicle_dimension_registry"]

        self.stiffness_db = {
            "motorcycle":           [130000, 2600000],
            "saloon_station_wagon": [115000, 2400000],
            "saloon":               [115000, 2400000],
            "suv":                  [140000, 2800000],
            "pickup":               [150000, 3000000],
            "light_commercial_psv": [140000, 2900000],
            "matatu":               [140000, 2900000],
            "heavy_commercial":     [210000, 4500000],
            "tuk_tuk":              [80000,  1600000],
        }

        self.g = 9.81

    def _get_vehicle_specs(self, model_key):
        if model_key in self.registry:
            return self.registry[model_key]
        for key in self.registry:
            if model_key in key or key in model_key:
                return self.registry[key]
        raise ValueError(f"Vehicle model '{model_key}' missing from registry.")

    def _calculate_crush_energy(self, v_class, depth_m, width_m):
        stiffness = self.stiffness_db.get(v_class, [115000, 2400000])
        A, B = stiffness[0], stiffness[1]
        return (width_m / 2.0) * ((A * depth_m) + (B * (depth_m ** 2)))

    def generate_simulation_timeline(
        self,
        payload,
        pre_crash_duration_seconds=5.0,
        post_crash_duration_seconds=0.5,
    ):
        mu = payload["environment_snapshot"]["resolved_friction_mu"]

        sim_config = payload.get("simulation_configuration", {})
        pre_crash_duration_seconds  = sim_config.get("requested_pre_crash_seconds",  pre_crash_duration_seconds)
        post_crash_duration_seconds = sim_config.get("requested_post_crash_seconds", post_crash_duration_seconds)

        v1_data = payload["vehicles"][0]
        v2_data = payload["vehicles"][1]

        spec1 = self._get_vehicle_specs(v1_data["model_identity"])
        spec2 = self._get_vehicle_specs(v2_data["model_identity"])

        m1 = spec1["kenyan_operational_mass_kg"]
        m2 = spec2["kenyan_operational_mass_kg"]

        e1 = self._calculate_crush_energy(
            spec1.get("class", "saloon_station_wagon"),
            v1_data["damage"]["depth_meters"],
            v1_data["damage"]["width_meters"],
        )
        e2 = self._calculate_crush_energy(
            spec2.get("class", "saloon_station_wagon"),
            v2_data["damage"]["depth_meters"],
            v2_data["damage"]["width_meters"],
        )

        delta_v1 = np.sqrt((2 * e1) / m1)
        delta_v2 = np.sqrt((2 * e2) / m2)

        # V1's pre-impact speed used to come from a momentum/restitution
        # formula derived purely from crush energy and V2's speed --
        # completely disconnected from (and could diverge wildly from) the
        # speed CrashReconstructionEngine already computed via its own
        # McHenry crush-energy analysis, which is what the Evidence Panel's
        # "V1 physics-derived" figure actually shows. That produced the
        # animation/HUD displaying one speed (e.g. 87 km/h) while the
        # evidence panel showed a different one (e.g. 33.5 km/h) for the
        # same claim. Now both V1 and V2 simply use whatever speed the
        # caller decided is authoritative (service.py passes the same
        # reconstructed value used everywhere else), so the animation can't
        # contradict the numbers shown beside it.
        v2_pre_impact_mps = v2_data.get("stated_speed_kmh", 50.0) / 3.6
        v1_pre_impact_mps = v1_data.get("stated_speed_kmh", 50.0) / 3.6

        a_braking          = self.g * mu
        human_prt_seconds  = 1.5
        braking_duration_s = 1.0

        # Collision geometry -- previously hardcoded (V1 always along world
        # Y, V2 always along world X, a fixed 90-degree T-bone crossing
        # regardless of the actual claim). approach_angle_deg is the angle
        # between the two vehicles' directions of travel (0=rear-end/same
        # direction, 90=T-bone/perpendicular, 180=head-on/opposite), the
        # same convention pipeline_bridge.py's infer_approach_angle already
        # uses. impact_side ("left"/"right") picks which way V2 approaches
        # from, since the angle alone is symmetric. V1's own heading is kept
        # fixed along world +Y as the scene's reference axis -- only V2's
        # direction is derived relative to it, so a T-bone-from-the-right
        # claim with no better data reproduces the old default exactly.
        approach_angle_deg = payload.get("approach_angle_deg", 90.0)
        impact_side = payload.get("impact_side", "right")
        v1_dir = np.array([0.0, 1.0])
        phi = np.radians(approach_angle_deg) * (-1.0 if impact_side == "left" else 1.0)
        cos_phi, sin_phi = np.cos(phi), np.sin(phi)
        v2_dir = np.array([
            v1_dir[0] * cos_phi - v1_dir[1] * sin_phi,
            v1_dir[0] * sin_phi + v1_dir[1] * cos_phi,
        ])

        time_steps = []
        t = -float(pre_crash_duration_seconds)
        while t < -braking_duration_s:
            time_steps.append(round(t, 2))
            t += 0.50
        t = -braking_duration_s
        while t <= float(post_crash_duration_seconds):
            time_steps.append(round(t, 2))
            t = round(t + 0.05, 2)

        v1_timeline = []
        v2_timeline = []

        for t in time_steps:
            if t < 0:
                abs_t = abs(t)
                is_colliding = False

                # time_to_stop: at low reconstructed speeds (a slow reverse
                # into a barrier, say), the vehicle can fully stop under
                # hard braking well before the fixed 1-second braking_duration_s
                # window ends. The position formula below is a downward
                # parabola in abs_t -- past the real stopping point it keeps
                # evaluating and starts DECREASING the distance-from-impact
                # again, eventually going negative, which put the car
                # visually on the wrong side of (or overlapping) the impact
                # point during frames that are still supposed to be
                # pre-impact. Holding position at the true stopping distance
                # once speed reaches zero fixes that for any claim, not just
                # this one -- previously this was masked by every prior test
                # claim happening to have high enough speed that the natural
                # stop always landed past 1 second anyway.
                time_to_stop = (v1_pre_impact_mps / a_braking) if a_braking > 0 else float("inf")
                braking_end_t = min(braking_duration_s, time_to_stop)
                if abs_t <= braking_duration_s:
                    if abs_t >= time_to_stop:
                        v1_speed = 0.0
                        m1 = (v1_pre_impact_mps ** 2) / (2 * a_braking) if a_braking > 0 else 0.0
                    else:
                        v1_speed = max(0.0, v1_pre_impact_mps - (a_braking * abs_t))
                        m1 = (v1_pre_impact_mps * abs_t) - (0.5 * a_braking * (abs_t ** 2))
                elif abs_t <= (braking_duration_s + human_prt_seconds):
                    v1_speed = v1_pre_impact_mps
                    brake_start_dist = (
                        (v1_pre_impact_mps * braking_end_t) -
                        (0.5 * a_braking * (braking_end_t ** 2))
                    )
                    m1 = brake_start_dist + (v1_pre_impact_mps * (abs_t - braking_duration_s))
                else:
                    v1_speed = v1_pre_impact_mps
                    m1 = (
                        (v1_pre_impact_mps * (abs_t - braking_duration_s + braking_end_t)) -
                        (0.5 * a_braking * (braking_end_t ** 2))
                    )

                v2_speed = v2_pre_impact_mps
                m2 = v2_pre_impact_mps * abs_t

                v1_pos = -v1_dir * m1
                v2_pos = -v2_dir * m2

            else:
                is_colliding = (t == 0.00)
                v1_speed = max(0.0, v1_pre_impact_mps - delta_v1 - (a_braking * t))
                v2_speed = max(0.0, v2_pre_impact_mps + delta_v2 - (a_braking * t))
                v1_pos = v1_dir * (v1_speed * t)
                v2_pos = v2_dir * (v2_speed * t)

            v1_timeline.append({
                "time_sec":     t,
                "position":     [round(float(v1_pos[0]), 3), round(float(v1_pos[1]), 3), 0.0],
                "velocity_kmh": round(float(v1_speed * 3.6), 1),
                "is_colliding": is_colliding,
            })
            v2_timeline.append({
                "time_sec":     t,
                "position":     [round(float(v2_pos[0]), 3), round(float(v2_pos[1]), 3), 0.0],
                "velocity_kmh": round(float(v2_speed * 3.6), 1),
                "is_colliding": is_colliding,
            })

        return {
            "claim_id": payload.get("claim_id", ""),
            "simulation_metadata": {
                "configured_pre_crash_seconds":  pre_crash_duration_seconds,
                "configured_post_crash_seconds": post_crash_duration_seconds,
                "total_frames_generated":        len(time_steps),
                "v1_pre_impact_speed_kmh":       round(v1_pre_impact_mps * 3.6, 1),
                "v2_pre_impact_speed_kmh":       round(v2_pre_impact_mps * 3.6, 1),
                "delta_v1_kmh":                  round(delta_v1 * 3.6, 1),
                "delta_v2_kmh":                  round(delta_v2 * 3.6, 1),
                "friction_mu":                   mu,
            },
            "v1_insured_telemetry":    v1_timeline,
            "v2_third_party_telemetry": v2_timeline,
        }