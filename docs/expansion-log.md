# Expansion log — folk-magic species and traditional-use data

Running record of the work that turned this from a single-target arnica mapper
into a West Kootenays foraging app for plants and fungi used in folk magic.
Written as the work proceeds, not reconstructed afterwards, so the reasoning
and the evidence behind each profile are visible.

Two parts:

* **Part 1 — species profiles.** New `config/species/*.json` profiles for
  plants and fungi that are (a) documented in European, North American or
  Indigenous folk-magic, herbal or folklore traditions and (b) actually present
  in the West Kootenays / interior BC.
* **Part 2 — traditional-use data.** A `folk_magic` block on *every* profile
  (new and existing), surfaced in the map UI on the site popup.

---

## How regional presence was confirmed

Presence is not assumed from a range map. For each candidate, research-grade
iNaturalist observations were counted inside two extents, using the API
directly (exact-match taxon IDs, because the `taxon_name` parameter also
matches *common* names — a trap this codebase already documents):

* **AOI** — the shipped `config/aoi/west_kootenays.geojson` bbox
  (−118.05, 49.00) to (−117.25, 49.45), ~2,900 km².
* **Kootenays** — the 30,000 km² validation extent used by
  `scripts/validate.py` (−118.5, 49.0) to (−116.0, 50.5).

Counts (research grade, 2026-10):

| Candidate | Taxon ID | AOI | Kootenays | Verdict |
|---|---|---|---|---|
| *Oplopanax horridus* | 83914 | 21 | 290 | add |
| *Chamaenerion angustifolium* | 564969 | 131 | 625 | add |
| *Urtica dioica* (complex) | 1631210 | 13 | 67 | add |
| *Achillea millefolium* (complex) | 1105043 | 79 | 402 | add |
| *Verbascum thapsus* | 59029 | 111 | 403 | add |
| *Sambucus racemosa* | 57824 | 14 | 165 | add |
| *Sambucus cerulea* | 143799 | 52 | 188 | congener (down-weighted) |
| *Juniperus scopulorum* | 135883 | 76 | 151 | add |
| *Juniperus communis* | 58725 | 50 | 237 | congener (down-weighted) |
| *Rosa acicularis* | 132527 | 1 | 6 | add, genus query |
| *Rosa nutkana* / *R. woodsii* | 78883 / 78887 | 5 / 1 | 11 / 11 | congeners |
| *Artemisia ludoviciana* | 71127 | 15 | 54 | add |
| *Artemisia absinthium* | 60350 | 4 | 32 | congener (down-weighted) |
| *Hypericum perforatum* | 56077 | 43 | 150 | add |
| *Thuja plicata* | 48252 | 262 | 791 | add |
| *Pseudotsuga menziesii* | 48256 | 179 | 423 | add |
| *Betula papyrifera* | 49883 | 47 | 122 | add |
| *Alectoria sarmentosa* | 126626 | 12 | 63 | add |
| *Amanita muscaria* | 48715 | 43 | 93 | add |
| *Arnica latifolia* (existing) | 75573 | 6 | 53 | already shipped |

Two caveats, both stated because they bound what the numbers mean:

* iNaturalist counts are a **lower bound on presence and a biased measure of
  abundance** — they track where botanists walk, near roads and towns. A count
  of 1 does not mean rare; it means under-observed. The counts are used here
  only to confirm a species is *in the region at all*, which is the claim that
  matters.
* An **AOI count of zero is treated as absent** even if the wider range map
  includes the area. *Hypericum scouleri* (0 in the AOI, 98 across the
  Kootenays) is a genuine interior species that simply does not reach this
  valley system, so it was not added; *Artemisia frigida* and *A. vulgaris*
  (0 in the AOI) were dropped for the same reason.

## Candidates considered and deliberately not added

