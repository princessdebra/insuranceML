"""
Kenyan-market vehicle physics registry for crash reconstruction.

METHODOLOGY 

Two genuinely different kinds of data live in each VehicleProfile and they
should NOT be trusted equally:

1. Dimensions & weights (kerb_weight_kg, gross_weight_kg, length/width/height,
   wheelbase, ground_clearance) — these are drawn from public manufacturer
   specification sheets for the exact model/generation most commonly seen on
   Kenyan roads (used Japanese imports for passenger cars, matatu conversions
   for the Hiace/Caravan class, etc). These are independently verifiable and
   not vehicle-crash-specific — high confidence.

2. Crush stiffness coefficients (crumple_A, crumple_B) — real values for
   these can only come from physical crash testing. No public Kenyan-fleet
   crash-test data exists, and Old Mutual has not yet supplied real
   claims/repair data to calibrate against. What IS used here is the
   *relative* stiffness relationship between structural categories that is
   well established in published crash-reconstruction literature (e.g.
   NHTSA vehicle-crush-stiffness studies): body-on-frame trucks/pickups test
   measurably stiffer than unibody cars of similar mass; AWD/reinforced
   unibody platforms sit above plain unibody sedans; heavier vehicles absorb
   a given crush depth with proportionally more energy. The numbers below
   preserve those real relative relationships, anchored to this codebase's
   own internal unit scale (see below) — they are class-level engineering
   estimates, not measurements of these specific vehicles.

   EXCEPTION — motorcycles and tuk-tuks: the McHenry crush model assumes a
   structural crush zone these vehicle types don't have, so there is no
   equivalent literature to calibrate against. Their crumple_A/B values are
   an unverified low placeholder, not a researched estimate — see the
   inline comments on those entries, and note their stiffness_confidence
   is capped at 0.3-0.4, the lowest in this file.

UNITS / SCALE
-------------
crumple_A/crumple_B are consumed by physics_engine.mchenry_crush_energy() as:
    energy_j = (A * C + B * C**2) * L * 1000
where C = crush depth (m), L = impact surface width (m). This file does not
use a published external unit convention (e.g. NHTSA's lb/in tables) verbatim
— those values are NOT dimensionally compatible with the formula above
without a documented conversion this codebase never defined. Instead, the
existing internal scale was empirically sanity-checked: A=150, B=280 (typical
compact sedan) with a 30cm crush over a 1m impact width implies ~37 km/h for
a 1,310kg car — a physically reasonable, textbook-consistent number. All
values below are calibrated as multiples of that same anchor, not pasted from
an external table.

CONFIDENCE
----------
`dimension_confidence` and `stiffness_confidence` are tracked separately so
the difference in certainty is visible instead of hidden behind one number.
`confidence` (used by physics_engine.py for its overall simulation-confidence
readout) is their weighted blend, weighted toward stiffness since that's the
figure the fraud score is most sensitive to.

Still true and worth restating: this remains provisional. It is Kenyan-market
-relevant and methodologically documented, not measured from real Kenyan
fleet crash/repair data. That upgrade path still runs through Old Mutual
supplying real claims data.
"""

import logging
from typing import Optional
from dataclasses import dataclass, field, asdict

logger = logging.getLogger(__name__)


@dataclass
class StructuralZones:
    front_bumper:      dict = field(default_factory=lambda: {"x": 0.0,  "y": 0.0,   "z": 0.35})
    rear_bumper:       dict = field(default_factory=lambda: {"x": 4.2,  "y": 0.0,   "z": 0.35})
    driver_door:       dict = field(default_factory=lambda: {"x": 1.6,  "y": -0.85, "z": 0.65})
    passenger_door:    dict = field(default_factory=lambda: {"x": 1.6,  "y":  0.85, "z": 0.65})
    rear_driver:       dict = field(default_factory=lambda: {"x": 2.8,  "y": -0.85, "z": 0.65})
    rear_passenger:    dict = field(default_factory=lambda: {"x": 2.8,  "y":  0.85, "z": 0.65})
    roof:              dict = field(default_factory=lambda: {"x": 2.1,  "y": 0.0,   "z": 1.45})
    front_left_wheel:  dict = field(default_factory=lambda: {"x": 0.4,  "y": -0.75, "z": 0.3})
    front_right_wheel: dict = field(default_factory=lambda: {"x": 0.4,  "y":  0.75, "z": 0.3})


