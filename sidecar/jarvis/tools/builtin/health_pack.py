"""Health Tool Pack (Phase 10): BMI / BMR / TDEE calculators + workout plan.

All real math (WHO BMI bands, Mifflin-St Jeor BMR, standard activity
multipliers). workout_plan is a structured template built from exercise
lists defined in this file, honestly labeled as a template — not medical
advice. Low risk, offline (needs_network=False).

Not a medical device: results are estimates for general fitness planning.
"""
from __future__ import annotations


# ------------------------------------------------------------ helpers
def _fail(tool: str, e: Exception) -> dict:
    return {"error": f"{tool} failed: {type(e).__name__}: {e}"}


def _pos(value, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be a number")
    v = float(value)
    if v <= 0:
        raise ValueError(f"{label} must be > 0")
    return v


# ------------------------------------------------------- health.bmi
_WHO_BANDS = [
    (18.5, "underweight"),
    (25.0, "healthy weight"),
    (30.0, "overweight"),
]


def health_bmi(args: dict) -> dict:
    """BMI from weight (kg) and height (cm), with WHO category."""
    try:
        weight = _pos(args["weight_kg"], "weight_kg")
        height_cm = _pos(args["height_cm"], "height_cm")
        height_m = height_cm / 100.0
        bmi = weight / (height_m ** 2)
        category = "obese"
        for cutoff, label in _WHO_BANDS:
            if bmi < cutoff:
                category = label
                break
        return {"weight_kg": weight, "height_cm": height_cm,
                "bmi": round(bmi, 2), "who_category": category,
                "note": "WHO adult bands; not valid for children, pregnancy, "
                        "or very muscular builds"}
    except KeyError as e:
        return {"error": f"missing required arg: {e}"}
    except ValueError as e:
        return {"error": str(e)}
    except Exception as e:
        return _fail("health.bmi", e)


# ------------------------------------------------------- health.bmr
def _bmr_mifflin(weight: float, height_cm: float, age: float,
                 sex: str) -> float:
    s = {"male": 5, "female": -161}[sex]
    return 10 * weight + 6.25 * height_cm - 5 * age + s


def health_bmr(args: dict) -> dict:
    """Basal metabolic rate via Mifflin-St Jeor."""
    try:
        weight = _pos(args["weight_kg"], "weight_kg")
        height_cm = _pos(args["height_cm"], "height_cm")
        age = _pos(args["age"], "age")
        if age > 120:
            return {"error": "age looks unrealistic (>120)"}
        sex = str(args.get("sex", "")).strip().lower()
        if sex not in ("male", "female"):
            return {"error": "sex must be 'male' or 'female'"}
        bmr = _bmr_mifflin(weight, height_cm, age, sex)
        return {"weight_kg": weight, "height_cm": height_cm, "age": age,
                "sex": sex, "bmr_kcal_per_day": round(bmr, 2),
                "method": "Mifflin-St Jeor"}
    except KeyError as e:
        return {"error": f"missing required arg: {e}"}
    except ValueError as e:
        return {"error": str(e)}
    except Exception as e:
        return _fail("health.bmr", e)


# ------------------------------------------------------ health.tdee
_ACTIVITY = {
    "sedentary": 1.2,
    "light": 1.375,
    "moderate": 1.55,
    "active": 1.725,
    "athlete": 1.9,
}


def health_tdee(args: dict) -> dict:
    """Total daily energy expenditure = BMR x activity factor."""
    try:
        bmr = args.get("bmr")
        if bmr is None:
            weight = _pos(args["weight_kg"], "weight_kg")
            height_cm = _pos(args["height_cm"], "height_cm")
            age = _pos(args["age"], "age")
            sex = str(args.get("sex", "")).strip().lower()
            if sex not in ("male", "female"):
                return {"error": "sex must be 'male' or 'female'"}
            bmr_val = _bmr_mifflin(weight, height_cm, age, sex)
        else:
            bmr_val = _pos(bmr, "bmr")
        level = str(args.get("activity_level", "moderate")).strip().lower()
        if level not in _ACTIVITY:
            return {"error": f"activity_level must be one of: "
                             f"{', '.join(sorted(_ACTIVITY))}"}
        factor = _ACTIVITY[level]
        return {"bmr_kcal_per_day": round(bmr_val, 2),
                "activity_level": level, "activity_factor": factor,
                "tdee_kcal_per_day": round(bmr_val * factor, 2)}
    except ValueError as e:
        return {"error": str(e)}
    except Exception as e:
        return _fail("health.tdee", e)


# ------------------------------------------------ health.workout_plan
_EXERCISES = {
    "strength": ["barbell squat", "deadlift", "bench press", "overhead press",
                 "barbell row", "pull-up / lat pulldown"],
    "hypertrophy": ["dumbbell bench press", "incline press", "leg press",
                    "Romanian deadlift", "cable row", "lateral raise",
                    "biceps curl", "triceps pushdown", "leg curl",
                    "calf raise"],
    "fat_loss": ["kettlebell swing", "goblet squat", "push-up",
                 "dumbbell thruster", "row machine intervals",
                 "mountain climbers", "farmer's carry", "burpees"],
    "endurance": ["running intervals", "cycling", "rowing", "jump rope",
                  "bodyweight circuit", "swimming", "stair climbs"],
}

_SETS_REPS = {
    "strength": ("4-5 sets", "4-6 reps", "2-3 min rest"),
    "hypertrophy": ("3-4 sets", "8-12 reps", "60-90 s rest"),
    "fat_loss": ("3 rounds", "40 s work / 20 s rest", "1 min between rounds"),
    "endurance": ("steady / intervals", "20-40 min", "conversational pace"),
}


def health_workout_plan(args: dict) -> dict:
    """Structured weekly workout template from built-in exercise lists."""
    try:
        goal = str(args.get("goal", "hypertrophy")).strip().lower()
        if goal not in _EXERCISES:
            return {"error": f"goal must be one of: "
                             f"{', '.join(sorted(_EXERCISES))}"}
        level = str(args.get("level", "beginner")).strip().lower()
        if level not in ("beginner", "intermediate", "advanced"):
            return {"error": "level must be beginner, intermediate, or advanced"}
        try:
            days = int(args.get("days_per_week", 3))
        except (TypeError, ValueError):
            return {"error": "days_per_week must be an integer"}
        if days < 1 or days > 7:
            return {"error": "days_per_week must be between 1 and 7"}
        pool = _EXERCISES[goal]
        sets, reps, rest = _SETS_REPS[goal]
        # Simple rotation: split the pool across the week so each day gets
        # 3-5 distinct exercises.
        per_day = 4 if level != "beginner" else 3
        schedule = []
        for d in range(1, days + 1):
            start = ((d - 1) * per_day) % len(pool)
            day_ex = [pool[(start + k) % len(pool)] for k in range(per_day)]
            schedule.append({"day": d, "exercises": [
                {"name": name, "sets": sets, "reps": reps, "rest": rest}
                for name in day_ex]})
        warmup = "5-10 min: brisk walk/jog + dynamic stretches"
        cooldown = "5 min: easy walk + static stretches"
        return {"goal": goal, "level": level, "days_per_week": days,
                "schedule": schedule, "warmup": warmup, "cooldown": cooldown,
                "progression": ("add a little weight or one rep when all "
                                "sets feel easy"),
                "honest_note": "TEMPLATE — general fitness planning, not "
                               "medical advice. Stop if anything hurts; "
                               "check with a professional for injuries or "
                               "health conditions."}
    except Exception as e:
        return _fail("health.workout_plan", e)


# ------------------------------------------------- registry wiring metadata
TOOL_DEFS = [
    {"name": "health.bmi",
     "description": "BMI from weight_kg + height_cm with WHO category.",
     "handler": health_bmi, "risk": "low", "needs_network": False,
     "schema": {"weight_kg": "number", "height_cm": "number"}},
    {"name": "health.bmr",
     "description": "Basal metabolic rate via Mifflin-St Jeor.",
     "handler": health_bmr, "risk": "low", "needs_network": False,
     "schema": {"weight_kg": "number", "height_cm": "number", "age": "number",
                "sex": "string"}},
    {"name": "health.tdee",
     "description": "Total daily energy expenditure (BMR x activity factor).",
     "handler": health_tdee, "risk": "low", "needs_network": False,
     "schema": {"bmr": "number?", "weight_kg": "number?",
                "height_cm": "number?", "age": "number?", "sex": "string?",
                "activity_level": "string?"}},
    {"name": "health.workout_plan",
     "description": "Weekly workout template from built-in exercise lists "
                    "(labeled template, not medical advice).",
     "handler": health_workout_plan, "risk": "low", "needs_network": False,
     "schema": {"goal": "string?", "level": "string?",
                "days_per_week": "int?"}},
]

# (risk, needs_network) additions for agent/policy.py's RISK_TABLE —
# without these, the policy engine default-denies unknown tools.
# Parent wiring:  from jarvis.agent.policy import RISK_TABLE
#                 RISK_TABLE.update(health_pack.RISK_TABLE_ADDITIONS)
RISK_TABLE_ADDITIONS = {
    "health.bmi": ("low", False),
    "health.bmr": ("low", False),
    "health.tdee": ("low", False),
    "health.workout_plan": ("low", False),
}


def register(reg) -> None:
    """Wire the four health tools into a Registry."""
    from ..base import Tool
    for spec in TOOL_DEFS:
        reg.register(Tool(name=spec["name"], description=spec["description"],
                          schema=spec["schema"], handler=spec["handler"],
                          risk=spec["risk"], needs_network=spec["needs_network"]))


__all__ = ["TOOL_DEFS", "RISK_TABLE_ADDITIONS", "register",
           "health_bmi", "health_bmr", "health_tdee", "health_workout_plan"]
