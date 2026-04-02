"""
Survival Tools - Calculators and guides for emergency situations
"""


def water_purification(volume_liters: float, method: str) -> dict:
    """
    Calculate water purification requirements.

    Args:
        volume_liters: Volume of water to purify in liters
        method: bleach, iodine, or boil

    Returns:
        Dictionary with instructions, treatment_amount, and wait_time_minutes
    """
    if method == "bleach":
        # 2 drops (0.1ml) of 5-6% household bleach per liter
        drops = volume_liters * 2
        ml = drops * 0.05  # Approximate: 1 drop ≈ 0.05ml
        return {
            "instructions": f"Add {drops:.0f} drops ({ml:.2f}ml) of household bleach (5-6% sodium hypochlorite) to {volume_liters}L of water. Stir well.",
            "treatment_amount": f"{drops:.0f} drops or {ml:.2f}ml bleach",
            "wait_time_minutes": 30
        }

    elif method == "iodine":
        # 5 drops of 2% iodine tincture per liter
        drops = volume_liters * 5
        ml = drops * 0.05
        return {
            "instructions": f"Add {drops:.0f} drops ({ml:.2f}ml) of 2% iodine tincture to {volume_liters}L of water. Shake well.",
            "treatment_amount": f"{drops:.0f} drops or {ml:.2f}ml iodine",
            "wait_time_minutes": 30
        }

    elif method == "boil":
        return {
            "instructions": f"Bring {volume_liters}L of water to a rolling boil for at least 1 minute (3 minutes at altitudes above 2000m). Let cool before drinking.",
            "treatment_amount": "N/A (heat treatment)",
            "wait_time_minutes": 1
        }

    else:
        return {
            "instructions": "Unknown purification method.",
            "treatment_amount": "N/A",
            "wait_time_minutes": 0
        }


def ration_estimator(people: int, days: int, activity: str) -> dict:
    """
    Estimate food ration requirements.

    Args:
        people: Number of people
        days: Number of days
        activity: rest, light, or heavy

    Returns:
        Dictionary with total_calories, per_day, and food quantities
    """
    # Base calories per person per day
    calories_per_day = {
        "rest": 2000,
        "light": 2500,
        "heavy": 3500
    }

    base_calories = calories_per_day.get(activity, 2500)
    total_calories = base_calories * people * days
    per_day_calories = base_calories * people

    # Rough food estimates (simplified survival rations)
    # Rice: ~130 cal/100g, Beans: ~120 cal/100g, Oil: ~900 cal/100ml
    rice_kg = (total_calories * 0.5) / 1300  # 50% from rice
    beans_kg = (total_calories * 0.3) / 1200  # 30% from beans
    oil_liters = (total_calories * 0.2) / 9000  # 20% from oil

    return {
        "total_calories": total_calories,
        "per_day": per_day_calories,
        "rice_kg": round(rice_kg, 2),
        "beans_kg": round(beans_kg, 2),
        "oil_liters": round(oil_liters, 2)
    }


def dosage_calculator(drug: str, weight_kg: float, age_years: float) -> dict:
    """
    Calculate weight-based medication dosage.

    Args:
        drug: ibuprofen, paracetamol, amoxicillin, or aspirin
        weight_kg: Patient weight in kg
        age_years: Patient age in years

    Returns:
        Dictionary with dose_mg, frequency, max_daily_mg, and warnings
    """
    warnings = []

    if drug == "ibuprofen":
        # Children: 5-10 mg/kg every 6-8 hours
        # Adults: 200-400mg every 4-6 hours, max 1200mg/day (OTC) or 3200mg/day (prescription)
        if age_years < 18:
            dose_mg = weight_kg * 7.5  # Middle of range
            max_daily = weight_kg * 40
            frequency = "Every 6-8 hours, max 4 doses/day"
        else:
            dose_mg = min(400, weight_kg * 5)  # Cap at 400mg
            max_daily = 1200
            frequency = "Every 4-6 hours with food"

        if age_years < 6:
            warnings.append("Consult a doctor for children under 6 years old")

    elif drug == "paracetamol":
        # Children: 10-15 mg/kg every 4-6 hours
        # Adults: 500-1000mg every 4-6 hours, max 4000mg/day
        if age_years < 18:
            dose_mg = weight_kg * 12.5
            max_daily = weight_kg * 75
            frequency = "Every 4-6 hours, max 5 doses/day"
        else:
            dose_mg = min(1000, weight_kg * 15)
            max_daily = 4000
            frequency = "Every 4-6 hours"

        warnings.append("Risk of liver damage if exceeding maximum daily dose")

    elif drug == "amoxicillin":
        # Children: 20-40 mg/kg/day divided into 3 doses
        # Adults: 250-500mg every 8 hours
        if age_years < 18:
            dose_mg = (weight_kg * 30) / 3  # Per dose (3x daily)
            max_daily = weight_kg * 40
            frequency = "Every 8 hours (3 times daily)"
        else:
            dose_mg = 500
            max_daily = 1500
            frequency = "Every 8 hours (3 times daily)"

        warnings.append("Complete full course even if symptoms improve")
        warnings.append("Avoid if allergic to penicillin")

    elif drug == "aspirin":
        # Adults only: 300-900mg every 4-6 hours
        dose_mg = 500
        max_daily = 4000
        frequency = "Every 4-6 hours with food"

        if age_years < 16:
            warnings.append("DO NOT GIVE TO CHILDREN UNDER 16 - Risk of Reye's syndrome")
            dose_mg = 0
            max_daily = 0

    else:
        return {
            "dose_mg": 0,
            "frequency": "Unknown drug",
            "max_daily_mg": 0,
            "warnings": ["Unknown medication"]
        }

    return {
        "dose_mg": round(dose_mg, 1),
        "frequency": frequency,
        "max_daily_mg": round(max_daily, 0),
        "warnings": warnings
    }


