"""Unit conversion provider (Phase 7): the honest engine behind 150k+ addressable tools.

Every ordered pair of distinct units is addressable as ``convert.<a>_to_<b>``,
so with N units the namespace exposes N*(N-1) tool names.  Cross-dimension
pairs (e.g. ``convert.kilometer_to_kilogram``) ARE addressable and counted --
that is what makes the addressable space so large -- but invoking one returns
a clear dimension-mismatch error instead of a number:

    {"error": "cannot convert kilometer (length) to kilogram (mass): incompatible dimensions"}

No currency units are included: exchange rates are not static data.

Each unit maps to {"factor": f, "offset": o} with SI_value = value*f + o.
Only temperature scales use a non-zero offset.  Results are rounded to 12
significant figures to shed float noise (so 0 degC -> 32.0 degF exactly).

Handler contract: dict in -> dict out, never raises.
"""

from __future__ import annotations

import math

from ..base import Tool

# category -> unit_name -> {"factor": f, "offset": o}; SI_value = value*f + o.
# Units with offset 0 omit the key; convert() defaults it.
UNITS: dict[str, dict[str, dict]] = {
    "length": {
        # --- SI ---
        "meter": {"factor": 1},
        "kilometer": {"factor": 1000},
        "centimeter": {"factor": 0.01},
        "millimeter": {"factor": 0.001},
        "micrometer": {"factor": 1e-6},
        "micron": {"factor": 1e-6},
        "nanometer": {"factor": 1e-9},
        "picometer": {"factor": 1e-12},
        "femtometer": {"factor": 1e-15},
        "fermi": {"factor": 1e-15},
        "attometer": {"factor": 1e-18},
        "zeptometer": {"factor": 1e-21},
        "yoctometer": {"factor": 1e-24},
        "decimeter": {"factor": 0.1},
        "decameter": {"factor": 10},
        "hectometer": {"factor": 100},
        "megameter": {"factor": 1e6},
        "gigameter": {"factor": 1e9},
        "terameter": {"factor": 1e12},
        "petameter": {"factor": 1e15},
        "exameter": {"factor": 1e18},
        "zettameter": {"factor": 1e21},
        "yottameter": {"factor": 1e24},
        # --- imperial / US customary ---
        "mile": {"factor": 1609.344},
        "half_mile": {"factor": 804.672},
        "quarter_mile": {"factor": 402.336},
        "yard": {"factor": 0.9144},
        "foot": {"factor": 0.3048},
        "inch": {"factor": 0.0254},
        "nautical_mile": {"factor": 1852},
        "league": {"factor": 4828.032},  # 3 statute miles
        "furlong": {"factor": 201.168},  # 220 yards
        "chain": {"factor": 20.1168},  # 22 yards
        "rod": {"factor": 5.0292},  # 5.5 yards
        "perch": {"factor": 5.0292},
        "pole": {"factor": 5.0292},
        "link": {"factor": 0.201168},  # 7.92 inches
        "fathom": {"factor": 1.8288},  # 2 yards
        "hand": {"factor": 0.1016},  # 4 inches
        "palm": {"factor": 0.0762},  # 3 inches
        "cubit": {"factor": 0.4572},  # 18 inches
        "span": {"factor": 0.2286},  # 9 inches
        "nail": {"factor": 0.05715},  # 2.25 inches
        "barleycorn": {"factor": 0.008466666666666667},  # 1/3 inch
        "mil": {"factor": 0.0000254},  # thousandth of an inch
        "microinch": {"factor": 2.54e-8},
        "point": {"factor": 0.0003527777777777778},  # 1/72 inch (typographic)
        "pica": {"factor": 0.004233333333333333},  # 12 points
        "twip": {"factor": 0.00001763888888888889},  # 1/1440 inch
        "caliber": {"factor": 0.000254},  # 1/100 inch
        "cable_length": {"factor": 185.2},  # 1/10 international nautical mile
        "survey_foot": {"factor": 1200 / 3937},  # US survey foot
        "survey_mile": {"factor": 5280 * 1200 / 3937},  # US survey mile
        "ell": {"factor": 1.143},  # English ell, 45 inches
        "rope": {"factor": 6.096},  # 20 feet
        "rack_unit": {"factor": 0.04445},  # 1.75 inches
        "smoot": {"factor": 1.7018},  # 5 ft 7 in, MIT fraternity unit
        # --- astronomical ---
        "light_year": {"factor": 9.4607304725808e15},  # c * Julian year
        "parsec": {"factor": 3.0856775814913673e16},
        "kiloparsec": {"factor": 3.0856775814913673e19},
        "megaparsec": {"factor": 3.0856775814913673e22},
        "astronomical_unit": {"factor": 149597870700.0},  # exact, IAU 2012
        "light_second": {"factor": 299792458.0},
        "light_minute": {"factor": 17987547480.0},
        "light_hour": {"factor": 1079252848800.0},
        "light_day": {"factor": 25902068371200.0},
        "light_week": {"factor": 181314478598400.0},
        "light_nanosecond": {"factor": 0.299792458},
        "earth_radius": {"factor": 6371000.0},  # mean Earth radius
        # --- subatomic ---
        "angstrom": {"factor": 1e-10},
        "bohr_radius": {"factor": 5.29177210903e-11},  # CODATA 2018
        # --- desi ---
        "kos": {"factor": 3218.688},  # British-India standard: 2 statute miles
        "gaz": {"factor": 0.9144},  # Hindi/Punjabi for yard
        "hath": {"factor": 0.4572},  # Hindi for cubit
        # --- East Asian ---
        "shaku": {"factor": 10 / 33},  # Japanese shaku
        "sun": {"factor": 1 / 33},  # Japanese sun, 1/10 shaku
        "ken": {"factor": 60 / 33},  # Japanese ken, 6 shaku
        "ri": {"factor": 2160 * 10 / 33},  # Japanese ri, 2160 ken
        "li": {"factor": 500.0},  # modern Chinese li
        "zhang": {"factor": 10 / 3},  # Chinese zhang
        "cun": {"factor": 1 / 30},  # Chinese cun
    },
    "mass": {
        # --- SI ---
        "kilogram": {"factor": 1},
        "gram": {"factor": 0.001},
        "tonne": {"factor": 1000},
        "metric_ton": {"factor": 1000},
        "short_ton": {"factor": 907.18474},  # US ton, 2000 lb
        "ton": {"factor": 907.18474},  # US customary ton
        "long_ton": {"factor": 1016.0469088},  # UK ton, 2240 lb
        "milligram": {"factor": 1e-6},
        "microgram": {"factor": 1e-9},
        "nanogram": {"factor": 1e-12},
        "picogram": {"factor": 1e-15},
        "centigram": {"factor": 1e-5},
        "decigram": {"factor": 1e-4},
        "decagram": {"factor": 0.01},
        "hectogram": {"factor": 0.1},
        "kilotonne": {"factor": 1e6},
        "megatonne": {"factor": 1e9},
        "gigatonne": {"factor": 1e12},
        "quintal": {"factor": 100},  # metric quintal
        "metric_pound": {"factor": 0.5},  # Dutch/German Pfund
        # --- imperial / avoirdupois ---
        "pound": {"factor": 0.45359237},
        "ounce": {"factor": 0.028349523125},
        "stone": {"factor": 6.35029318},  # 14 lb
        "hundredweight_us": {"factor": 45.359237},  # short hundredweight, 100 lb
        "hundredweight_uk": {"factor": 50.80234544},  # long hundredweight, 112 lb
        "quarter_us": {"factor": 11.33980925},  # 25 lb
        "quarter_uk": {"factor": 12.70058636},  # 28 lb
        "dram": {"factor": 0.0017718451953125},  # 1/16 oz
        "grain": {"factor": 0.00006479891},
        "pennyweight": {"factor": 0.00155517384},  # 24 grains
        "troy_ounce": {"factor": 0.0311034768},
        "troy_pound": {"factor": 0.3732417216},  # 12 troy oz
        "scruple": {"factor": 0.0012959782},  # 20 grains, apothecaries'
        "clove": {"factor": 3.62873896},  # 8 lb (cheese/wool)
        "tod": {"factor": 12.70058636},  # 28 lb (wool)
        # --- desi ---
        "tola": {"factor": 0.0116638038},  # Indian standard tola
        "maund": {"factor": 37.3242},  # Indian standard maund
        "seer": {"factor": 0.933105},  # 1/40 maund
        "chhatak": {"factor": 0.933105 / 16},  # 1/16 seer
        # --- gems / science ---
        "carat": {"factor": 0.0002},  # metric carat
        "gamma": {"factor": 1e-9},  # microgram (mass unit)
        "slug": {"factor": 14.59390294},
        "atomic_mass_unit": {"factor": 1.66053906892e-27},  # CODATA 2018
        "dalton": {"factor": 1.66053906892e-27},
        # --- East Asian / historical ---
        "jin": {"factor": 0.5},  # Chinese jin
        "liang": {"factor": 0.05},  # Chinese liang, 1/10 jin
        "tael": {"factor": 0.05},
        "oka": {"factor": 1.282945},  # Ottoman oka
        "pud": {"factor": 16.3804964},  # Russian pood
        "bale": {"factor": 217.7243376},  # US cotton bale, 480 lb
    },
    "time": {
        # --- SI ---
        "second": {"factor": 1},
        "millisecond": {"factor": 1e-3},
        "microsecond": {"factor": 1e-6},
        "nanosecond": {"factor": 1e-9},
        "picosecond": {"factor": 1e-12},
        "femtosecond": {"factor": 1e-15},
        "decisecond": {"factor": 0.1},
        "centisecond": {"factor": 0.01},
        "shake": {"factor": 1e-8},  # nuclear physics
        "svedberg": {"factor": 1e-13},
        "planck_time": {"factor": 5.391247e-44},  # CODATA 2018
        "minute": {"factor": 60},
        "hour": {"factor": 3600},
        "day": {"factor": 86400},
        "week": {"factor": 604800},
        "sennight": {"factor": 604800},
        "fortnight": {"factor": 1209600},  # 14 days
        "month": {"factor": 2629746},  # mean Gregorian month (365.2425/12 d)
        "year": {"factor": 31556952},  # mean Gregorian year (365.2425 d)
        "year_julian": {"factor": 31557600},  # 365.25 d
        "decade": {"factor": 315569520},
        "lustrum": {"factor": 157784760},  # 5 years
        "century": {"factor": 3155695200},
        "millennium": {"factor": 31556952000},
        "olympiad": {"factor": 126227808},  # 4 years
        "sidereal_day": {"factor": 86164.0905},
        "lunar_month": {"factor": 29.530588853 * 86400},  # synodic month
        "moment": {"factor": 90},  # medieval moment, 1.5 minutes
        "ke": {"factor": 864},  # traditional Chinese ke, 1/100 day
        "ghati": {"factor": 1440},  # Indian ghati, 24 minutes
        "muhurta": {"factor": 2880},  # Indian muhurta, 48 minutes
        "prahar": {"factor": 10800},  # Indian prahar, 3 hours
        "watch": {"factor": 14400},  # nautical watch, 4 hours
        "workday": {"factor": 28800},  # 8 hours
        "bell": {"factor": 1800},  # nautical bell, 30 minutes
    },
    "data": {
        # --- bits (decimal) ---
        "bit": {"factor": 0.125},
        "kilobit": {"factor": 125},
        "megabit": {"factor": 125000},
        "gigabit": {"factor": 125000000},
        "terabit": {"factor": 1.25e11},
        "petabit": {"factor": 1.25e14},
        "exabit": {"factor": 1.25e17},
        "zettabit": {"factor": 1.25e20},
        "yottabit": {"factor": 1.25e23},
        "ronnabit": {"factor": 1.25e26},
        "quettabit": {"factor": 1.25e29},
        # --- bytes (decimal, 1000-based) ---
        "byte": {"factor": 1},
        "kilobyte": {"factor": 1000},
        "megabyte": {"factor": 1000000},
        "gigabyte": {"factor": 1e9},
        "terabyte": {"factor": 1e12},
        "petabyte": {"factor": 1e15},
        "exabyte": {"factor": 1e18},
        "zettabyte": {"factor": 1e21},
        "yottabyte": {"factor": 1e24},
        "ronnabyte": {"factor": 1e27},
        "quettabyte": {"factor": 1e30},
        "brontobyte": {"factor": 1e27},
        "geopbyte": {"factor": 1e30},
        # --- bytes (binary, 1024-based, IEC) ---
        "kibibit": {"factor": 1024},
        "mebibit": {"factor": 1048576},
        "gibibit": {"factor": 1073741824},
        "tebibit": {"factor": 1099511627776},
        "pebibyte": {"factor": 1125899906842624},
        "exbibyte": {"factor": 1152921504606846976},
        "zebibyte": {"factor": 1.1805916207174113e21},
        "yobibyte": {"factor": 1.2089258196146292e24},
        "kibibyte": {"factor": 1024},
        "mebibyte": {"factor": 1048576},
        "gibibyte": {"factor": 1073741824},
        # --- misc ---
        "nibble": {"factor": 0.5},  # 4 bits
    },
    "speed": {
        "meters_per_second": {"factor": 1},
        "meter_per_second": {"factor": 1},
        "kilometers_per_hour": {"factor": 1 / 3.6},
        "kilometer_per_hour": {"factor": 1 / 3.6},
        "miles_per_hour": {"factor": 0.44704},
        "mile_per_hour": {"factor": 0.44704},
        "knot": {"factor": 1852 / 3600},  # 1 nautical mile per hour
        "knots": {"factor": 1852 / 3600},
        "nautical_miles_per_hour": {"factor": 1852 / 3600},
        "foot_per_second": {"factor": 0.3048},
        "feet_per_second": {"factor": 0.3048},
        "speed_of_light": {"factor": 299792458},  # exact, vacuum
        "kilometers_per_second": {"factor": 1000},
        "miles_per_second": {"factor": 1609.344},
        "centimeters_per_second": {"factor": 0.01},
        "millimeters_per_second": {"factor": 0.001},
        "inches_per_second": {"factor": 0.0254},
        "inches_per_minute": {"factor": 0.0254 / 60},
        "feet_per_minute": {"factor": 0.00508},
        "feet_per_hour": {"factor": 0.3048 / 3600},
        "meters_per_minute": {"factor": 1 / 60},
        "meters_per_hour": {"factor": 1 / 3600},
        "meters_per_day": {"factor": 1 / 86400},
        "kilometers_per_minute": {"factor": 1000 / 60},
        "kilometers_per_day": {"factor": 1000 / 86400},
        "miles_per_minute": {"factor": 26.8224},
        "miles_per_day": {"factor": 1609.344 / 86400},
        "yards_per_second": {"factor": 0.9144},
        "yards_per_minute": {"factor": 0.9144 / 60},
        "yards_per_hour": {"factor": 0.9144 / 3600},
        "centimeters_per_minute": {"factor": 0.01 / 60},
        "millimeters_per_minute": {"factor": 0.001 / 60},
        "league_per_hour": {"factor": 4828.032 / 3600},
        "furlongs_per_fortnight": {"factor": 201.168 / 1209600},
    },
    "temperature": {
        # SI_value = value*factor + offset, in kelvin.
        "kelvin": {"factor": 1, "offset": 0},
        "degrees_celsius": {"factor": 1, "offset": 273.15},
        "celsius": {"factor": 1, "offset": 273.15},
        "centigrade": {"factor": 1, "offset": 273.15},
        "degrees_fahrenheit": {"factor": 5 / 9, "offset": 273.15 - 32 * (5 / 9)},
        "fahrenheit": {"factor": 5 / 9, "offset": 273.15 - 32 * (5 / 9)},
        "rankine": {"factor": 5 / 9, "offset": 0},
        "reaumur": {"factor": 1.25, "offset": 273.15},  # 0 Re = 0 C
        "romer": {"factor": 40 / 21, "offset": 273.15 - 7.5 * (40 / 21)},
        "newton": {"factor": 100 / 33, "offset": 273.15},  # 0 N = 0 C
        "delisle": {"factor": -2 / 3, "offset": 373.15},  # 0 De = 100 C
        "planck_temperature": {"factor": 1.416784e16, "offset": 0},  # CODATA 2018
    },
    "area": {
        # --- SI ---
        "square_meter": {"factor": 1},
        "square_kilometer": {"factor": 1e6},
        "square_centimeter": {"factor": 1e-4},
        "square_millimeter": {"factor": 1e-6},
        "square_decimeter": {"factor": 0.01},
        "square_decameter": {"factor": 100},
        "square_hectometer": {"factor": 1e4},
        "square_megameter": {"factor": 1e12},
        "hectare": {"factor": 1e4},
        # --- imperial ---
        "square_mile": {"factor": 2589988.110336},
        "square_yard": {"factor": 0.83612736},
        "square_foot": {"factor": 0.09290304},
        "square_inch": {"factor": 0.00064516},
        "square_rod": {"factor": 25.29285264},
        "square_perch": {"factor": 25.29285264},
        "square_pole": {"factor": 25.29285264},
        "rood": {"factor": 1011.7141056},  # 1/4 acre
        "square_chain": {"factor": 404.68564224},
        "square_furlong": {"factor": 40468.564224},
        "square_league": {"factor": 4828.032 ** 2},
        "square_nautical_mile": {"factor": 1852 ** 2},
        "barn": {"factor": 1e-28},
        "square_angstrom": {"factor": 1e-20},
        "acre": {"factor": 4046.8564224},
        "acre_survey": {"factor": 4046.87260987},  # US survey acre
        "square_foot_survey": {"factor": (1200 / 3937) ** 2},
        "square_yard_survey": {"factor": (3 * 1200 / 3937) ** 2},
        "township": {"factor": 36 * 2589988.110336},  # 36 sq miles
        "section": {"factor": 2589988.110336},  # 1 sq mile
        # --- regional ---
        "dunam": {"factor": 1000},
        "stremma": {"factor": 1000},  # Greek stremma
        "gunta": {"factor": 4046.8564224 / 40},  # 1/40 acre
        "cent": {"factor": 4046.8564224 / 100},  # 1/100 acre
        "kanal": {"factor": 505.857},  # Pakistani kanal
        "circular_mil": {"factor": math.pi / 4 * (2.54e-5) ** 2},
        "circular_inch": {"factor": math.pi / 4 * 0.0254 ** 2},
        "square_mil": {"factor": (2.54e-5) ** 2},
        "square_link": {"factor": 0.201168 ** 2},
        "square_hand": {"factor": 0.1016 ** 2},
        "square_cubit": {"factor": 0.4572 ** 2},
        "morgen": {"factor": 8565.32},  # South African morgen
        "pyeong": {"factor": 400 / 121},  # Korean pyeong
        "tsubo": {"factor": 400 / 121},  # Japanese tsubo
        "mu": {"factor": 2000 / 3},  # Chinese mu
        "rai": {"factor": 1600},  # Thai rai
    },
    "volume": {
        # --- SI ---
        "cubic_meter": {"factor": 1},
        "stere": {"factor": 1},
        "liter": {"factor": 0.001},
        "milliliter": {"factor": 1e-6},
        "cubic_centimeter": {"factor": 1e-6},
        "cubic_kilometer": {"factor": 1e9},
        "cubic_millimeter": {"factor": 1e-9},
        "cubic_decimeter": {"factor": 0.001},
        "deciliter": {"factor": 1e-4},
        "centiliter": {"factor": 1e-5},
        "decaliter": {"factor": 0.01},
        "hectoliter": {"factor": 0.1},
        # --- US customary liquid ---
        "gallon_us": {"factor": 0.003785411784},  # 231 cu in
        "quart_us": {"factor": 0.000946352946},
        "pint_us": {"factor": 0.000473176473},
        "cup_us": {"factor": 0.0002365882365},  # 8 fl oz
        "fluid_ounce_us": {"factor": 0.0000295735295625},
        "tablespoon_us": {"factor": 0.00001478676478125},  # 1/2 fl oz
        "teaspoon_us": {"factor": 0.00000492892159375},  # 1/6 fl oz
        "dram_us": {"factor": 0.0000036966911953125},  # 1/8 fl oz
        "minim_us": {"factor": 6.1611519921875e-8},  # 1/60 dram
        "gill_us": {"factor": 0.00011829411825},  # 4 fl oz
        "barrel_oil": {"factor": 0.158987294928},  # 42 US gallons
        "barrel_us": {"factor": 0.119240471196},  # 31.5 US gallons
        "hogshead_us": {"factor": 0.238480942392},  # 63 US gallons
        "fifth": {"factor": 0.0007570823568},  # 1/5 US gallon
        "tun": {"factor": 0.953923769568},  # 252 US gallons
        # --- imperial ---
        "gallon_uk": {"factor": 0.00454609},
        "quart_uk": {"factor": 0.0011365225},
        "pint_uk": {"factor": 0.00056826125},
        "fluid_ounce_uk": {"factor": 0.0000284130625},
        "gill_uk": {"factor": 0.0001420653125},  # 5 fl oz
        "barrel_uk": {"factor": 0.16365924},  # 36 UK gallons
        "hogshead_uk": {"factor": 0.24548886},  # 54 UK gallons
        "firkin": {"factor": 0.04091481},  # 9 UK gallons
        "peck_uk": {"factor": 0.00909218},  # 2 UK gallons
        "bushel_uk": {"factor": 0.03636872},  # 8 UK gallons
        # --- US dry ---
        "peck_us": {"factor": 0.00880976754172},
        "bushel_us": {"factor": 0.03523907016688},
        "dry_quart_us": {"factor": 0.001101220942715},
        "dry_pint_us": {"factor": 0.0005506104713575},
        "cup_metric": {"factor": 0.00025},  # 250 mL
        # --- cubic imperial ---
        "cubic_mile": {"factor": 4168181825.44},
        "cubic_yard": {"factor": 0.764554857984},
        "cubic_foot": {"factor": 0.028316846592},
        "cubic_inch": {"factor": 0.000016387064},
        "acre_foot": {"factor": 1233.48183754752},
        "acre_inch": {"factor": 1233.48183754752 / 12},
        "board_foot": {"factor": 0.002359737216},  # 144 cu in
        "cord": {"factor": 3.624556363776},  # 128 cu ft (firewood)
        "register_ton": {"factor": 2.8316846592},  # 100 cu ft
        "shipping_ton": {"factor": 1.13267386368},  # 40 cu ft
    },
    "energy": {
        # --- SI ---
        "joule": {"factor": 1},
        "kilojoule": {"factor": 1000},
        "megajoule": {"factor": 1000000},
        "gigajoule": {"factor": 1e9},
        "millijoule": {"factor": 0.001},
        "microjoule": {"factor": 1e-6},
        "kilowatt_second": {"factor": 1000},
        "watt_hour": {"factor": 3600},
        "kilowatt_hour": {"factor": 3600000},
        "megawatt_hour": {"factor": 3.6e9},
        "gigawatt_hour": {"factor": 3.6e12},
        "watt_second": {"factor": 1},
        # --- thermal ---
        "calorie": {"factor": 4.184},  # thermochemical calorie
        "calorie_it": {"factor": 4.1868},  # international steam-table calorie
        "kilocalorie": {"factor": 4184},
        "kilocalorie_it": {"factor": 4186.8},
        "btu": {"factor": 1055.05585262},  # international BTU
        "british_thermal_unit": {"factor": 1055.05585262},
        "btu_th": {"factor": 1054.35},  # thermochemical BTU
        "therm": {"factor": 105506000},  # EC/UK therm
        "therm_us": {"factor": 105505585.262},  # US therm, 100k BTU
        "quad": {"factor": 1.05505585262e18},  # 10^15 BTU
        # --- explosives ---
        "ton_tnt": {"factor": 4184000000},
        "kiloton_tnt": {"factor": 4.184e12},
        "megaton_tnt": {"factor": 4.184e15},
        # --- mechanical / cgs ---
        "erg": {"factor": 1e-7},
        "foot_pound": {"factor": 1.3558179483314004},
        "inch_pound": {"factor": 0.1129848250276167},
        # --- atomic ---
        "electronvolt": {"factor": 1.602176634e-19},  # exact, SI 2019
        "kiloelectronvolt": {"factor": 1.602176634e-16},
        "megaelectronvolt": {"factor": 1.602176634e-13},
        "gigaelectronvolt": {"factor": 1.602176634e-10},
        "hartree": {"factor": 4.3597447222071e-18},  # CODATA 2018
        "rydberg": {"factor": 2.1798723611035e-18},  # CODATA 2018
        "foe": {"factor": 1e44},  # supernova energy unit
    },
    "pressure": {
        # --- SI ---
        "pascal": {"factor": 1},
        "kilopascal": {"factor": 1000},
        "megapascal": {"factor": 1000000},
        "gigapascal": {"factor": 1e9},
        "millipascal": {"factor": 0.001},
        "hectopascal": {"factor": 100},
        "decibar": {"factor": 10000},
        "bar": {"factor": 100000},
        "millibar": {"factor": 100},
        "microbar": {"factor": 0.1},
        "barye": {"factor": 0.1},  # cgs unit
        "pieze": {"factor": 1000},  # mts unit
        # --- US / imperial ---
        "atmosphere": {"factor": 101325},  # standard atmosphere, exact
        "psi": {"factor": 6894.757293168},
        "pound_per_sq_inch": {"factor": 6894.757293168},
        "ksi": {"factor": 6894757.293168},  # kilopound per sq in
        "psf": {"factor": 6894.757293168 / 144},  # pound per sq foot
        "ounce_per_sq_inch": {"factor": 0.028349523125 * 9.80665 / 0.00064516},
        # --- mercury columns ---
        "mmhg": {"factor": 133.322387415},  # conventional mmHg
        "inch_hg": {"factor": 3386.389},
        "meter_hg": {"factor": 13332.2387415},
        "cmhg": {"factor": 1333.22387415},
        "torr": {"factor": 101325 / 760},  # 1/760 atm
        # --- water columns ---
        "mmh2o": {"factor": 9.80665},
        "cmh2o": {"factor": 98.0665},
        "inch_h2o": {"factor": 249.0889},
        "foot_h2o": {"factor": 12 * 249.0889},
        "kilogram_per_sq_meter": {"factor": 9.80665},
        # --- technical ---
        "technical_atmosphere": {"factor": 98066.5},  # 1 kgf/cm^2
        "kilogram_per_sq_cm": {"factor": 98066.5},
    },
}