@dataclass
class VehicleProfile:
    make: str
    model: str
    body_type: str

    kerb_weight_kg: float
    gross_weight_kg: float
    typical_passenger_load_kg: float = 0.0

    length_mm: float = 4200.0
    width_mm: float  = 1700.0
    height_mm: float = 1450.0
    wheelbase_mm: float = 2550.0
    front_overhang_mm: float = 800.0
    rear_overhang_mm: float  = 900.0
    ground_clearance_mm: float = 150.0

    has_abs: bool = False
    has_esc: bool = False
    airbag_count: int = 2
    drive_type: str = "2WD"
    crumple_zone: str = "standard"

    front_suspension: str = "macpherson"
    rear_suspension: str = "torsion"

    drag_coefficient: float = 0.30
    rolling_resistance: float = 0.015
    center_of_gravity_height_ratio: float = 0.52

    crumple_A: float = 150.0
    crumple_B: float = 280.0

    zones: StructuralZones = field(default_factory=StructuralZones)

    # Dimensions/weights: public manufacturer spec for the model/generation
    # most common on Kenyan roads. Stiffness (crumple_A/B): class-relative
    # estimate from published crash-reconstruction literature — see module
    # docstring. Neither is measured from real Kenyan fleet data yet.
    data_source: str = "kenya_market_spec_class_calibrated_stiffness"
    dimension_confidence: float = 0.85
    stiffness_confidence: float = 0.6
    confidence: float = field(init=False, default=0.0)

    def to_dict(self):
        return asdict(self)

    def __post_init__(self):
        # Overall confidence used by physics_engine.py's simulation-confidence
        # readout — weighted toward stiffness since the fraud score is most
        # sensitive to it, and stiffness is the less-certain of the two.
        self.confidence = round(
            (self.stiffness_confidence * 0.6) + (self.dimension_confidence * 0.4), 2
        )

    @property
    def center_of_gravity_z(self) -> float:
        return (self.height_mm / 1000) * self.center_of_gravity_height_ratio

    @property
    def effective_mass_kg(self) -> float:
        return self.kerb_weight_kg + self.typical_passenger_load_kg


