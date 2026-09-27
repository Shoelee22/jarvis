"""Math formulas provider (Phase 10): ~100 real named formulas.

Namespace: ``math.<formula>``. Each formula declares explicit named numeric
args, computes honestly with real constants, and never raises out of a
handler (validation failures return ``{"error": ...}``).

Constants used: pi (math.pi), g = 9.80665 m/s^2 (documented on each formula
that uses it). No invented constants.

Categories: geometry (32), finance (17), physics (22), statistics (16),
algebra (13) = 100 formulas.

expand() == 100 exactly; sample_names() works.
"""
from __future__ import annotations

import math
from collections import Counter

from ..base import Tool
from .providers import Provider

#: Standard gravity used by gravity-dependent physics formulas.
G = 9.80665  # m/s^2, documented on each formula that uses it


def _sig12(x):
    """Round a float to 12 significant figures to shed float noise."""
    if isinstance(x, bool) or not isinstance(x, float):
        return x
    if x == 0.0 or not math.isfinite(x):
        return x
    mag = math.floor(math.log10(abs(x)))
    scale = 10.0 ** (12 - 1 - mag)
    return round(x * scale) / scale


# ---------------------------------------------------------------------------
# formula table: name -> spec
#
# spec keys:
#   category    str
#   description str (documents the formula + constants used)
#   args        list of (name, kind, description); kind in
#               {"number", "int", "list", "numbers2"} where "numbers2" is a
#               second list arg (covariance/correlation).
#   defaults    {arg_name: default} for optional args
#   compute     fn(**kwargs) -> number | dict
#   validate    fn(kwargs) -> error str | None  (optional)
# ---------------------------------------------------------------------------

FORMULAS: dict[str, dict] = {}

def _f(name, category, description, args, compute, defaults=None, validate=None):
    FORMULAS[name] = {
        "category": category, "description": description,
        "args": args, "compute": compute,
        "defaults": defaults or {}, "validate": validate,
    }


# --- small validators ------------------------------------------------------
def _nonneg(*names):
    def _v(kw):
        for n in names:
            if kw[n] < 0:
                return f"{n} must be non-negative"
        return None
    return _v


def _positive(*names):
    def _v(kw):
        for n in names:
            if kw[n] <= 0:
                return f"{n} must be positive"
        return None
    return _v


# ============================ GEOMETRY (32) ================================
_f("circle_area", "geometry", "Area of a circle = pi r^2.",
   [("radius", "number", "radius")],
   lambda radius: math.pi * radius ** 2, validate=_nonneg("radius"))
_f("circle_circumference", "geometry", "Circumference of a circle = 2 pi r.",
   [("radius", "number", "radius")],
   lambda radius: 2 * math.pi * radius, validate=_nonneg("radius"))
_f("circle_diameter", "geometry", "Diameter of a circle = 2 r.",
   [("radius", "number", "radius")],
   lambda radius: 2 * radius, validate=_nonneg("radius"))
_f("circle_area_from_diameter", "geometry", "Area of a circle from diameter = pi d^2 / 4.",
   [("diameter", "number", "diameter")],
   lambda diameter: math.pi * diameter ** 2 / 4, validate=_nonneg("diameter"))
_f("sphere_volume", "geometry", "Volume of a sphere = 4/3 pi r^3.",
   [("radius", "number", "radius")],
   lambda radius: 4 / 3 * math.pi * radius ** 3, validate=_nonneg("radius"))
_f("sphere_surface_area", "geometry", "Surface area of a sphere = 4 pi r^2.",
   [("radius", "number", "radius")],
   lambda radius: 4 * math.pi * radius ** 2, validate=_nonneg("radius"))
_f("hemisphere_volume", "geometry", "Volume of a hemisphere = 2/3 pi r^3.",
   [("radius", "number", "radius")],
   lambda radius: 2 / 3 * math.pi * radius ** 3, validate=_nonneg("radius"))
_f("cylinder_volume", "geometry", "Volume of a cylinder = pi r^2 h.",
   [("radius", "number", "radius"), ("height", "number", "height")],
   lambda radius, height: math.pi * radius ** 2 * height,
   validate=_nonneg("radius", "height"))
_f("cylinder_surface_area", "geometry", "Total surface area of a cylinder = 2 pi r (r + h).",
   [("radius", "number", "radius"), ("height", "number", "height")],
   lambda radius, height: 2 * math.pi * radius * (radius + height),
   validate=_nonneg("radius", "height"))
_f("cone_volume", "geometry", "Volume of a cone = pi r^2 h / 3.",
   [("radius", "number", "radius"), ("height", "number", "height")],
   lambda radius, height: math.pi * radius ** 2 * height / 3,
   validate=_nonneg("radius", "height"))
_f("pyramid_volume", "geometry", "Volume of a pyramid = base_area * height / 3.",
   [("base_area", "number", "area of the base"), ("height", "number", "height")],
   lambda base_area, height: base_area * height / 3,
   validate=_nonneg("base_area", "height"))