def fire_starting(conditions: str) -> dict:
    """
    Provide fire starting guidance based on conditions.

    Args:
        conditions: wet, dry, windy, or snow

    Returns:
        Dictionary with method, steps, and tinder_options
    """
    guides = {
        "wet": {
            "method": "Wet Weather Fire - Use Standing Dead Wood",
            "steps": [
                "Find standing dead trees (deadwood that hasn't fallen)",
                "Split wood to access dry interior - bark may be wet but inside is dry",
                "Create feather sticks by shaving thin curls from dry wood",
                "Build a raised platform with logs to keep fire off wet ground",
                "Start small with the driest material, gradually add larger pieces",
                "Use birch bark or resin-rich wood as natural fire starters"
            ],
            "tinder_options": [
                "Inner bark from standing dead trees",
                "Birch bark (even when wet)",
                "Resinous pine wood shavings",
                "Dry material from under logs or in tree hollows",
                "Cotton balls with petroleum jelly (if available)"
            ]
        },
        "dry": {
            "method": "Standard Fire Lay - Optimal Conditions",
            "steps": [
                "Clear area down to mineral soil in a 10ft diameter",
                "Gather tinder (fine, dry material), kindling (pencil-sized), and fuel wood",
                "Create a tinder bundle and place in center",
                "Build a teepee or log cabin structure with kindling around tinder",
                "Light tinder from upwind side",
                "Gradually add larger pieces as fire establishes",
                "Never leave fire unattended"
            ],
            "tinder_options": [
                "Dry grass and leaves",
                "Pine needles",
                "Shredded bark",
                "Paper or cardboard",
                "Wood shavings"
            ]
        },
        "windy": {
            "method": "Dakota Fire Hole - Wind Protected",
            "steps": [
                "Dig a pit 12 inches deep and 8-10 inches wide",
                "Dig a second hole upwind, 6-8 inches away, tunneling to connect at bottom",
                "The upwind tunnel provides oxygen while pit shields flames",
                "Place tinder at bottom of main pit",
                "Light fire - wind will be channeled through tunnel",
                "This design is also more efficient and less visible",
                "Completely extinguish and fill holes when done"
            ],
            "tinder_options": [
                "Fine dry grass",
                "Small twigs",
                "Commercial fire starters",
                "Char cloth",
                "Dryer lint"
            ]
        },
        "snow": {
            "method": "Snow/Winter Fire - Insulated Base",
            "steps": [
                "Create a solid base platform using green logs laid side-by-side",
                "Place fire on platform to prevent melting into snow",
                "Use a reflector wall (logs or rocks) behind fire to direct heat",
                "Position yourself between fire and reflector",
                "Keep abundant dry wood nearby - damp wood is harder to add later",
                "Build lean-to shelter overhead if possible for protection",
                "Keep fire smaller - easier to maintain in cold conditions"
            ],
            "tinder_options": [
                "Birch bark (excellent in any weather)",
                "Resinous pine/spruce wood shavings",
                "Standing dead branches from evergreens",
                "Inner bark from dead branches",
                "Commercial fire starters or petroleum jelly cotton"
            ]
        }
    }

    result = guides.get(conditions, guides["dry"])
    return result