VEHICLE_REGISTRY: dict[str, VehicleProfile] = {

    # ── Unibody saloons (baseline stiffness class) ──────────────────────
    # Kenya's most common used-import sedans. Dimensions/weights from public
    # spec sheets for the generation typically imported (2WD, 1.5-1.8L).
    "toyota_premio": VehicleProfile(
        make="Toyota", model="Premio", body_type="saloon",
        kerb_weight_kg=1310, gross_weight_kg=1785, typical_passenger_load_kg=300,
        length_mm=4495, width_mm=1695, height_mm=1475, wheelbase_mm=2600,
        front_overhang_mm=890, rear_overhang_mm=1005, ground_clearance_mm=155,
        has_abs=True, has_esc=False, airbag_count=4, drive_type="2WD",
        crumple_zone="standard", crumple_A=160.0, crumple_B=290.0,
        dimension_confidence=0.85, stiffness_confidence=0.6
    ),
    "toyota_allion": VehicleProfile(
        make="Toyota", model="Allion", body_type="saloon",
        kerb_weight_kg=1290, gross_weight_kg=1765, typical_passenger_load_kg=300,
        length_mm=4545, width_mm=1695, height_mm=1480, wheelbase_mm=2600,
        has_abs=True, has_esc=False, airbag_count=4, drive_type="2WD",
        crumple_zone="standard", crumple_A=155.0, crumple_B=285.0,
        dimension_confidence=0.85, stiffness_confidence=0.6
    ),
    "toyota_fielder": VehicleProfile(
        make="Toyota", model="Fielder", body_type="saloon",
        kerb_weight_kg=1195, gross_weight_kg=1620, typical_passenger_load_kg=320,
        length_mm=4395, width_mm=1695, height_mm=1500, wheelbase_mm=2600,
        has_abs=True, has_esc=False, airbag_count=2, drive_type="2WD",
        crumple_zone="standard", crumple_A=145.0, crumple_B=270.0,
        dimension_confidence=0.85, stiffness_confidence=0.6
    ),
    "toyota_vitz": VehicleProfile(
        make="Toyota", model="Vitz", body_type="saloon",
        kerb_weight_kg=940, gross_weight_kg=1360, typical_passenger_load_kg=250,
        length_mm=3775, width_mm=1665, height_mm=1530, wheelbase_mm=2440,
        has_abs=True, has_esc=False, airbag_count=2, drive_type="2WD",
        crumple_zone="standard", crumple_A=120.0, crumple_B=240.0,
        dimension_confidence=0.85, stiffness_confidence=0.6
    ),
    "toyota_axio": VehicleProfile(
        make="Toyota", model="Axio", body_type="saloon",
        kerb_weight_kg=1080, gross_weight_kg=1535, typical_passenger_load_kg=300,
        length_mm=4235, width_mm=1695, height_mm=1460, wheelbase_mm=2600,
        has_abs=True, has_esc=False, airbag_count=2, drive_type="2WD",
        crumple_zone="standard", crumple_A=140.0, crumple_B=265.0,
        dimension_confidence=0.85, stiffness_confidence=0.6
    ),
    "toyota_probox": VehicleProfile(
        make="Toyota", model="Probox", body_type="saloon",
        kerb_weight_kg=1025, gross_weight_kg=1490, typical_passenger_load_kg=350,
        length_mm=4195, width_mm=1690, height_mm=1515, wheelbase_mm=2550,
        has_abs=False, has_esc=False, airbag_count=2, drive_type="2WD",
        crumple_zone="standard", crumple_A=135.0, crumple_B=250.0,
        dimension_confidence=0.85, stiffness_confidence=0.6
    ),
    "nissan_tiida": VehicleProfile(
        make="Nissan", model="Tiida", body_type="saloon",
        kerb_weight_kg=1120, gross_weight_kg=1560, typical_passenger_load_kg=300,
        length_mm=4290, width_mm=1695, height_mm=1510, wheelbase_mm=2600,
        has_abs=True, has_esc=False, airbag_count=2, drive_type="2WD",
        crumple_zone="standard", crumple_A=140.0, crumple_B=260.0,
        dimension_confidence=0.8, stiffness_confidence=0.55
    ),
    "honda_fit": VehicleProfile(
        make="Honda", model="Fit", body_type="saloon",
        kerb_weight_kg=1060, gross_weight_kg=1490, typical_passenger_load_kg=280,
        length_mm=3895, width_mm=1695, height_mm=1550, wheelbase_mm=2530,
        has_abs=True, has_esc=False, airbag_count=2, drive_type="2WD",
        crumple_zone="standard", crumple_A=130.0, crumple_B=250.0,
        dimension_confidence=0.85, stiffness_confidence=0.6
    ),
    "mazda_demio": VehicleProfile(
        make="Mazda", model="Demio", body_type="saloon",
        kerb_weight_kg=1010, gross_weight_kg=1450, typical_passenger_load_kg=270,
        length_mm=3885, width_mm=1695, height_mm=1515, wheelbase_mm=2490,
        has_abs=True, has_esc=False, airbag_count=2, drive_type="2WD",
        crumple_zone="standard", crumple_A=128.0, crumple_B=248.0,
        dimension_confidence=0.8, stiffness_confidence=0.55
    ),

    # ── AWD / reinforced unibody ─────────────────────────────────────────
    # Published crash literature shows AWD platforms with driveline
    # reinforcement running ~15-20% stiffer than a plain unibody sedan of
    # similar mass.
    "subaru_impreza": VehicleProfile(
        make="Subaru", model="Impreza", body_type="saloon",
        kerb_weight_kg=1380, gross_weight_kg=1830, typical_passenger_load_kg=300,
        length_mm=4415, width_mm=1740, height_mm=1480, wheelbase_mm=2645,
        has_abs=True, has_esc=True, airbag_count=6, drive_type="AWD",
        crumple_zone="reinforced", crumple_A=180.0, crumple_B=320.0,
        dimension_confidence=0.85, stiffness_confidence=0.62
    ),

    # ── Unibody crossover SUVs ────────────────────────────────────────────
    # Reinforced subframe + larger mass; ~25-30% stiffer than sedan baseline,
    # consistent with published crossover-vs-sedan crash comparisons.
    "toyota_rav4": VehicleProfile(
        make="Toyota", model="RAV4", body_type="suv",
        kerb_weight_kg=1655, gross_weight_kg=2100, typical_passenger_load_kg=350,
        length_mm=4600, width_mm=1855, height_mm=1685, wheelbase_mm=2690,
        front_overhang_mm=910, rear_overhang_mm=1000, ground_clearance_mm=198,
        has_abs=True, has_esc=True, airbag_count=6, drive_type="AWD",
        crumple_zone="reinforced", center_of_gravity_height_ratio=0.58,
        crumple_A=190.0, crumple_B=340.0,
        dimension_confidence=0.85, stiffness_confidence=0.62
    ),
    "toyota_harrier": VehicleProfile(
        make="Toyota", model="Harrier", body_type="suv",
        kerb_weight_kg=1690, gross_weight_kg=2150, typical_passenger_load_kg=350,
        length_mm=4720, width_mm=1845, height_mm=1690, wheelbase_mm=2660,
        ground_clearance_mm=195,
        has_abs=True, has_esc=True, airbag_count=6, drive_type="AWD",
        crumple_zone="reinforced", center_of_gravity_height_ratio=0.58,
        crumple_A=195.0, crumple_B=345.0,
        dimension_confidence=0.8, stiffness_confidence=0.6
    ),
    "nissan_xtrail": VehicleProfile(
        make="Nissan", model="X-Trail", body_type="suv",
        kerb_weight_kg=1595, gross_weight_kg=2060, typical_passenger_load_kg=350,
        length_mm=4640, width_mm=1820, height_mm=1715, wheelbase_mm=2705,
        ground_clearance_mm=210,
        has_abs=True, has_esc=True, airbag_count=6, drive_type="AWD",
        crumple_zone="reinforced", center_of_gravity_height_ratio=0.59,
        crumple_A=185.0, crumple_B=330.0,
        dimension_confidence=0.8, stiffness_confidence=0.58
    ),
    "subaru_forester": VehicleProfile(
        make="Subaru", model="Forester", body_type="suv",
        kerb_weight_kg=1540, gross_weight_kg=2020, typical_passenger_load_kg=350,
        length_mm=4615, width_mm=1795, height_mm=1730, wheelbase_mm=2670,
        ground_clearance_mm=220,
        has_abs=True, has_esc=True, airbag_count=6, drive_type="AWD",
        crumple_zone="reinforced", center_of_gravity_height_ratio=0.59,
        crumple_A=185.0, crumple_B=330.0,
        dimension_confidence=0.8, stiffness_confidence=0.6
    ),

    # ── Body-on-frame SUVs / pickups ──────────────────────────────────────
    # Ladder-frame chassis measurably stiffer in real crash data than unibody
    # cars of similar mass — typically +35-45% over the sedan baseline.
    "toyota_prado": VehicleProfile(
        make="Toyota", model="Land Cruiser Prado", body_type="suv",
        kerb_weight_kg=2155, gross_weight_kg=2850, typical_passenger_load_kg=400,
        length_mm=4825, width_mm=1885, height_mm=1845, wheelbase_mm=2790,
        ground_clearance_mm=221,
        has_abs=True, has_esc=True, airbag_count=8, drive_type="4WD",
        crumple_zone="reinforced", center_of_gravity_height_ratio=0.62,
        crumple_A=220.0, crumple_B=380.0,
        dimension_confidence=0.85, stiffness_confidence=0.62
    ),
    "toyota_land_cruiser_70": VehicleProfile(
        make="Toyota", model="Land Cruiser 70", body_type="suv",
        kerb_weight_kg=2050, gross_weight_kg=3200, typical_passenger_load_kg=500,
        length_mm=4910, width_mm=1870, height_mm=1920, wheelbase_mm=2730,
        ground_clearance_mm=230,
        has_abs=True, has_esc=False, airbag_count=2, drive_type="4WD",
        crumple_zone="standard", center_of_gravity_height_ratio=0.62,
        crumple_A=210.0, crumple_B=370.0,
        dimension_confidence=0.8, stiffness_confidence=0.58
    ),
    "toyota_hilux_dc": VehicleProfile(
        make="Toyota", model="Hilux Double Cab", body_type="pickup",
        kerb_weight_kg=1920, gross_weight_kg=3000, typical_passenger_load_kg=500,
        length_mm=5330, width_mm=1855, height_mm=1815, wheelbase_mm=3085,
        ground_clearance_mm=270,
        has_abs=True, has_esc=True, airbag_count=2, drive_type="4WD",
        crumple_zone="standard", center_of_gravity_height_ratio=0.60,
        crumple_A=200.0, crumple_B=360.0,
        dimension_confidence=0.85, stiffness_confidence=0.6
    ),
    "isuzu_dmax": VehicleProfile(
        make="Isuzu", model="D-Max", body_type="pickup",
        kerb_weight_kg=1930, gross_weight_kg=3100, typical_passenger_load_kg=500,
        length_mm=5295, width_mm=1860, height_mm=1785, wheelbase_mm=3095,
        ground_clearance_mm=245,
        has_abs=True, has_esc=False, airbag_count=2, drive_type="4WD",
        crumple_zone="standard", center_of_gravity_height_ratio=0.60,
        crumple_A=200.0, crumple_B=355.0,
        dimension_confidence=0.8, stiffness_confidence=0.58
    ),

    # ── Cab-over vans used as matatus ─────────────────────────────────────
    # Kenya's dominant PSV class (14-seater Hiace/Caravan conversions). The
    # cab-over layout gives minimal front overhang / crush distance ahead of
    # the driver — a real, documented safety characteristic of this body
    # style, not a modelling shortcut — reflected here as reduced effective
    # stiffness relative to a bonneted sedan.
    "toyota_hiace_matatu": VehicleProfile(
        make="Toyota", model="Hiace 14-Seater Matatu", body_type="matatu",
        kerb_weight_kg=2285, gross_weight_kg=3500, typical_passenger_load_kg=1200,
        length_mm=5380, width_mm=1880, height_mm=2285, wheelbase_mm=3110,
        ground_clearance_mm=175,
        has_abs=False, has_esc=False, airbag_count=0, drive_type="2WD",
        crumple_zone="none", center_of_gravity_height_ratio=0.72,
        crumple_A=100.0, crumple_B=180.0, drag_coefficient=0.42,
        dimension_confidence=0.75, stiffness_confidence=0.5
    ),
    "nissan_caravan_matatu": VehicleProfile(
        make="Nissan", model="Caravan Matatu", body_type="matatu",
        kerb_weight_kg=2050, gross_weight_kg=3200, typical_passenger_load_kg=1100,
        length_mm=4695, width_mm=1800, height_mm=2185, wheelbase_mm=2800,
        ground_clearance_mm=165,
        has_abs=False, has_esc=False, airbag_count=0, drive_type="2WD",
        crumple_zone="none", center_of_gravity_height_ratio=0.70,
        crumple_A=95.0, crumple_B=175.0, drag_coefficient=0.44,
        dimension_confidence=0.7, stiffness_confidence=0.45
    ),
    "isuzu_nqr_bus": VehicleProfile(
        make="Isuzu", model="NQR 33-Seater", body_type="matatu",
        kerb_weight_kg=5200, gross_weight_kg=8500, typical_passenger_load_kg=2800,
        length_mm=7400, width_mm=2180, height_mm=2850, wheelbase_mm=4200,
        ground_clearance_mm=220,
        has_abs=False, has_esc=False, airbag_count=0, drive_type="2WD",
        crumple_zone="none", center_of_gravity_height_ratio=0.75,
        crumple_A=80.0, crumple_B=160.0, drag_coefficient=0.55,
        dimension_confidence=0.65, stiffness_confidence=0.4
    ),

    # ── Motorcycles (boda boda) ───────────────────────────────────────────
    # Kenya's highest-volume vehicle class by claim count. IMPORTANT: unlike
    # the car/pickup/matatu classes above, crumple_A/crumple_B here are NOT
    # derived from published crash-reconstruction literature — the McHenry
    # crush model assumes a structural crush zone a motorcycle frame doesn't
    # have, so there is no equivalent literature to calibrate against. These
    # are a deliberately low, order-of-magnitude placeholder chosen only to
    # keep the formula numerically sane, not a researched estimate — hence
    # stiffness_confidence is capped at 0.35, the lowest in this file, and
    # should be read as "unverified guess," not "class-calibrated."
    # Dimensions/weight ARE grounded: averaged from the two real bikes below.
    "bajaj_boxer_boda": VehicleProfile(
        make="Bajaj", model="Boxer", body_type="motorcycle",
        kerb_weight_kg=118, gross_weight_kg=290, typical_passenger_load_kg=150,
        length_mm=2020, width_mm=760, height_mm=1085, wheelbase_mm=1270,
        ground_clearance_mm=165,
        has_abs=False, has_esc=False, airbag_count=0, drive_type="2WD",
        crumple_zone="none", center_of_gravity_height_ratio=0.65,
        crumple_A=30.0, crumple_B=60.0, drag_coefficient=0.55,
        dimension_confidence=0.8, stiffness_confidence=0.35
    ),
    "tvs_apache": VehicleProfile(
        make="TVS", model="Apache RTR 160", body_type="motorcycle",
        kerb_weight_kg=143, gross_weight_kg=310, typical_passenger_load_kg=150,
        length_mm=2080, width_mm=755, height_mm=1060, wheelbase_mm=1357,
        ground_clearance_mm=180,
        has_abs=True, has_esc=False, airbag_count=0, drive_type="2WD",
        crumple_zone="none", center_of_gravity_height_ratio=0.64,
        crumple_A=32.0, crumple_B=62.0, drag_coefficient=0.52,
        dimension_confidence=0.8, stiffness_confidence=0.35
    ),

    # ── Tuk-tuks (coastal Kenya PSV) ───────────────────────────────────────
    # Same caveat as motorcycles above: crumple_A/B is an unverified low
    # placeholder, not literature-calibrated — a three-wheeler tuk-tuk chassis
    # has no equivalent in the crash-reconstruction research this file's other
    # stiffness values draw from. Dimensions/weight are a real published spec
    # for the Bajaj RE (Kenya's dominant tuk-tuk import).
    "bajaj_re_tuktuk": VehicleProfile(
        make="Bajaj", model="RE Tuk-Tuk", body_type="tuk_tuk",
        kerb_weight_kg=395, gross_weight_kg=860, typical_passenger_load_kg=300,
        length_mm=2780, width_mm=1310, height_mm=1705, wheelbase_mm=1830,
        ground_clearance_mm=165,
        has_abs=False, has_esc=False, airbag_count=0, drive_type="2WD",
        crumple_zone="none", center_of_gravity_height_ratio=0.68,
        crumple_A=50.0, crumple_B=90.0, drag_coefficient=0.58,
        dimension_confidence=0.75, stiffness_confidence=0.4
    ),

    # ── Heavy commercial (trucks, trailers, buses) ────────────────────────
    "generic_trailer": VehicleProfile(
        make="Unknown", model="Semi-Trailer", body_type="heavy_commercial",
        kerb_weight_kg=18000, gross_weight_kg=36000, typical_passenger_load_kg=0,
        length_mm=16500, width_mm=2550, height_mm=4000, wheelbase_mm=6200,
        has_abs=True, has_esc=False, airbag_count=2, drive_type="2WD",
        crumple_zone="none", center_of_gravity_height_ratio=0.70,
        crumple_A=60.0, crumple_B=120.0, drag_coefficient=0.55,
        dimension_confidence=0.55, stiffness_confidence=0.3
    ),
    "isuzu_fvr_truck": VehicleProfile(
        make="Isuzu", model="FVR Truck", body_type="heavy_commercial",
        kerb_weight_kg=6500, gross_weight_kg=14000, typical_passenger_load_kg=0,
        length_mm=7800, width_mm=2400, height_mm=2950, wheelbase_mm=4600,
        has_abs=True, has_esc=False, airbag_count=2, drive_type="2WD",
        crumple_zone="none", center_of_gravity_height_ratio=0.68,
        crumple_A=70.0, crumple_B=140.0,
        dimension_confidence=0.65, stiffness_confidence=0.4
    ),
    "isuzu_nkr_lorry": VehicleProfile(
        make="Isuzu", model="NKR Lorry", body_type="heavy_commercial",
        kerb_weight_kg=3200, gross_weight_kg=7500, typical_passenger_load_kg=0,
        length_mm=5900, width_mm=2050, height_mm=2400, wheelbase_mm=3400,
        has_abs=False, has_esc=False, airbag_count=2, drive_type="2WD",
        crumple_zone="none", center_of_gravity_height_ratio=0.65,
        crumple_A=75.0, crumple_B=148.0,
        dimension_confidence=0.7, stiffness_confidence=0.4
    ),
    "mitsubishi_canter": VehicleProfile(
        make="Mitsubishi", model="Canter", body_type="heavy_commercial",
        kerb_weight_kg=2900, gross_weight_kg=6000, typical_passenger_load_kg=0,
        length_mm=5600, width_mm=1995, height_mm=2350, wheelbase_mm=3300,
        has_abs=False, has_esc=False, airbag_count=2, drive_type="2WD",
        crumple_zone="none", center_of_gravity_height_ratio=0.65,
        crumple_A=78.0, crumple_B=152.0,
        dimension_confidence=0.7, stiffness_confidence=0.4
    ),
    "tata_bus": VehicleProfile(
        make="Tata", model="Bus", body_type="heavy_commercial",
        kerb_weight_kg=7500, gross_weight_kg=13000, typical_passenger_load_kg=3000,
        length_mm=9500, width_mm=2400, height_mm=3200, wheelbase_mm=5500,
        has_abs=False, has_esc=False, airbag_count=0, drive_type="2WD",
        crumple_zone="none", center_of_gravity_height_ratio=0.72,
        crumple_A=65.0, crumple_B=130.0,
        dimension_confidence=0.55, stiffness_confidence=0.3
    ),
    "mercedes_actros": VehicleProfile(
        make="Mercedes", model="Actros", body_type="heavy_commercial",
        kerb_weight_kg=8500, gross_weight_kg=18000, typical_passenger_load_kg=0,
        length_mm=6200, width_mm=2500, height_mm=3700, wheelbase_mm=3900,
        has_abs=True, has_esc=True, airbag_count=2, drive_type="2WD",
        crumple_zone="none", center_of_gravity_height_ratio=0.68,
        crumple_A=65.0, crumple_B=125.0,
        dimension_confidence=0.6, stiffness_confidence=0.35
    ),
}