_f("frustum_volume", "geometry",
   "Volume of a conical frustum = pi h / 3 (R^2 + R r + r^2).",
   [("radius_top", "number", "top radius"), ("radius_bottom", "number", "bottom radius"),
    ("height", "number", "height")],
   lambda radius_top, radius_bottom, height:
       math.pi * height / 3 * (radius_bottom ** 2 + radius_bottom * radius_top + radius_top ** 2),
   validate=_nonneg("radius_top", "radius_bottom", "height"))
_f("cube_volume", "geometry", "Volume of a cube = s^3.",
   [("side", "number", "side length")],
   lambda side: side ** 3, validate=_nonneg("side"))
_f("cube_surface_area", "geometry", "Surface area of a cube = 6 s^2.",
   [("side", "number", "side length")],
   lambda side: 6 * side ** 2, validate=_nonneg("side"))
_f("cuboid_volume", "geometry", "Volume of a cuboid = l w h.",
   [("length", "number", "length"), ("width", "number", "width"),
    ("height", "number", "height")],
   lambda length, width, height: length * width * height,
   validate=_nonneg("length", "width", "height"))
_f("triangle_area_base_height", "geometry", "Area of a triangle = b h / 2.",
   [("base", "number", "base"), ("height", "number", "height")],
   lambda base, height: base * height / 2, validate=_nonneg("base", "height"))
def _heron(side_a, side_b, side_c):
    s = (side_a + side_b + side_c) / 2
    return math.sqrt(s * (s - side_a) * (s - side_b) * (s - side_c))
_f("triangle_area_heron", "geometry", "Area of a triangle from three sides (Heron's formula).",
   [("side_a", "number", "side a"), ("side_b", "number", "side b"),
    ("side_c", "number", "side c")],
   _heron, validate=lambda kw:
       None if (kw["side_a"] > 0 and kw["side_b"] > 0 and kw["side_c"] > 0
                and kw["side_a"] + kw["side_b"] > kw["side_c"]
                and kw["side_a"] + kw["side_c"] > kw["side_b"]
                and kw["side_b"] + kw["side_c"] > kw["side_a"])
       else "sides must be positive and satisfy the triangle inequality")
_f("equilateral_triangle_area", "geometry", "Area of an equilateral triangle = sqrt(3)/4 s^2.",
   [("side", "number", "side length")],
   lambda side: math.sqrt(3) / 4 * side ** 2, validate=_nonneg("side"))
_f("pythagorean_hypotenuse", "geometry", "Hypotenuse of a right triangle = sqrt(a^2 + b^2).",
   [("leg_a", "number", "leg a"), ("leg_b", "number", "leg b")],
   lambda leg_a, leg_b: math.hypot(leg_a, leg_b),
   validate=_nonneg("leg_a", "leg_b"))
_f("rectangle_area", "geometry", "Area of a rectangle = l w.",
   [("length", "number", "length"), ("width", "number", "width")],
   lambda length, width: length * width, validate=_nonneg("length", "width"))
_f("rectangle_perimeter", "geometry", "Perimeter of a rectangle = 2 (l + w).",
   [("length", "number", "length"), ("width", "number", "width")],
   lambda length, width: 2 * (length + width), validate=_nonneg("length", "width"))
_f("square_area", "geometry", "Area of a square = s^2.",
   [("side", "number", "side length")],
   lambda side: side ** 2, validate=_nonneg("side"))
_f("square_perimeter", "geometry", "Perimeter of a square = 4 s.",
   [("side", "number", "side length")],
   lambda side: 4 * side, validate=_nonneg("side"))
_f("trapezoid_area", "geometry", "Area of a trapezoid = (b1 + b2)/2 * h.",
   [("base1", "number", "base 1"), ("base2", "number", "base 2"),
    ("height", "number", "height")],
   lambda base1, base2, height: (base1 + base2) / 2 * height,
   validate=_nonneg("base1", "base2", "height"))
_f("parallelogram_area", "geometry", "Area of a parallelogram = b h.",
   [("base", "number", "base"), ("height", "number", "height")],
   lambda base, height: base * height, validate=_nonneg("base", "height"))
_f("rhombus_area", "geometry", "Area of a rhombus from diagonals = d1 d2 / 2.",
   [("diagonal1", "number", "diagonal 1"), ("diagonal2", "number", "diagonal 2")],
   lambda diagonal1, diagonal2: diagonal1 * diagonal2 / 2,
   validate=_nonneg("diagonal1", "diagonal2"))
_f("ellipse_area", "geometry", "Area of an ellipse = pi a b (semi-major, semi-minor).",
   [("semi_major", "number", "semi-major axis"), ("semi_minor", "number", "semi-minor axis")],
   lambda semi_major, semi_minor: math.pi * semi_major * semi_minor,
   validate=_nonneg("semi_major", "semi_minor"))