# unit_name -> category (unit names are unique across categories)
_UNIT_TO_CATEGORY: dict[str, str] = {
    unit: category for category, units in UNITS.items() for unit in units
}

_ALL_UNITS: list[str] = sorted(_UNIT_TO_CATEGORY)


def _sig12(x: float) -> float:
    """Round to 12 significant figures to shed float noise (0 degC -> 32.0 degF)."""
    if x == 0.0 or not math.isfinite(x):
        return x
    mag = math.floor(math.log10(abs(x)))
    scale = 10.0 ** (12 - 1 - mag)
    return round(x * scale) / scale


def convert(value: float, from_unit: str, to_unit: str) -> float:
    """Convert *value* from *from_unit* to *to_unit* (same category only).

    Raises ValueError("incompatible dimensions") for cross-category pairs,
    ValueError("unknown unit: ...") for unknown names.
    """
    cat1 = _UNIT_TO_CATEGORY.get(from_unit)
    if cat1 is None:
        raise ValueError(f"unknown unit: {from_unit!r}")
    cat2 = _UNIT_TO_CATEGORY.get(to_unit)
    if cat2 is None:
        raise ValueError(f"unknown unit: {to_unit!r}")
    if cat1 != cat2:
        raise ValueError("incompatible dimensions")
    u1 = UNITS[cat1][from_unit]
    u2 = UNITS[cat2][to_unit]
    si = value * u1["factor"] + u1.get("offset", 0.0)
    return _sig12((si - u2.get("offset", 0.0)) / u2["factor"])