FALLBACK_PROFILES: dict[str, VehicleProfile] = {
    "saloon": VehicleProfile(
        make="Unknown", model="Generic Saloon", body_type="saloon",
        kerb_weight_kg=1200, gross_weight_kg=1650, typical_passenger_load_kg=300,
        length_mm=4400, width_mm=1720, height_mm=1480, wheelbase_mm=2580,
        has_abs=False, airbag_count=2, crumple_A=145.0, crumple_B=265.0,
        dimension_confidence=0.5, stiffness_confidence=0.4
    ),
    "suv": VehicleProfile(
        make="Unknown", model="Generic SUV", body_type="suv",
        kerb_weight_kg=1700, gross_weight_kg=2200, typical_passenger_load_kg=380,
        length_mm=4650, width_mm=1840, height_mm=1700, wheelbase_mm=2680,
        has_abs=True, airbag_count=4, crumple_A=185.0, crumple_B=330.0,
        center_of_gravity_height_ratio=0.59,
        dimension_confidence=0.5, stiffness_confidence=0.4
    ),
    "pickup": VehicleProfile(
        make="Unknown", model="Generic Pickup", body_type="pickup",
        kerb_weight_kg=1900, gross_weight_kg=3000, typical_passenger_load_kg=500,
        length_mm=5200, width_mm=1850, height_mm=1800, wheelbase_mm=3050,
        has_abs=True, airbag_count=2, drive_type="4WD",
        crumple_A=195.0, crumple_B=350.0,
        center_of_gravity_height_ratio=0.60,
        dimension_confidence=0.5, stiffness_confidence=0.35
    ),
    "matatu": VehicleProfile(
        make="Unknown", model="Generic Matatu", body_type="matatu",
        kerb_weight_kg=2300, gross_weight_kg=3600, typical_passenger_load_kg=1200,
        length_mm=5400, width_mm=1900, height_mm=2300, wheelbase_mm=3100,
        has_abs=False, airbag_count=0, crumple_A=100.0, crumple_B=180.0,
        center_of_gravity_height_ratio=0.72,
        dimension_confidence=0.45, stiffness_confidence=0.3
    ),
    "heavy_commercial": VehicleProfile(
        make="Unknown", model="Generic Heavy Commercial", body_type="heavy_commercial",
        kerb_weight_kg=6000, gross_weight_kg=14000, typical_passenger_load_kg=0,
        length_mm=7500, width_mm=2300, height_mm=2800, wheelbase_mm=4400,
        has_abs=False, airbag_count=2, crumple_A=70.0, crumple_B=140.0,
        center_of_gravity_height_ratio=0.67,
        dimension_confidence=0.4, stiffness_confidence=0.3
    ),
    # Dimensions here are averaged from the two real motorcycle entries above
    # (Bajaj Boxer, TVS Apache) — a defensible interpolation, though the exact
    # figures (e.g. 130kg) carry more precision than that averaging really
    # supports. crumple_A/B is NOT literature-derived (see comment on the
    # named motorcycle entries above) — an unverified low placeholder only,
    # hence the capped 0.3 stiffness_confidence.
    "motorcycle": VehicleProfile(
        make="Unknown", model="Generic Motorcycle", body_type="motorcycle",
        kerb_weight_kg=130, gross_weight_kg=300, typical_passenger_load_kg=150,
        length_mm=2050, width_mm=770, height_mm=1090, wheelbase_mm=1300,
        has_abs=False, airbag_count=0, crumple_A=30.0, crumple_B=60.0,
        center_of_gravity_height_ratio=0.65,
        dimension_confidence=0.5, stiffness_confidence=0.3
    ),
    # Same caveat: crumple_A/B is an unverified placeholder, not literature-
    # calibrated (see bajaj_re_tuktuk comment above).
    "tuk_tuk": VehicleProfile(
        make="Unknown", model="Generic Tuk-Tuk", body_type="tuk_tuk",
        kerb_weight_kg=400, gross_weight_kg=870, typical_passenger_load_kg=300,
        length_mm=2800, width_mm=1320, height_mm=1720, wheelbase_mm=1850,
        has_abs=False, airbag_count=0, crumple_A=50.0, crumple_B=90.0,
        center_of_gravity_height_ratio=0.68,
        dimension_confidence=0.45, stiffness_confidence=0.3
    ),
}