_f("regular_hexagon_area", "geometry", "Area of a regular hexagon = (3 sqrt(3) / 2) s^2.",
   [("side", "number", "side length")],
   lambda side: 3 * math.sqrt(3) / 2 * side ** 2, validate=_nonneg("side"))
_f("annulus_area", "geometry", "Area of an annulus = pi (R^2 - r^2).",
   [("outer_radius", "number", "outer radius"), ("inner_radius", "number", "inner radius")],
   lambda outer_radius, inner_radius: math.pi * (outer_radius ** 2 - inner_radius ** 2),
   validate=lambda kw:
       "radii must be non-negative" if kw["outer_radius"] < 0 or kw["inner_radius"] < 0
       else ("inner radius must not exceed outer radius"
             if kw["inner_radius"] > kw["outer_radius"] else None))
_f("sector_area", "geometry", "Area of a circular sector = pi r^2 * angle_degrees / 360.",
   [("radius", "number", "radius"), ("angle_degrees", "number", "central angle in degrees")],
   lambda radius, angle_degrees: math.pi * radius ** 2 * angle_degrees / 360,
   validate=_nonneg("radius"))
_f("arc_length", "geometry", "Arc length = 2 pi r * angle_degrees / 360.",
   [("radius", "number", "radius"), ("angle_degrees", "number", "central angle in degrees")],
   lambda radius, angle_degrees: 2 * math.pi * radius * angle_degrees / 360,
   validate=_nonneg("radius"))
_f("distance_2d", "geometry", "Euclidean distance between two points in the plane.",
   [("x1", "number", "x of point 1"), ("y1", "number", "y of point 1"),
    ("x2", "number", "x of point 2"), ("y2", "number", "y of point 2")],
   lambda x1, y1, x2, y2: math.hypot(x2 - x1, y2 - y1))

# ============================ FINANCE (17) ================================
_f("compound_interest", "finance",
   "Compound interest: A = P (1 + r/n)^(n t); r is an annual percent. "
   "Returns final amount; interest_earned = A - P.",
   [("principal", "number", "principal"), ("annual_rate_percent", "number", "annual rate %"),
    ("years", "number", "years"), ("compounds_per_year", "number", "compounds per year (default 1)")],
   lambda principal, annual_rate_percent, years, compounds_per_year:
       principal * (1 + annual_rate_percent / 100 / compounds_per_year)
       ** (compounds_per_year * years),
   defaults={"compounds_per_year": 1},
   validate=lambda kw: "compounds_per_year must be positive" if kw["compounds_per_year"] <= 0
                      else ("principal and years must be non-negative"
                            if kw["principal"] < 0 or kw["years"] < 0 else None))
_f("simple_interest", "finance", "Simple interest: I = P r t / 100 (r an annual percent).",
   [("principal", "number", "principal"), ("annual_rate_percent", "number", "annual rate %"),
    ("years", "number", "years")],
   lambda principal, annual_rate_percent, years:
       principal * annual_rate_percent * years / 100,
   validate=_nonneg("principal", "years"))
_f("loan_emi", "finance",
   "Loan EMI: P r (1+r)^n / ((1+r)^n - 1) with monthly r = annual%/1200, n = years*12.",
   [("principal", "number", "principal"), ("annual_rate_percent", "number", "annual rate %"),
    ("years", "number", "years in months/12")],
   lambda principal, annual_rate_percent, years:
       principal / (years * 12) if annual_rate_percent == 0 else
       (lambda r, n: principal * r * (1 + r) ** n / ((1 + r) ** n - 1))
       (annual_rate_percent / 1200, years * 12),
   validate=_positive("principal", "years"))
def _loan_total_interest(principal, annual_rate_percent, years):
    n = years * 12
    if annual_rate_percent == 0:
        return 0.0
    r = annual_rate_percent / 1200
    emi = principal * r * (1 + r) ** n / ((1 + r) ** n - 1)
    return emi * n - principal
_f("loan_total_interest", "finance",
   "Total interest paid over a loan = EMI * months - principal.",
   [("principal", "number", "principal"), ("annual_rate_percent", "number", "annual rate %"),
    ("years", "number", "years")],
   _loan_total_interest, validate=_positive("principal", "years"))
_f("future_value", "finance", "Future value: FV = PV (1 + r)^n; r a decimal rate per period.",
   [("present_value", "number", "present value"), ("rate_percent", "number", "rate % per period"),
    ("periods", "number", "periods")],
   lambda present_value, rate_percent, periods:
       present_value * (1 + rate_percent / 100) ** periods,
   validate=_nonneg("periods"))
_f("present_value", "finance", "Present value: PV = FV / (1 + r)^n; r a decimal rate per period.",
   [("future_value", "number", "future value"), ("rate_percent", "number", "rate % per period"),
    ("periods", "number", "periods")],
   lambda future_value, rate_percent, periods:
       future_value / (1 + rate_percent / 100) ** periods,
   validate=_nonneg("periods"))
