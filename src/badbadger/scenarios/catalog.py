"""Server-owned chapter catalog; clients may select keys, never file paths."""

CHAPTERS = {
    "relay_crisis": {"scenario_id": "relay-crisis-0.1", "file": "relay_crisis.json",
                     "title": "Relay Crisis", "tagline": "A station in trouble. Three conflicting plans. Your next move."},
    "blackbarrow": {"scenario_id": "blackbarrow-north-road-0.1", "file": "blackbarrow_north_road.json",
                   "title": "Blackbarrow: The North Road", "tagline": "A waiting convoy. A damaged road. Three companions who already trust one another."},
}


def chapter(key: str) -> dict:
    if not isinstance(key, str) or key not in CHAPTERS:
        raise ValueError("Unknown chapter")
    return CHAPTERS[key]


def chapter_key(scenario_id: str) -> str:
    for key, value in CHAPTERS.items():
        if value["scenario_id"] == scenario_id:
            return key
    raise ValueError("This service requires a supported chapter save")