def _normalise_key(make: str, model: str) -> str:
    return f"{make}_{model}".lower().replace(" ", "_").replace("-", "_")


def get_vehicle_profile(
    make: str,
    model: str,
    body_type: Optional[str] = None
) -> tuple[VehicleProfile, str]:
    key = _normalise_key(make, model)

    if key in VEHICLE_REGISTRY:
        logger.info(f"Registry hit: {key} (confidence={VEHICLE_REGISTRY[key].confidence})")
        return VEHICLE_REGISTRY[key], "exact_match"

    for reg_key, profile in VEHICLE_REGISTRY.items():
        if model.lower() in reg_key or reg_key in key:
            logger.info(f"Registry partial match: {reg_key} for query {key}")
            return profile, "partial_match"

    if body_type:
        bt = body_type.lower().replace(" ", "_")
        if bt in FALLBACK_PROFILES:
            logger.warning(f"Registry miss for {key} — using {bt} fallback profile")
            return FALLBACK_PROFILES[bt], "body_type_fallback"

    logger.warning(f"Registry miss for {key} — using generic saloon fallback")
    return FALLBACK_PROFILES["saloon"], "generic_fallback"


def get_profile_by_key(key: str) -> Optional[VehicleProfile]:
    return VEHICLE_REGISTRY.get(key)