_f("rule_of_72", "finance", "Rule of 72: years to double money ~= 72 / annual_rate_percent.",
   [("annual_rate_percent", "number", "annual rate %")],
   lambda annual_rate_percent: 72 / annual_rate_percent,
   validate=_positive("annual_rate_percent"))
_f("roi", "finance", "Return on investment = (gain - cost) / cost * 100 %.",
   [("gain", "number", "total gain"), ("cost", "number", "total cost")],
   lambda gain, cost: (gain - cost) / cost * 100, validate=_positive("cost"))
_f("profit_margin", "finance", "Profit margin = (revenue - cost) / revenue * 100 %.",
   [("revenue", "number", "revenue"), ("cost", "number", "cost")],
   lambda revenue, cost: (revenue - cost) / revenue * 100,
   validate=_positive("revenue"))
_f("markup_percentage", "finance", "Markup = (price - cost) / cost * 100 %.",
   [("cost", "number", "cost"), ("selling_price", "number", "selling price")],
   lambda cost, selling_price: (selling_price - cost) / cost * 100,
   validate=_positive("cost"))
_f("discount_percentage", "finance", "Discount = (original - sale) / original * 100 %.",
   [("original_price", "number", "original price"), ("sale_price", "number", "sale price")],
   lambda original_price, sale_price:
       (original_price - sale_price) / original_price * 100,
   validate=_positive("original_price"))
_f("sales_tax_total", "finance", "Total with sales tax = price * (1 + tax_rate_percent/100).",
   [("price", "number", "price before tax"), ("tax_rate_percent", "number", "tax rate %")],
   lambda price, tax_rate_percent: price * (1 + tax_rate_percent / 100),
   validate=_nonneg("price"))
_f("tip_total", "finance", "Bill total with tip = bill * (1 + tip_percent/100).",
   [("bill", "number", "bill amount"), ("tip_percent", "number", "tip %")],
   lambda bill, tip_percent: bill * (1 + tip_percent / 100), validate=_nonneg("bill"))
_f("effective_annual_rate", "finance",
   "Effective annual rate = (1 + nominal%/100/n)^n - 1, returned as a percent.",
   [("nominal_rate_percent", "number", "nominal annual rate %"),
    ("compounds_per_year", "number", "compounds per year")],
   lambda nominal_rate_percent, compounds_per_year:
       ((1 + nominal_rate_percent / 100 / compounds_per_year) ** compounds_per_year - 1) * 100,
   validate=_positive("compounds_per_year"))
_f("depreciation_straight_line", "finance",
   "Straight-line depreciation per year = (cost - salvage) / life_years.",
   [("cost", "number", "initial cost"), ("salvage_value", "number", "salvage value"),
    ("life_years", "number", "useful life in years")],
   lambda cost, salvage_value, life_years: (cost - salvage_value) / life_years,
   validate=_positive("life_years"))
def _cagr(begin_value, end_value, years):
    return ((end_value / begin_value) ** (1 / years) - 1) * 100
_f("cagr", "finance",
   "Compound annual growth rate = ((end/begin)^(1/years) - 1) * 100 %.",
   [("begin_value", "number", "beginning value"), ("end_value", "number", "ending value"),
    ("years", "number", "years")],
   _cagr, validate=_positive("begin_value", "years"))
_f("break_even_units", "finance",
   "Break-even units = fixed_costs / (price_per_unit - variable_cost_per_unit).",
   [("fixed_costs", "number", "fixed costs"), ("price_per_unit", "number", "price per unit"),
    ("variable_cost_per_unit", "number", "variable cost per unit")],
   lambda fixed_costs, price_per_unit, variable_cost_per_unit:
       fixed_costs / (price_per_unit - variable_cost_per_unit),
   validate=lambda kw: "price per unit must exceed variable cost per unit"
                      if kw["price_per_unit"] <= kw["variable_cost_per_unit"] else None)

# ============================ PHYSICS (22) ================================
_f("kinetic_energy", "physics", "Kinetic energy = 1/2 m v^2 (joules).",
   [("mass_kg", "number", "mass in kg"), ("velocity_ms", "number", "velocity in m/s")],
   lambda mass_kg, velocity_ms: 0.5 * mass_kg * velocity_ms ** 2,
   validate=_nonneg("mass_kg"))
_f("potential_energy_gravity", "physics",
   f"Gravitational potential energy = m g h with g = {G} m/s^2 (joules).",
   [("mass_kg", "number", "mass in kg"), ("height_m", "number", "height in m")],
   lambda mass_kg, height_m: mass_kg * G * height_m,
   validate=_nonneg("mass_kg"))
