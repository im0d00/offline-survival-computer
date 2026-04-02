"""
Practical survival helper functions used by the Offline Survival Computer.
The calculations are intentionally simple and conservative so they are easy
to reason about and safe for offline use.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List


@dataclass
class WaterPlan:
    instructions: str
    treatment_amount: str
    wait_time_minutes: int

    def to_dict(self) -> Dict[str, object]:
        return {
            "instructions": self.instructions,
            "treatment_amount": self.treatment_amount,
            "wait_time_minutes": self.wait_time_minutes,
        }


def plan_water_treatment(
    liters: float, method: str = "boil", altitude_m: float = 0
) -> Dict[str, object]:
    liters = max(liters, 0.25)
    method = (method or "boil").lower()
    altitude_m = max(0.0, altitude_m)

    if method == "boil":
        wait = 1 if altitude_m < 2000 else 3
        plan = WaterPlan(
            instructions=(
                "Bring water to a rolling boil. Boil for 1 minute "
                "(3 minutes above 2000 m / 6500 ft). Let it cool covered."
            ),
            treatment_amount=f"{liters:.1f} L boiled",
            wait_time_minutes=wait,
        )
    else:
        drops = max(2, int(liters * 2))
        plan = WaterPlan(
            instructions="Add chlorine/iodine, shake for 30 seconds, wait before drinking.",
            treatment_amount=f"{drops} drops disinfectant",
            wait_time_minutes=30,
        )
    return plan.to_dict()


def plan_calories(weight_kg: float, activity_level: str = "moderate") -> Dict[str, object]:
    weight_kg = max(30.0, weight_kg)
    level = (activity_level or "moderate").lower()
    multiplier = {"low": 28, "moderate": 32, "high": 38}.get(level, 32)
    total_calories = int(weight_kg * multiplier)

    protein_g = int(weight_kg * 1.2)
    fat_g = int(total_calories * 0.25 / 9)
    carbs_g = int((total_calories - (protein_g * 4) - (fat_g * 9)) / 4)

    return {
        "total_calories": total_calories,
        "protein_g": protein_g,
        "carbs_g": max(carbs_g, 0),
        "fat_g": fat_g,
        "plan": "Distribute calories across 3 meals; prioritise protein and complex carbs.",
    }


def plan_medication_dose(medication: str, weight_kg: float, age: int) -> Dict[str, object]:
    med = (medication or "").strip().lower()
    weight_kg = max(5.0, weight_kg)
    age = max(0, age)

    profiles = {
        "ibuprofen": {"mg_per_kg": 10, "frequency": "every 6-8h", "max_daily": 2400},
        "acetaminophen": {"mg_per_kg": 15, "frequency": "every 4-6h", "max_daily": 3000},
        "aspirin": {"mg_per_kg": 10, "frequency": "every 6h", "max_daily": 3000},
    }
    profile = profiles.get(med, {"mg_per_kg": 10, "frequency": "every 8h", "max_daily": 2000})

    dose_mg = int(weight_kg * profile["mg_per_kg"])
    warnings: List[str] = []
    if med == "aspirin" and age < 16:
        warnings.append("Avoid aspirin under age 16 due to Reye's syndrome risk.")
    if dose_mg > profile["max_daily"]:
        dose_mg = profile["max_daily"]

    return {
        "dose_mg": dose_mg,
        "frequency": profile["frequency"],
        "max_daily_mg": profile["max_daily"],
        "warnings": warnings,
    }


def plan_fire_starting(fuel: str = "wood", weather: str = "dry") -> Dict[str, object]:
    fuel = (fuel or "wood").lower()
    weather = (weather or "dry").lower()

    tinder_options = ["cotton + petroleum jelly", "dry grass", "birch bark"]
    if weather == "wet":
        tinder_options.append("alcohol swab")

    steps = [
        "Gather pencil, finger, and wrist-sized fuel; keep tinder dry.",
        "Build a small teepee with tinder and kindling.",
        "Use a spark/ferro rod or lighter at the base until tinder catches.",
        "Add fuel gradually, leaving airflow through the structure.",
    ]
    method = "teepee"
    if fuel == "charcoal":
        method = "charcoal chimney"
        steps.insert(0, "Fill chimney with charcoal and tinder underneath.")

    return {"method": method, "steps": steps, "tinder_options": tinder_options}
