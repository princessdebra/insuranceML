import sqlite3
import logging
from pathlib import Path

DB_PATH = Path("claims_database.db")

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")


FRICTION_MU_ROWS = [
    ("asphalt",          "dry",      0.75, "Standard tarmac, good condition"),
    ("asphalt",          "wet",      0.45, "Post-rain surface — most common Nairobi scenario"),
    ("asphalt",          "flooded",  0.25, "Standing water — aquaplaning risk"),
    ("asphalt",          "dusty",    0.55, "Dry season dust layer on tarmac"),
    ("asphalt",          "oily",     0.30, "Fuel/oil spill — high fraud narrative flag"),
    ("concrete",         "dry",      0.80, "Expressway/airport aprons"),
    ("concrete",         "wet",      0.50, "Common on Nairobi Expressway"),
    ("concrete",         "flooded",  0.28, "Urban flooding scenario"),
    ("murram",           "dry",      0.55, "Unpaved rural/estate roads"),
    ("murram",           "wet",      0.30, "Slippery — upcountry common"),
    ("murram",           "muddy",    0.18, "Heavy rain — Western Kenya / Mt Kenya region"),
    ("gravel",           "dry",      0.50, "Common on C-roads and estate access roads"),
    ("gravel",           "wet",      0.32, ""),
    ("dirt",             "dry",      0.45, "Farm tracks / rural access"),
    ("dirt",             "wet",      0.20, "Very slippery — high accident risk"),
    ("dirt",             "muddy",    0.12, "Extreme off-road"),
    ("cobblestone",      "dry",      0.65, "Older Nairobi CBD streets, Mombasa Old Town"),
    ("cobblestone",      "wet",      0.38, "Significantly reduced grip"),
    ("sand",             "dry",      0.35, "Coastal Kenya — Mombasa, Malindi"),
    ("sand",             "wet",      0.25, ""),
    ("potholed_asphalt", "dry",      0.60, "Degraded urban tarmac — evasive maneuvering risk"),
    ("potholed_asphalt", "wet",      0.38, ""),
    ("bridge_deck",      "dry",      0.70, "Steel grate or concrete deck"),
    ("bridge_deck",      "wet",      0.35, "Bridges ice/wet faster — Nyali, Thika Road bridges"),
    ("highway",          "dry",      0.75, "Alias for asphalt dry — Thika/Mombasa Road"),
    ("highway",          "wet",      0.45, ""),
    ("highway",          "flooded",  0.25, ""),
]


