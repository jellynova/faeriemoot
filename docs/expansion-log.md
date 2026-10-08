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
| `riparian` (new) | Distance to FWA streams/wetlands/lakes, weighted by stream order | moisture-obligate plants: devil's club, nettle, elderberry |

The `host_trees` model was written for the chanterelle but is not
mushroom-specific: it scores "how much of this stand is the thing the target
needs". For a tree target the affinity table simply says "this is the tree",
and for an epiphytic lichen it says "conifer canopy, old stand". Both reuse it
unchanged.

---

## The riparian model

Water proximity comes from BC's **Freshwater Atlas** (FWA): streams as lines
with a `STREAM_ORDER`, lakes/rivers/wetlands as polygons. Verified live against
the WFS before it was wired in — all four layers return data and the geometry
column is `GEOMETRY`.

Three things about the data shaped the design:

* **Stream order matters.** Of 526 stream segments in an 8 km test box, 267
  were first-order and only 25 were fifth-order. A first-order gully is often
  dry by midsummer; a fourth-order mainstem is not. Each cell therefore takes
  the credit of the stream it is *nearest to*, so a seep next door beats a
  mainstem across the valley.
* **Order 9 is not an order.** It marks FWA's *areal* representation of a major
  river — the Columbia appears this way — so the credit table clamps at the
  top rather than reading 9 as an unknown. This is documented in the stage
  because it looks like a bug otherwise.
* **Wetlands are habitat, not water to exclude.** A skunk-cabbage swamp is
  prime devil's-club ground, so only open lake/river water is masked out;
  wetlands score.

Slope comes from the DEM the terrain stage already built, so a riparian run
downloads no Sentinel-2 imagery at all — it is the cheapest habitat model of
the three.

## Progress log

| # | Step | Status |
|---|---|---|
| 1 | Plan, presence research, this log | done |
| 2 | `folk_magic` schema + validation + manifest export + UI panel | done |
| 3 | `riparian` habitat model (new stage) | done |
| 4 | New species profiles, one commit each | not started |
| 5 | README + docs for the new models and data | not started |

See the commit history for the per-species steps; each commit runs the suite.