| Candidate | Why not |
|---|---|
| *Hypericum scouleri* (Scouler's St John's wort) | 0 AOI records — does not reach this valley system. |
| *Artemisia vulgaris* (common mugwort), *A. frigida* | 0 AOI records each. |
| *Artemisia tridentata* (big sagebrush) | 3 AOI records; the AOI sits at the wet northern edge of its range. Present but marginal, so excluded rather than modelled badly. |
| *Usnea* spp. (beard lichens) | Only 3 AOI records, and almost all are genus-level. *Alectoria sarmentosa* is the better-recorded "old man's beard" of these forests and carries the same folk association, so it stands in for the group. |
| *Pteridium aquilinum* (bracken) | Present and folkloric, but bracken's magic belongs to European *Pteridium* fern-seed lore while the local plant is a carcinogenic toxic; the safety note would dominate the entry. Left out. |
| *Arctostaphylos uva-ursi* (kinnikinnick) | Present and abundant (175 AOI) with real ceremonial use. Left out of this pass only to keep the set focused on the requested candidates; a good next addition. |

## Folk-magic associations: how they are framed

Every association in a profile carries a **theme** (protection, love,
prosperity, divination, …), a **note** describing the recorded use, and an
**origin** naming the tradition or literature it comes from. Three rules were
applied throughout:

1. **Separate European from Indigenous North American records.** A plant can
   carry both, and conflating them misattributes one tradition's practice to
   another. Each is labelled where it is known.
2. **Say when the record is thin.** Fireweed's magical documentation is
   genuinely weak next to its food and medicine use; the profile says so rather
   than inventing an association to fill the field.
3. **No medical or magical instruction.** The blocks are documentary and
   historical. Every one carries a safety note (toxicity, drug interactions, or
   an explicit "none known"), and the UI frames the whole panel as folklore,
   not advice.

Recurring sources for the associations, at the level of the tradition rather
than a single page citation:

* **Pacific Northwest Coast and interior Indigenous ethnobotany** — Nancy
  Turner's work on Haida, Tlingit, Coast Salish and interior BC plant use;
  Erna Gunther's *Ethnobotany of Western Washington*; Daniel Moerman's
  *Native American Ethnobotany* (a compilation of the ethnographic record).
* **European folk magic and herbalism** — Nicholas Culpeper's *Complete
  Herbal*, John Gerard's *Herball*, Paul Huson's *Mastering Herbalism* and
  Scott Cunningham's *Encyclopedia of Magical Herbs* for the modern
  folk-magic compilations of older practice, and the standard folklore record
  for plants like elder, juniper, mugwort and St John's wort.
* **Local/regional floras** for habitat and elevation bands — E-Flora BC and
  the BC Ministry of Forests' biogeoclimatic zone descriptions (ICH, IDF, ESSF,
  MS as used in the profiles).

## Habitat models used

| Model | Signal | Used for |
|---|---|---|
| `spectral` (existing) | Sentinel-2 NDVI/NDMI canopy continuum | meadow, dry open and disturbed-ground plants |
| `host_trees` (existing) | BC VRI stand composition, age, crown closure | mycorrhizal fungi, and (generalised) target trees and epiphytic lichens |
| `moisture` (optional extra) | Distance to FWA streams/lakes/wetlands by stream order, blended with topographic position | moisture-obligate plants: devil's club, nettle, elderberry |

The `host_trees` model was written for the chanterelle but is not
mushroom-specific: it scores "how much of this stand is the thing the target
needs". For a tree target the affinity table simply says "this is the tree",
and for an epiphytic lichen it says "conifer canopy, old stand". Both reuse it
unchanged.

---

## The moisture component

Water proximity comes from BC's **Freshwater Atlas** (FWA): streams as lines
with a `STREAM_ORDER`, lakes/rivers/wetlands as polygons. Verified live against
the WFS before it was wired in — all four layers return data and the geometry
column is `GEOMETRY`. Three things about the data shaped the design:

* **Stream order matters.** Of 526 stream segments in an 8 km test box, 267
  were first-order and only 25 were fifth-order. A first-order gully is often
  dry by midsummer; a fourth-order mainstem is not. Profiles filter on
  `min_stream_order` for that reason.
* **Order 9 is not an order.** It marks FWA's *areal* representation of a major
  river — the Columbia appears this way. Noted because it looks like a data bug
  otherwise.
* **Wetlands count as habitat, not as water to exclude.** A skunk-cabbage swamp
  is prime devil's-club ground, so a profile lists wetlands among the features
  it credits rather than masking them out.

The second half of the component is **topographic position** — a cell's
elevation against the mean within a window around it. That is what catches a
seep or a toe slope with no blue line drawn on it, which is exactly how devil's
club grows. Neither half needs Sentinel-2, so a moisture species run is cheap.