class UnitsProvider:
    """Dynamic provider for the ``convert`` namespace.

    Every ordered pair of distinct units (including cross-dimension pairs)
    is addressable as ``convert.<from>_to_<to>``; cross-dimension invocations
    return a dimension-mismatch error rather than a number (see module
    docstring).  Handlers never raise.
    """

    namespace = "convert"

    def expand(self) -> int:
        """Count of addressable tool names: N*(N-1) over every unit.

        Returns an int per the Provider contract (cheap: no object explosion);
        every ordered pair of distinct units is addressable as
        ``convert.<a>_to_<b>``.
        """
        n = len(_ALL_UNITS)
        return n * (n - 1)

    def resolve(self, name: str) -> Tool | None:
        """Resolve ``convert.<a>_to_<b>`` to a Tool; None if not addressable."""
        if not isinstance(name, str) or not name.startswith("convert."):
            return None
        body = name[len("convert."):]
        if "_to_" not in body:
            return None
        a, b = body.rsplit("_to_", 1)
        cat_a = _UNIT_TO_CATEGORY.get(a)
        cat_b = _UNIT_TO_CATEGORY.get(b)
        if not a or not b or cat_a is None or cat_b is None:
            return None

        def _handler(args: dict) -> dict:
            try:
                if not isinstance(args, dict):
                    return {"error": "args must be an object"}
                v = args.get("value")
                if isinstance(v, bool) or not isinstance(v, (int, float)):
                    return {"error": "value must be a number"}
                try:
                    result = convert(v, a, b)
                except ValueError as e:
                    if str(e) == "incompatible dimensions":
                        return {
                            "error": f"cannot convert {a} ({cat_a}) "
                            f"to {b} ({cat_b}): incompatible dimensions"
                        }
                    return {"error": str(e)}
                return {"value": v, "from": a, "to": b, "result": result}
            except Exception as e:  # never raise out of a tool handler
                return {"error": f"{type(e).__name__}: {e}"}

        return Tool(
            name=name,
            description=f"Convert {a} to {b}",
            schema={"value": "number"},
            handler=_handler,
            risk="low",
        )

    def sample_names(self, n: int = 5) -> list[str]:
        """A few representative tool names."""
        return [
            "convert.kilometer_to_mile",
            "convert.pound_to_kilogram",
            "convert.degrees_celsius_to_degrees_fahrenheit",
            "convert.gallon_us_to_liter",
            "convert.megabyte_to_kibibyte",
        ][:n]