_f("force_newton", "physics", "Force = m a (newtons, Newton's second law).",
   [("mass_kg", "number", "mass in kg"), ("acceleration_ms2", "number", "acceleration in m/s^2")],
   lambda mass_kg, acceleration_ms2: mass_kg * acceleration_ms2,
   validate=_nonneg("mass_kg"))
_f("weight_on_earth", "physics", f"Weight on Earth = m g with g = {G} m/s^2 (newtons).",
   [("mass_kg", "number", "mass in kg")],
   lambda mass_kg: mass_kg * G, validate=_nonneg("mass_kg"))
_f("speed", "physics", "Speed = distance / time.",
   [("distance_m", "number", "distance in m"), ("time_s", "number", "time in s")],
   lambda distance_m, time_s: distance_m / time_s,
   validate=_positive("time_s"))
_f("distance_from_speed_time", "physics", "Distance = speed * time.",
   [("speed_ms", "number", "speed in m/s"), ("time_s", "number", "time in s")],
   lambda speed_ms, time_s: speed_ms * time_s, validate=_nonneg("time_s"))
_f("travel_time", "physics", "Time = distance / speed.",
   [("distance_m", "number", "distance in m"), ("speed_ms", "number", "speed in m/s")],
   lambda distance_m, speed_ms: distance_m / speed_ms,
   validate=_positive("speed_ms"))
_f("ohms_law_voltage", "physics", "Ohm's law: V = I R.",
   [("current_a", "number", "current in amps"), ("resistance_ohm", "number", "resistance in ohms")],
   lambda current_a, resistance_ohm: current_a * resistance_ohm,
   validate=_nonneg("resistance_ohm"))
_f("ohms_law_current", "physics", "Ohm's law: I = V / R.",
   [("voltage_v", "number", "voltage in volts"), ("resistance_ohm", "number", "resistance in ohms")],
   lambda voltage_v, resistance_ohm: voltage_v / resistance_ohm,
   validate=_positive("resistance_ohm"))
_f("ohms_law_resistance", "physics", "Ohm's law: R = V / I.",
   [("voltage_v", "number", "voltage in volts"), ("current_a", "number", "current in amps")],
   lambda voltage_v, current_a: voltage_v / current_a,
   validate=_positive("current_a"))
_f("power_electrical", "physics", "Electrical power: P = V I (watts).",
   [("voltage_v", "number", "voltage in volts"), ("current_a", "number", "current in amps")],
   lambda voltage_v, current_a: voltage_v * current_a)
_f("momentum", "physics", "Linear momentum = m v.",
   [("mass_kg", "number", "mass in kg"), ("velocity_ms", "number", "velocity in m/s")],
   lambda mass_kg, velocity_ms: mass_kg * velocity_ms,
   validate=_nonneg("mass_kg"))
_f("work_done", "physics", "Work = force * distance (joules).",
   [("force_n", "number", "force in newtons"), ("distance_m", "number", "distance in m")],
   lambda force_n, distance_m: force_n * distance_m)
_f("pressure", "physics", "Pressure = force / area (pascals).",
   [("force_n", "number", "force in newtons"), ("area_m2", "number", "area in m^2")],
   lambda force_n, area_m2: force_n / area_m2, validate=_positive("area_m2"))
_f("density", "physics", "Density = mass / volume (kg/m^3).",
   [("mass_kg", "number", "mass in kg"), ("volume_m3", "number", "volume in m^3")],
   lambda mass_kg, volume_m3: mass_kg / volume_m3,
   validate=_positive("volume_m3"))
_f("frequency_from_period", "physics", "Frequency = 1 / period (Hz).",
   [("period_s", "number", "period in s")],
   lambda period_s: 1 / period_s, validate=_positive("period_s"))
_f("period_from_frequency", "physics", "Period = 1 / frequency (s).",
   [("frequency_hz", "number", "frequency in Hz")],
   lambda frequency_hz: 1 / frequency_hz, validate=_positive("frequency_hz"))
_f("wavelength", "physics", "Wavelength = wave speed / frequency.",
   [("wave_speed_ms", "number", "wave speed in m/s"), ("frequency_hz", "number", "frequency in Hz")],
   lambda wave_speed_ms, frequency_hz: wave_speed_ms / frequency_hz,
   validate=_positive("frequency_hz"))
_f("acceleration", "physics", "Average acceleration = (v_final - v_initial) / time.",
   [("v_initial_ms", "number", "initial velocity in m/s"),
    ("v_final_ms", "number", "final velocity in m/s"), ("time_s", "number", "time in s")],
   lambda v_initial_ms, v_final_ms, time_s: (v_final_ms - v_initial_ms) / time_s,
   validate=_positive("time_s"))
_f("pendulum_period", "physics",
   f"Period of a simple pendulum = 2 pi sqrt(L / g) with g = {G} m/s^2.",
   [("length_m", "number", "pendulum length in m")],
   lambda length_m: 2 * math.pi * math.sqrt(length_m / G),
   validate=_positive("length_m"))