### Why this is a component and not a third habitat model

This work first implemented water as a third `habitat_model` (`riparian`) that
*replaced* the vegetation or forest layer. The designated branch already had a
`moisture` component that *adds* to either, and on review the component is the
better design, so the riparian model was removed and its species re-targeted:

* **Water composes.** A moisture-obligate plant still has a stand or a canopy
  worth scoring — devil's club wants wet *cedar-hemlock forest*, not wet ground
  in general — and a replacement model throws that signal away.
* **It catches unmapped water.** Topographic position finds the seep; distance
  to a mapped stream alone cannot.
* **One mechanism, not two.** Two parallel implementations of "how far is the
  water" would have meant two FWA fetches and two places for the logic to drift.

The three water species therefore score on two signals each:

| Species | Habitat model | What the model finds | What moisture adds |
|---|---|---|---|
| Devil's club | `host_trees` | Cedar-led wet forest, as an *indicator* of forest type | The wet patch within it |
| Stinging nettle | `spectral` | Open, lush, disturbed ground | The damp part of it |
| Red elderberry | `spectral` | Open-to-semi-open edge and runout | The damp ground it needs |

Devil's club is the case the branch's `forest.host_label` exists for: the
affinity table names cedar, but cedar forms arbuscular associations and hosts
nothing, so the stand is being used as an indicator rather than as a host. The
label makes the UI say "cedar-rich forest" rather than "host-rich forest", and
the profile comment says the same thing for anyone reading the config.

A first real run is worth recording, because it says how much the layer
discriminates. The AOI returned 9,069 stream segments, 330 lakes, 59 river
polygons and 188 wetlands, and **79% of the AOI lies within 350 m of some
water** — this is a wet, deeply dissected landscape, not a dry one. So the
candidate pool is large: what separates the top sites is proximity *within* the
band, the topographic-position term, and the stand or canopy signal that goes
with it. The percentile-based site selection then takes the best 1%. The
practical consequence is that these maps are a *ranking* of damp ground, and
the top pins are the floodplain, bench and toe-slope sites rather than the bank
of the nearest ditch.

## Species checklist

Each row is one commit. Habitat model and the folk-magic themes carried are
listed so the set's coverage is visible at a glance.

| Species | Common name | Model | Themes | Status |
|---|---|---|---|---|
| *Oplopanax horridus* | devil's club | host_trees + moisture | protection, spirit-work, healing, luck | added |
| *Urtica dioica* | stinging nettle | spectral + moisture | protection, banishing, weather, healing | added |
| *Sambucus racemosa* | red elderberry | spectral + moisture | protection, banishing, death, prosperity | added |
| *Chamaenerion angustifolium* | fireweed | spectral | healing | added |
| *Achillea millefolium* | yarrow | spectral | divination, love, protection, courage | added |
| *Verbascum thapsus* | mullein | spectral | protection, banishing, divination | added |
| *Juniperus scopulorum* | Rocky Mountain juniper | spectral | purification, protection, banishing | added |
| *Artemisia ludoviciana* | western mugwort | spectral | purification, dreams, protection | added |
| *Hypericum perforatum* | St John's wort | spectral | banishing, protection, divination | added |
| *Rosa acicularis* | prickly rose | spectral | love, protection, healing | added |
| *Thuja plicata* | western redcedar | host_trees | purification, protection, spirit-work | added |
| *Pseudotsuga menziesii* | Douglas-fir | host_trees | protection, purification, healing | added |
| *Betula papyrifera* | paper birch | host_trees | protection, purification, spirit-work | added |
| *Alectoria sarmentosa* | old man's beard | host_trees | protection, healing | added |
| *Amanita muscaria* | fly agaric | host_trees | spirit-work, divination, luck | added |

## The folk-magic block (Part 2)

Every profile — the two that existed and all fifteen new ones — carries a
`folk_magic` block:

```json
"folk_magic": {
  "folk_names": ["..."],
  "traditions": ["..."],
  "associations": [
    { "theme": "protection", "note": "what it was used for", "origin": "where that is recorded" }
  ],
  "safety": { "level": "caution", "note": "toxicity and interactions" },
  "sources": ["..."]
}
```

Design decisions worth recording:

* **`origin` is required on every association.** This is the schema's main
  editorial job. The plants in this set sit between two traditions that are
  easy to blend — European folk magic and Indigenous North American practice —
  and blending them misattributes one to the other. Requiring a stated origin
  forces the distinction to be made in the data, and a test fails any profile
  that omits it.
* **The theme vocabulary is closed** (`src/foraging/folk_magic.py`): protection,
  warding, banishing, purification, love, fertility, prosperity, luck,
  divination, dreams, courage, healing, spirit-work, death, weather, hunting.
  An invented theme is a typo or an editorial slip, and validation rejects it
  rather than letting an unmapped chip render blank.
* **Safety levels are ordered and load-bearing**: `none`, `caution`, `toxic`,
  `restricted`. They are also CSS classes, so the level has to be one of the
  four. The `restricted` level is used once, for the fly agaric.
* **Validation runs in `load_config`**, so a bad block fails at startup with the
  species named rather than producing an empty panel after a full pipeline run.
* **The block travels once per species in the manifest**, not once per site in
  the GeoJSON: it is species-level information and duplicating it onto 400 pins
  would bloat the file for nothing.
* **The UI frames it as folklore in both places it appears** — the site popup
  and the sidebar species panel — using a disclaimer string that ships with the
  data rather than being written into the JavaScript, so the framing cannot
  drift between the map and the docs.

## Progress log

| # | Step | Status |
|---|---|---|
| 1 | Plan, presence research, this log | done |
| 2 | `folk_magic` schema + validation + manifest export + UI panel | done |
| 3 | Water signal: moisture component (merged from the branch; the `riparian` model this work first wrote was removed in favour of it) | done |
| 4 | New species profiles, one commit each | done (15) |
| 5 | README + docs for the new models and data | done |

See the commit history for the per-species steps; each commit runs the suite.

## What shipped

* **15 new species profiles** on top of the two that existed, 17 in total, each
  with a habitat model, a folk-magic block and a safety note.
* **The moisture component** for the water species, merged from the branch and
  consolidated on: this work's parallel `riparian` model was removed so there is
  one way to score water, not two. The new species also generalised
  `host_trees`, which now serves trees and lichens as well as fungi.
* **The `folk_magic` schema**, validated at config load, carried in the
  manifest, and rendered on the site popup and in a sidebar panel.
* **389 tests**, up from 82: the new suites cover the folk-magic schema, the
  moisture component, and a structural check of every profile.

Verified beyond the suite:

* The **full pipeline ran end to end** for devil's club, the species that
  exercises the most machinery (host-tree indicator + moisture): 23,722 VRI
  stands, 7.8% of the AOI mapped as water, 215 ranked sites, 11 raster overlays
  including the moisture layer, and a manifest carrying the folk-magic block.
  The top site is a 148-year-old cedar-rich stand 0 m from water - which is
  what the model is supposed to find.
* The **browser check caught two more real bugs**, both now fixed and
  re-measured: the popup had grown past 1000 px with its top off the map, and
  the stand-share row read "Host share" while the layer toggle read "Cedar
  share", because the label was not travelling in the manifest.
* The **map UI was driven in a real browser** against that build: the sidebar
  panel and the popup both render the folklore content, and the moisture layer
  appears in the toggle list. That check found a genuine bug - the popup had
  grown past 1000 px and its top ran off the map - which is fixed and
  re-measured.
* The **original spectral path was re-run** for arnica: 188 sites and 14
  flagged, matching the figures already in the README, so the manifest and
  dispatch changes did not move the original target.

## Honest limits

* **Only two new profiles carry a `validation` block** (juniper and elderberry,
  both with a congener that shares the collectors but not the habitat). The
  rest have no defensible contrast taxon in this region, and
  `scripts/validate.py` reports "nothing to validate against" rather than
  inventing a test. The new profiles are expert priors in the same sense the
  chanterelle's were.
* **The folk-magic associations are documentary, not exhaustive.** Each profile
  carries the associations the named traditions support, with the origin
  stated; a plant used in a dozen regional traditions has a dozen more entries
  than one profile can hold. Where the record is thin, the profile says so
  instead of padding it.
* **Nothing here is a field guide.** The safety notes are hazard summaries for
  someone reading the folklore, not identification or dosage, and several of
  these plants are genuinely dangerous to confuse with something else.
