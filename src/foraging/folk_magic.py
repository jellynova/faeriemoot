"""Folk-magic and traditional-use metadata carried by species profiles.

This module holds the *schema* for the ``folk_magic`` block that every species
profile now carries, plus the validation that keeps those blocks honest and
machine-readable.

What the block is
-----------------
Documentary, historical and cultural information about how a plant or fungus
has been used in folk magic, herbalism and folklore — protective charms,
love divination, prosperity workings, purification, banishing, spirit-work and
so on — with a note on the tradition each association is recorded in, and a
plain safety note.

What it is **not**
------------------
Not medical advice, not a safety guarantee, not an instruction to use anything,
and not a claim that any of it works. The UI is required to frame it as
folklore; see ``web/app.js``. The safety note is a hazard summary for someone
who reads the folklore and wonders whether the plant is dangerous — nothing
more.

Design notes
------------
* ``THEMES`` is a closed vocabulary. A profile that invents a theme is a typo
  or an editorial slip, and validation rejects it rather than letting an
  unmapped chip render blank in the UI.
* ``SAFETY_LEVELS`` is ordered from harmless to dangerous and is used directly
  as a CSS class, so the level must be one of the four slugs.
* ``origin`` is required on every association. An association with no stated
  tradition is folklore the project cannot source, and conflating European
  folk practice with Indigenous North American practice is a real
  misattribution risk — naming the origin is what keeps them apart.
"""

from __future__ import annotations

from typing import Any

# Closed vocabulary of association themes. Ordered for display; the UI shows
# chips in profile order, but tests and docs read this list.
THEMES = (
    "protection",
    "warding",
    "banishing",
    "purification",
    "love",
    "fertility",
    "prosperity",
    "luck",
    "divination",
    "dreams",
    "courage",
    "healing",
    "spirit-work",
    "death",
    "weather",
    "hunting",
)

THEME_LABELS = {
    "protection": "protection",
    "warding": "warding",
    "banishing": "banishing",
    "purification": "purification",
    "love": "love",
    "fertility": "fertility",
    "prosperity": "prosperity",
    "luck": "luck",
    "divination": "divination",
    "dreams": "dreams",
    "courage": "courage",
    "healing": "healing",
    "spirit-work": "spirit work",
    "death": "death & mourning",
    "weather": "weather",
    "hunting": "hunting",
}

# Harmless -> dangerous. These slugs are also CSS class suffixes in the UI
# (.safety-none, .safety-caution, ...), so they are part of the interface.
SAFETY_LEVELS = ("none", "caution", "toxic", "restricted")

SAFETY_LABELS = {
    "none": "No known hazard",
    "caution": "Caution",
    "toxic": "Toxic",
    "restricted": "Do not use",
}

BLOCK_KEYS = {"folk_names", "traditions", "associations", "safety", "sources"}
ASSOCIATION_KEYS = {"theme", "note", "origin"}


def _nonempty(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def problems(profile: dict) -> list[str]:
    """Every schema violation in a profile's ``folk_magic`` block.

    Returns a list of human-readable problems rather than raising, so a test
    can report all of them for a profile at once.
    """
    species = profile.get("id") or profile.get("scientific_name") or "<unnamed>"
    block = profile.get("folk_magic")
    if block is None:
        return [f"{species}: no folk_magic block (every profile carries one)"]
    if not isinstance(block, dict):
        return [f"{species}: folk_magic must be an object"]

    out: list[str] = []
    # `$`-prefixed keys are the config files' comment convention and are
    # stripped by load_json; tolerate them here too, so a profile can be
    # validated straight from disk without pre-processing.
    unknown = {k for k in block if not k.startswith("$")} - BLOCK_KEYS
    if unknown:
        out.append(f"{species}: unknown folk_magic key(s): {', '.join(sorted(unknown))}")

    names = block.get("folk_names")
    if not isinstance(names, list) or not names or not all(_nonempty(n) for n in names):
        out.append(f"{species}: folk_names must be a non-empty list of strings")

    traditions = block.get("traditions", [])
    if not isinstance(traditions, list) or not all(_nonempty(t) for t in traditions):
        out.append(f"{species}: traditions must be a list of strings")

    assocs = block.get("associations")
    if not isinstance(assocs, list) or not assocs:
        out.append(f"{species}: associations must be a non-empty list")
    else:
        for i, a in enumerate(assocs):
            where = f"{species}: associations[{i}]"
            if not isinstance(a, dict):
                out.append(f"{where} must be an object")
                continue
            bad = set(a) - ASSOCIATION_KEYS
            if bad:
                out.append(f"{where} has unknown key(s): {', '.join(sorted(bad))}")
            theme = a.get("theme")
            if theme not in THEMES:
                out.append(f"{where} theme {theme!r} is not in the theme vocabulary")
            for field in ("note", "origin"):
                if not _nonempty(a.get(field)):
                    out.append(f"{where} needs a non-empty {field}")

    safety = block.get("safety")
    if not isinstance(safety, dict):
        out.append(f"{species}: safety must be an object with level and note")
    else:
        extra = set(safety) - {"level", "note"}
        if extra:
            out.append(f"{species}: unknown safety key(s): {', '.join(sorted(extra))}")
        if safety.get("level") not in SAFETY_LEVELS:
            out.append(f"{species}: safety.level must be one of {', '.join(SAFETY_LEVELS)}")
        if not _nonempty(safety.get("note")):
            out.append(f"{species}: safety.note must be a non-empty string")

    sources = block.get("sources", [])
    if not isinstance(sources, list) or not all(_nonempty(s) for s in sources):
        out.append(f"{species}: sources must be a list of strings")

    return out


def validate(profile: dict) -> None:
    """Raise ``ValueError`` on the first profile with a bad block."""
    found = problems(profile)
    if found:
        raise ValueError("invalid folk_magic block:\n  " + "\n  ".join(found))


def for_manifest(profile: dict) -> dict | None:
    """The block as the web UI consumes it, with display labels resolved.

    Returns ``None`` when the profile carries no block, so the UI can simply
    omit the panel rather than render an empty one.
    """
    block = profile.get("folk_magic")
    if not block:
        return None
    safety = block.get("safety") or {}
    level = safety.get("level") if safety.get("level") in SAFETY_LEVELS else "caution"
    return {
        "folk_names": list(block.get("folk_names", [])),
        "traditions": list(block.get("traditions", [])),
        "associations": [
            {
                "theme": a["theme"],
                "theme_label": THEME_LABELS.get(a["theme"], a["theme"]),
                "note": a["note"],
                "origin": a["origin"],
            }
            for a in block.get("associations", [])
        ],
        "safety": {
            "level": level,
            "level_label": SAFETY_LABELS[level],
            "note": safety.get("note", ""),
        },
        "sources": list(block.get("sources", [])),
        # Rendered verbatim by the UI above the panel. Kept server-side so the
        # framing cannot drift between the map and the docs.
        "disclaimer": (
            "Historical and cultural information about folklore, recorded from the "
            "traditions named. Not medical or magical advice, and not a substitute "
            "for expert identification before you touch or eat anything."
        ),
    }