_f("free_fall_time", "physics",
   f"Free-fall time from rest = sqrt(2 h / g) with g = {G} m/s^2.",
   [("height_m", "number", "drop height in m")],
   lambda height_m: math.sqrt(2 * height_m / G), validate=_nonneg("height_m"))
_f("projectile_range", "physics",
   f"Projectile range on flat ground = v^2 sin(2 theta) / g with g = {G} m/s^2.",
   [("velocity_ms", "number", "launch speed in m/s"),
    ("angle_degrees", "number", "launch angle in degrees")],
   lambda velocity_ms, angle_degrees:
       velocity_ms ** 2 * math.sin(2 * math.radians(angle_degrees)) / G,
   validate=_nonneg("velocity_ms"))

# ============================ STATISTICS (16) =============================
def _nums(kw, name="values"):
    v = kw[name]
    if not isinstance(v, (list, tuple)) or not v:
        raise ValueError(f"{name} must be a non-empty list of numbers")
    for x in v:
        if isinstance(x, bool) or not isinstance(x, (int, float)):
            raise ValueError(f"{name} must contain only numbers")
    return [float(x) for x in v]

_f("mean", "statistics", "Arithmetic mean of a list of numbers.",
   [("values", "list", "list of numbers")],
   lambda values: sum(_nums({"values": values})) / len(_nums({"values": values})))
def _median(values):
    xs = sorted(_nums({"values": values}))
    n = len(xs)
    mid = n // 2
    return (xs[mid - 1] + xs[mid]) / 2 if n % 2 == 0 else xs[mid]
_f("median", "statistics", "Median (middle value) of a list of numbers.",
   [("values", "list", "list of numbers")], _median)
def _mode(values):
    xs = _nums({"values": values})
    counts = Counter(xs)
    top = max(counts.values())
    for x in xs:  # first value to reach the top count wins (documented)
        if counts[x] == top:
            return x
_f("mode", "statistics",
   "Mode of a list of numbers (most frequent; first on ties).",
   [("values", "list", "list of numbers")], _mode)
def _var_pop(values):
    xs = _nums({"values": values})
    m = sum(xs) / len(xs)
    return sum((x - m) ** 2 for x in xs) / len(xs)
_f("variance_population", "statistics",
   "Population variance = mean of squared deviations.",
   [("values", "list", "list of numbers")], _var_pop)
_f("stdev_population", "statistics", "Population standard deviation = sqrt(population variance).",
   [("values", "list", "list of numbers")], lambda values: math.sqrt(_var_pop(values)))
def _var_samp(values):
    xs = _nums({"values": values})
    if len(xs) < 2:
        raise ValueError("sample variance needs at least 2 values")
    m = sum(xs) / len(xs)
    return sum((x - m) ** 2 for x in xs) / (len(xs) - 1)
_f("variance_sample", "statistics",
   "Sample variance (Bessel's correction, divides by n-1).",
   [("values", "list", "list of numbers")], _var_samp)
_f("stdev_sample", "statistics", "Sample standard deviation = sqrt(sample variance).",
   [("values", "list", "list of numbers")], lambda values: math.sqrt(_var_samp(values)))
_f("data_range", "statistics", "Range = max - min of a list of numbers.",
   [("values", "list", "list of numbers")],
   lambda values: max(_nums({"values": values})) - min(_nums({"values": values})))
def _percentile(values, p):
    xs = sorted(_nums({"values": values}))
    if not 0 <= p <= 100:
        raise ValueError("p must be between 0 and 100")
    rank = p / 100 * (len(xs) - 1)  # linear-interpolation method (documented)
    lo = math.floor(rank)
    hi = math.ceil(rank)
    return xs[lo] + (xs[hi] - xs[lo]) * (rank - lo)
_f("percentile", "statistics",
   "p-th percentile (linear-interpolation method): p in [0, 100].",
   [("values", "list", "list of numbers"), ("p", "number", "percentile 0-100")],
   _percentile)
_f("z_score", "statistics", "Z-score = (value - mean) / stdev.",
   [("value", "number", "value"), ("mean", "number", "mean"),
    ("stdev", "number", "standard deviation")],
   lambda value, mean, stdev: (value - mean) / stdev,
   validate=lambda kw: "stdev must be non-zero" if kw["stdev"] == 0 else None)
_f("sum_values", "statistics", "Sum of a list of numbers.",
   [("values", "list", "list of numbers")], lambda values: sum(_nums({"values": values})))
def _gmean(values):
    xs = _nums({"values": values})
    if any(x <= 0 for x in xs):
        raise ValueError("geometric mean needs positive values")
    return math.exp(sum(math.log(x) for x in xs) / len(xs))
_f("geometric_mean", "statistics", "Geometric mean (requires positive values).",
   [("values", "list", "list of numbers")], _gmean)
