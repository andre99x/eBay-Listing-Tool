EBAY_CONDITIONS = [
    {"id": 1000, "name": "Neu"},
    {"id": 1500, "name": "Neu: Sonstige"},
    {"id": 2000, "name": "Generalüberholt"},
    {"id": 2500, "name": "Refurbished"},
    {"id": 3000, "name": "Gebraucht"},
    {"id": 4000, "name": "Sehr gut"},
    {"id": 5000, "name": "Gut"},
    {"id": 6000, "name": "Akzeptabel"},
    {"id": 7000, "name": "Defekt"},
]


def normalize_condition(condition_id: str | None, condition_name: str | None):
    condition = None
    if condition_id:
        condition = next((c for c in EBAY_CONDITIONS if str(c["id"]) == str(condition_id)), None)
    if not condition and condition_name:
        condition = next((c for c in EBAY_CONDITIONS if c["name"].lower() == condition_name.lower()), None)

    if not condition:
        raise ValueError("Ungültiger Zustand. Bitte gültige eBay-Zustands-ID auswählen.")

    return str(condition["id"]), condition["name"]
