"""Small, deterministic light hits, composed before captions in the existing encode."""
from __future__ import annotations

import math

EFFECTS = {
    "none": {"label": "Original", "hint": "Keep the original picture."},
    "white": {"label": "White light", "hint": "Soft white hits at phrase changes."},
    "green": {"label": "Green light", "hint": "Green edge light at phrase changes."},
    "red": {"label": "Red light", "hint": "Red edge light at phrase changes."},
    "rgb": {"label": "Green / red / white", "hint": "Alternate three light colours."},
}


def light_filter(effect: str, intensity: float, words: list[dict], duration: float) -> str:
    if effect not in EFFECTS:
        raise ValueError("Unknown light effect.")
    if not math.isfinite(intensity) or not 0 <= intensity <= 1:
        raise ValueError("Light intensity must be between 0 and 1.")
    if effect == "none" or intensity == 0 or duration <= 0:
        return ""
    # Phrase starts, at least 2.5 seconds apart; no rapid strobing.
    starts = [0.0]
    previous = 0.0
    for word in words:
        at = float(word["start"])
        if at - starts[-1] >= 2.5 and (at - previous > 0.25 or at - starts[-1] >= 5):
            if at < duration - 0.3:
                starts.append(at)
        previous = float(word["end"])
        if len(starts) >= 24:
            break
    colours = ["0x62ef97", "0xff5b68", "white"] if effect == "rgb" else [{"green": "0x62ef97", "red": "0xff5b68", "white": "white"}[effect]]
    filters = []
    for i, at in enumerate(starts):
        colour = colours[i % len(colours)]
        enable = f"between(t,{at:.3f},{min(at + 0.2, duration):.3f})"
        filters.append(f"drawbox=x=0:y=0:w=iw:h=ih:color={colour}@{intensity * 0.22:.3f}:t=fill:enable='{enable}'")
        if colour != "white":
            filters.append(f"drawbox=x=0:y=0:w=iw:h=ih:color={colour}@{intensity * 0.32:.3f}:t=24:enable='{enable}'")
    return ",".join(filters)