def _hmean(values):
    xs = _nums({"values": values})
    if any(x <= 0 for x in xs):
        raise ValueError("harmonic mean needs positive values")
    return len(xs) / sum(1 / x for x in xs)
_f("harmonic_mean", "statistics", "Harmonic mean (requires positive values).",
   [("values", "list", "list of numbers")], _hmean)
def _cov(x_values, y_values):
    xs = _nums({"x_values": x_values}, "x_values")
    ys = _nums({"y_values": y_values}, "y_values")
    if len(xs) != len(ys):
        raise ValueError("x_values and y_values must have equal length")
    if len(xs) < 2:
        raise ValueError("covariance needs at least 2 pairs")
    mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / (len(xs) - 1)
_f("covariance", "statistics", "Sample covariance of two equal-length lists.",
   [("x_values", "list", "list of numbers"), ("y_values", "list", "list of numbers")],
   _cov)
def _corr(x_values, y_values):
    xs = _nums({"x_values": x_values}, "x_values")
    ys = _nums({"y_values": y_values}, "y_values")
    if len(xs) != len(ys):
        raise ValueError("x_values and y_values must have equal length")
    if len(xs) < 2:
        raise ValueError("correlation needs at least 2 pairs")
    mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
    den = math.sqrt(sum((x - mx) ** 2 for x in xs) * sum((y - my) ** 2 for y in ys))
    if den == 0:
        raise ValueError("correlation undefined: zero variance")
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / den
_f("correlation_pearson", "statistics", "Pearson correlation coefficient of two lists.",
   [("x_values", "list", "list of numbers"), ("y_values", "list", "list of numbers")],
   _corr)
def _iqr(values):
    return _percentile(values, 75) - _percentile(values, 25)
_f("interquartile_range", "statistics",
   "Interquartile range = 75th percentile - 25th percentile.",
   [("values", "list", "list of numbers")], _iqr)

# ============================ ALGEBRA (13) ================================
def _qroots(a, b, c):
    if a == 0:
        raise ValueError("a must be non-zero for a quadratic")
    d = b * b - 4 * a * c
    if d >= 0:
        sq = math.sqrt(d)
        return {"root1": (-b + sq) / (2 * a), "root2": (-b - sq) / (2 * a)}
    sq = math.sqrt(-d)  # complex pair, formatted honestly
    re, im = -b / (2 * a), sq / (2 * a)
    return {"root1": f"{re}+{im}i", "root2": f"{re}-{im}i"}
_f("quadratic_roots", "algebra",
   "Roots of a x^2 + b x + c = 0; complex pair returned as strings when the discriminant < 0.",
   [("a", "number", "coefficient a"), ("b", "number", "coefficient b"),
    ("c", "number", "coefficient c")],
   _qroots)
_f("quadratic_discriminant", "algebra", "Discriminant of a quadratic = b^2 - 4 a c.",
   [("a", "number", "coefficient a"), ("b", "number", "coefficient b"),
    ("c", "number", "coefficient c")],
   lambda a, b, c: b * b - 4 * a * c)
def _fact(n):
    if not float(n).is_integer() or n < 0:
        raise ValueError("n must be a non-negative integer")
    if n > 1000:
        raise ValueError("n too large (cap 1000)")
    return math.factorial(int(n))
_f("factorial", "algebra", "Factorial n! (n a non-negative integer, cap 1000).",
   [("n", "int", "non-negative integer")], _fact)
def _gcd(a, b):
    if not float(a).is_integer() or not float(b).is_integer():
        raise ValueError("a and b must be integers")
    return math.gcd(int(a), int(b))
_f("gcd", "algebra", "Greatest common divisor of two integers.",
   [("a", "int", "integer"), ("b", "int", "integer")], _gcd)
def _lcm(a, b):
    if not float(a).is_integer() or not float(b).is_integer():
        raise ValueError("a and b must be integers")
    return abs(math.lcm(int(a), int(b)))
_f("lcm", "algebra", "Least common multiple of two integers.",
   [("a", "int", "integer"), ("b", "int", "integer")], _lcm)
_f("percentage", "algebra", "Percentage = part / whole * 100.",
   [("part", "number", "part"), ("whole", "number", "whole")],
   lambda part, whole: part / whole * 100,
   validate=lambda kw: "whole must be non-zero" if kw["whole"] == 0 else None)
_f("percentage_change", "algebra", "Percentage change = (new - old) / |old| * 100.",
   [("old_value", "number", "old value"), ("new_value", "number", "new value")],
   lambda old_value, new_value: (new_value - old_value) / abs(old_value) * 100,
   validate=lambda kw: "old_value must be non-zero" if kw["old_value"] == 0 else None)
_f("logarithm", "algebra", "Logarithm of value to the given base.",
   [("value", "number", "value"), ("base", "number", "base")],
   lambda value, base: math.log(value, base),
   validate=lambda kw: ("value must be positive" if kw["value"] <= 0
                        else ("base must be positive and not 1"
                              if kw["base"] <= 0 or kw["base"] == 1 else None)))