VEHICLE_REGISTRY_ROWS = [
    ("toyota_premio",        "Toyota",      "Premio",             "saloon",           1310,  1785,  300,  4495, 1695, 1475, 2600, 1, 4, "2WD", "standard",   160.0, 290.0, 0.52, 0.70, "dummy"),
    ("toyota_allion",        "Toyota",      "Allion",             "saloon",           1290,  1765,  300,  4545, 1695, 1480, 2600, 1, 4, "2WD", "standard",   155.0, 285.0, 0.52, 0.70, "dummy"),
    ("toyota_fielder",       "Toyota",      "Fielder",            "saloon",           1195,  1620,  320,  4395, 1695, 1500, 2600, 1, 2, "2WD", "standard",   145.0, 270.0, 0.52, 0.70, "dummy"),
    ("toyota_vitz",          "Toyota",      "Vitz",               "saloon",            930,  1360,  250,  3775, 1665, 1530, 2440, 1, 2, "2WD", "standard",   120.0, 240.0, 0.52, 0.65, "dummy"),
    ("toyota_axio",          "Toyota",      "Axio",               "saloon",           1080,  1535,  300,  4235, 1695, 1460, 2600, 1, 2, "2WD", "standard",   140.0, 265.0, 0.52, 0.65, "dummy"),
    ("toyota_probox",        "Toyota",      "Probox",             "saloon",           1025,  1490,  350,  4195, 1690, 1515, 2550, 0, 2, "2WD", "standard",   135.0, 250.0, 0.52, 0.70, "dummy"),
    ("nissan_tiida",         "Nissan",      "Tiida",              "saloon",           1120,  1560,  300,  4290, 1695, 1510, 2600, 1, 2, "2WD", "standard",   140.0, 260.0, 0.52, 0.60, "dummy"),
    ("subaru_impreza",       "Subaru",      "Impreza",            "saloon",           1380,  1830,  300,  4415, 1740, 1480, 2645, 1, 6, "AWD", "reinforced", 180.0, 320.0, 0.52, 0.70, "dummy"),
    ("honda_fit",            "Honda",       "Fit",                "saloon",           1060,  1490,  280,  3895, 1695, 1550, 2530, 1, 2, "2WD", "standard",   130.0, 250.0, 0.52, 0.65, "dummy"),
    ("mazda_demio",          "Mazda",       "Demio",              "saloon",           1010,  1450,  270,  3885, 1695, 1515, 2490, 1, 2, "2WD", "standard",   128.0, 248.0, 0.52, 0.65, "dummy"),
    ("toyota_rav4",          "Toyota",      "RAV4",               "suv",              1655,  2100,  350,  4600, 1855, 1685, 2690, 1, 6, "AWD", "reinforced", 190.0, 340.0, 0.58, 0.70, "dummy"),
    ("toyota_harrier",       "Toyota",      "Harrier",            "suv",              1690,  2150,  350,  4720, 1845, 1690, 2660, 1, 6, "AWD", "reinforced", 195.0, 345.0, 0.58, 0.70, "dummy"),
    ("toyota_prado",         "Toyota",      "Land Cruiser Prado", "suv",              2155,  2850,  400,  4825, 1885, 1845, 2790, 1, 8, "4WD", "reinforced", 220.0, 380.0, 0.62, 0.72, "dummy"),
    ("nissan_xtrail",        "Nissan",      "X-Trail",            "suv",              1595,  2060,  350,  4640, 1820, 1715, 2705, 1, 6, "AWD", "reinforced", 185.0, 330.0, 0.59, 0.68, "dummy"),
    ("subaru_forester",      "Subaru",      "Forester",           "suv",              1540,  2020,  350,  4615, 1795, 1730, 2670, 1, 6, "AWD", "reinforced", 185.0, 330.0, 0.59, 0.70, "dummy"),
    ("toyota_hilux_dc",      "Toyota",      "Hilux Double Cab",   "pickup",           1920,  3000,  500,  5330, 1855, 1815, 3085, 1, 2, "4WD", "standard",   200.0, 360.0, 0.60, 0.68, "dummy"),
    ("isuzu_dmax",           "Isuzu",       "D-Max",              "pickup",           1930,  3100,  500,  5295, 1860, 1785, 3095, 1, 2, "4WD", "standard",   200.0, 355.0, 0.60, 0.65, "dummy"),
    ("toyota_hiace_matatu",  "Toyota",      "Hiace 14-Seater",    "matatu",           2285,  3500, 1200,  5380, 1880, 2285, 3110, 0, 0, "2WD", "none",       100.0, 180.0, 0.72, 0.60, "dummy"),
    ("nissan_caravan_matatu","Nissan",      "Caravan Matatu",     "matatu",           2050,  3200, 1100,  4695, 1800, 2185, 2800, 0, 0, "2WD", "none",        95.0, 175.0, 0.70, 0.55, "dummy"),
    ("isuzu_nqr_bus",        "Isuzu",       "NQR 33-Seater",      "matatu",           5200,  8500, 2800,  7400, 2180, 2850, 4200, 0, 0, "2WD", "none",        80.0, 160.0, 0.75, 0.50, "dummy"),
    ("isuzu_fvr_truck",      "Isuzu",       "FVR Truck",          "heavy_commercial", 6500, 14000,    0,  7800, 2400, 2950, 4600, 1, 2, "2WD", "none",        70.0, 140.0, 0.68, 0.50, "dummy"),
    ("isuzu_nkr_lorry",      "Isuzu",       "NKR Lorry",          "heavy_commercial", 3200,  7500,    0,  5900, 2050, 2400, 3400, 0, 2, "2WD", "none",        75.0, 148.0, 0.65, 0.50, "dummy"),
    ("mitsubishi_canter",    "Mitsubishi",  "Canter",             "heavy_commercial", 2900,  6000,    0,  5600, 1995, 2350, 3300, 0, 2, "2WD", "none",        78.0, 152.0, 0.65, 0.50, "dummy"),
    ("generic_trailer",      "Unknown",     "Semi-Trailer",       "heavy_commercial",18000, 36000,    0, 16500, 2550, 4000, 6200, 1, 2, "2WD", "none",        60.0, 120.0, 0.70, 0.40, "dummy"),
    ("bajaj_boxer_boda",     "Bajaj",       "Boxer BM150",        "motorcycle",        118,   290,  150,  2020,  760, 1085, 1270, 0, 0, "2WD", "none",        30.0,  60.0, 0.65, 0.60, "dummy"),
    ("tvs_apache",           "TVS",         "Apache RTR 160",     "motorcycle",        143,   310,  150,  2080,  755, 1060, 1357, 1, 0, "2WD", "none",        32.0,  62.0, 0.64, 0.60, "dummy"),
    ("bajaj_re_tuktuk",      "Bajaj",       "RE Tuk-Tuk",         "tuk_tuk",           395,   860,  300,  2780, 1310, 1705, 1830, 0, 0, "2WD", "none",        50.0,  90.0, 0.68, 0.55, "dummy"),
]