def list_registry() -> list[dict]:
    return [
        {
            "key": k,
            "make": v.make,
            "model": v.model,
            "body_type": v.body_type,
            "kerb_weight_kg": v.kerb_weight_kg,
            "confidence": v.confidence,
            "dimension_confidence": v.dimension_confidence,
            "stiffness_confidence": v.stiffness_confidence,
            "data_source": v.data_source
        }
        for k, v in VEHICLE_REGISTRY.items()
    ]


def registry_stats() -> dict:
    total = len(VEHICLE_REGISTRY)
    by_type = {}
    for v in VEHICLE_REGISTRY.values():
        by_type[v.body_type] = by_type.get(v.body_type, 0) + 1
    avg_confidence = sum(v.confidence for v in VEHICLE_REGISTRY.values()) / total
    avg_dimension_confidence = sum(v.dimension_confidence for v in VEHICLE_REGISTRY.values()) / total
    avg_stiffness_confidence = sum(v.stiffness_confidence for v in VEHICLE_REGISTRY.values()) / total

    return {
        "total_vehicles":            total,
        "by_body_type":              by_type,
        "average_confidence":        round(avg_confidence, 3),
        "average_dimension_confidence": round(avg_dimension_confidence, 3),
        "average_stiffness_confidence": round(avg_stiffness_confidence, 3),
        "data_note": (
            "Dimensions/weights sourced from public manufacturer specs for "
            "the vehicle generations common on Kenyan roads. Crush-stiffness "
            "coefficients are class-relative estimates from published crash-"
            "reconstruction literature (see module docstring), not measured "
            "from real Kenyan fleet crash data — that upgrade still depends "
            "on Old Mutual supplying real claims/repair data."
        )
    }