_f("power", "algebra", "Power = base ^ exponent.",
   [("base", "number", "base"), ("exponent", "number", "exponent")],
   lambda base, exponent: base ** exponent)
def _nth_root(value, n):
    n = int(n)
    if value < 0:
        if n % 2 == 0:
            raise ValueError("even root of a negative number")
        return -((-value) ** (1 / n))  # real odd root of a negative
    return value ** (1 / n)
_f("nth_root", "algebra", "n-th root of value (real root).",
   [("value", "number", "value"), ("n", "int", "root degree")],
   _nth_root,
   validate=lambda kw: "n must be a non-zero integer" if kw["n"] == 0
                       or not float(kw["n"]).is_integer() else None)
def _comb(n, k):
    if not float(n).is_integer() or not float(k).is_integer():
        raise ValueError("n and k must be integers")
    n, k = int(n), int(k)
    if n < 0 or k < 0 or k > n:
        raise ValueError("need 0 <= k <= n")
    return math.comb(n, k)
_f("combinations", "algebra", "Binomial coefficient C(n, k) = n! / (k! (n-k)!).",
   [("n", "int", "n"), ("k", "int", "k")], _comb)
def _perm(n, k):
    if not float(n).is_integer() or not float(k).is_integer():
        raise ValueError("n and k must be integers")
    n, k = int(n), int(k)
    if n < 0 or k < 0 or k > n:
        raise ValueError("need 0 <= k <= n")
    return math.perm(n, k)
_f("permutations", "algebra", "Permutations P(n, k) = n! / (n-k)!.",
   [("n", "int", "n"), ("k", "int", "k")], _perm)
def _fib(n):
    if not float(n).is_integer() or n < 0:
        raise ValueError("n must be a non-negative integer")
    if n > 100000:
        raise ValueError("n too large (cap 100000)")
    a, b = 0, 1
    for _ in range(int(n)):
        a, b = b, a + b
    return a
_f("fibonacci", "algebra", "n-th Fibonacci number (F(0)=0, F(1)=1; cap n=100000).",
   [("n", "int", "non-negative integer")], _fib)

assert len(FORMULAS) == 100, f"expected 100 formulas, got {len(FORMULAS)}"


# ---------------------------------------------------------------------------
# provider
# ---------------------------------------------------------------------------

def _coerce(name: str, kind: str, value):
    """Coerce/validate one arg; raises ValueError on bad input."""
    if kind == "list":
        if not isinstance(value, (list, tuple)) or not value:
            raise ValueError(f"{name} must be a non-empty list of numbers")
        for x in value:
            if isinstance(x, bool) or not isinstance(x, (int, float)):
                raise ValueError(f"{name} must contain only numbers")
        return list(value)
    if kind == "int":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"{name} must be an integer")
        return value
    # kind == "number"
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a number")
    return value


class MathProvider(Provider):
    """math.<formula>: 100 real named formulas. expand() == 100."""

    namespace = "math"

    def expand(self) -> int:
        return len(FORMULAS)

    def resolve(self, name: str) -> Tool | None:
        if not self._owns(name):
            return None
        local = self._local(name)
        spec = FORMULAS.get(local)
        if spec is None:
            return None

        def _math(args: dict, _local=local, _spec=spec) -> dict:
            try:
                if not isinstance(args, dict):
                    return {"error": "args must be an object"}
                kwargs = {}
                for arg_name, kind, _desc in _spec["args"]:
                    if arg_name in args:
                        kwargs[arg_name] = _coerce(arg_name, kind, args[arg_name])
                    elif arg_name in _spec["defaults"]:
                        kwargs[arg_name] = _spec["defaults"][arg_name]
                    else:
                        return {"error": f"missing required arg: {arg_name}"}
                try:
                    if _spec["validate"] is not None:
                        err = _spec["validate"](kwargs)
                        if err:
                            return {"error": err}
                    result = _spec["compute"](**kwargs)
                except ValueError as e:
                    return {"error": str(e)}
                if isinstance(result, dict):
                    out = {k: _sig12(v) for k, v in result.items()}
                else:
                    out = _sig12(result)
                return {"formula": _local, "inputs": kwargs, "result": out}
            except Exception as e:  # never raise
                return {"error": f"{type(e).__name__}: {e}"}

        schema = {n: k for n, k, _d in spec["args"]}
        return Tool(
            name=name,
            description=f"[{spec['category']}] {spec['description']}",
            schema=schema,
            handler=_math,
            risk="low",
        )

    def sample_names(self, n: int = 5) -> list[str]:
        picks = ["circle_area", "compound_interest", "kinetic_energy",
                 "mean", "quadratic_roots"]
        return [self._dotted(p) for p in picks[: max(0, n)]]