CLASS_MAPPING_ROWS = [
    ("saloon",           "saloon_station_wagon"),
    ("suv",              "saloon_station_wagon"),
    ("pickup",           "light_commercial_psv"),
    ("matatu",           "light_commercial_psv"),
    ("heavy_commercial", "heavy_commercial"),
    ("motorcycle",       "motorcycle"),
    ("tuk_tuk",          "motorcycle"),
]


def create_tables(conn: sqlite3.Connection):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS friction_mu_lookup (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            road_type       TEXT    NOT NULL,
            weather         TEXT    NOT NULL,
            mu_value        REAL    NOT NULL,
            notes           TEXT,
            UNIQUE(road_type, weather)
        );

        CREATE TABLE IF NOT EXISTS vehicle_registry (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            registry_key        TEXT    NOT NULL UNIQUE,
            make                TEXT    NOT NULL,
            model               TEXT    NOT NULL,
            body_type           TEXT    NOT NULL,
            kerb_weight_kg      REAL    NOT NULL,
            gross_weight_kg     REAL    NOT NULL,
            pax_load_kg         REAL    NOT NULL DEFAULT 0,
            length_mm           REAL,
            width_mm            REAL,
            height_mm           REAL,
            wheelbase_mm        REAL,
            has_abs             INTEGER NOT NULL DEFAULT 0,
            airbag_count        INTEGER NOT NULL DEFAULT 0,
            drive_type          TEXT,
            crumple_zone        TEXT,
            crumple_A           REAL    NOT NULL,
            crumple_B           REAL    NOT NULL,
            cog_height_ratio    REAL    NOT NULL DEFAULT 0.52,
            confidence          REAL    NOT NULL DEFAULT 0.5,
            data_source         TEXT    NOT NULL DEFAULT 'dummy'
        );

        CREATE TABLE IF NOT EXISTS vehicle_class_mapping (
            body_type       TEXT PRIMARY KEY,
            engine_class    TEXT NOT NULL
        );
    """)
    logger.info("Tables created (or already exist).")


def seed_friction_mu(conn: sqlite3.Connection):
    conn.executemany(
        """
        INSERT INTO friction_mu_lookup (road_type, weather, mu_value, notes)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(road_type, weather) DO UPDATE SET
            mu_value = excluded.mu_value,
            notes    = excluded.notes
        """,
        FRICTION_MU_ROWS
    )
    logger.info(f"Seeded {len(FRICTION_MU_ROWS)} friction mu rows.")


def seed_vehicle_registry(conn: sqlite3.Connection):
    conn.executemany(
        """
        INSERT INTO vehicle_registry (
            registry_key, make, model, body_type,
            kerb_weight_kg, gross_weight_kg, pax_load_kg,
            length_mm, width_mm, height_mm, wheelbase_mm,
            has_abs, airbag_count, drive_type, crumple_zone,
            crumple_A, crumple_B, cog_height_ratio, confidence, data_source
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(registry_key) DO UPDATE SET
            kerb_weight_kg   = excluded.kerb_weight_kg,
            gross_weight_kg  = excluded.gross_weight_kg,
            pax_load_kg      = excluded.pax_load_kg,
            crumple_A        = excluded.crumple_A,
            crumple_B        = excluded.crumple_B,
            confidence       = excluded.confidence,
            data_source      = excluded.data_source
        """,
        VEHICLE_REGISTRY_ROWS
    )
    logger.info(f"Seeded {len(VEHICLE_REGISTRY_ROWS)} vehicle registry rows.")


def seed_class_mapping(conn: sqlite3.Connection):
    conn.executemany(
        """
        INSERT INTO vehicle_class_mapping (body_type, engine_class)
        VALUES (?, ?)
        ON CONFLICT(body_type) DO UPDATE SET engine_class = excluded.engine_class
        """,
        CLASS_MAPPING_ROWS
    )
    logger.info(f"Seeded {len(CLASS_MAPPING_ROWS)} class mapping rows.")


def run():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    try:
        create_tables(conn)
        seed_friction_mu(conn)
        seed_vehicle_registry(conn)
        seed_class_mapping(conn)
        conn.commit()
        logger.info(f"Done. Database: {DB_PATH.resolve()}")
    except Exception as e:
        conn.rollback()
        logger.error(f"Seed failed: {e}")
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    run()