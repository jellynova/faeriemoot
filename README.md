# Foraging Suitability Mapper

Predicts and ranks likely wild-foraging sites by stacking terrain, vegetation,
access and observation data into a per-site suitability score, then serving the
ranked sites as clickable pins on an interactive map.

Seventeen species ship for the West Kootenays, BC — Rossland / Castlegar /
Salmo — all of them plants or fungi with a documented place in folk magic,
herbalism or folklore. Each is scored on whatever actually limits it:

* **Mountain arnica** (*Arnica latifolia*), a subalpine meadow plant — terrain
  and its Sentinel-2 vegetation signature.
* **Pacific golden chanterelle** (*Cantharellus formosus*), a fall-fruiting
  mycorrhizal mushroom — the **host-tree composition** of each forest stand
  from BC's Vegetation Resources Inventory.
* **Devil's club, stinging nettle, red elderberry** — moisture-obligate plants,
  scored on **distance to water** from BC's Freshwater Atlas.
* **Fireweed, yarrow, mullein, Rocky Mountain juniper, western mugwort, St
  John's wort, prickly rose** — open-ground plants, scored on the canopy
  continuum.
* **Western redcedar, Douglas-fir, paper birch**, the epiphytic **old man's
  beard** lichen, and the **fly agaric** — scored on stand composition, age and
  canopy closure.

Every profile also carries a **folk-magic block**: folk names, the traditions
the plant is documented in, the associations recorded for it (protection, love,
prosperity, divination, banishing and the rest), and a plain safety note. That
is historical and cultural information about folklore, shown on the map so you
know what you are looking at. It is not medical advice and not magical advice.

Nothing about the region is hard-coded: the AOI, the target species and the
scoring weights are all swappable config files.

> **This tool describes land tenure, not permission.** A site's land-status
> flag tells you what kind of ground it is, not whether you may harvest there.
> Confirm access and harvesting rules yourself. Snowpack and seasonal road
> closures are deliberately not modelled.

---

## Quick start

```bash
uv venv && uv pip install -e .        # or: pip install -e .
forage run                            # arnica, full pipeline, ~2 minutes
forage run --species config/species/cantharellus_formosus.json   # chanterelle
forage run --species config/species/oplopanax_horridus.json      # devil's club
forage run --species config/species/achillea_millefolium.json    # yarrow
python -m http.server -d web 8000     # then open http://localhost:8000
```

No API keys, accounts or tokens are required. Every data source is public and
anonymous.

---

## What it does

Each 30 m cell in the area of interest is scored by four layers, then
high-scoring cells are clustered into discrete sites and ranked. The habitat
layer is one of two, chosen by the species profile's `habitat_model`; a profile
may also switch on the optional **moisture** component, which adds a fifth
layer rather than replacing one.

| Layer | What it contributes | Source |
|---|---|---|
| **Terrain** | Elevation band, slope, aspect (per-species preference) | Copernicus GLO-30 DEM via Planetary Computer |
| **Vegetation** (`spectral`) | Open meadow / open forest vs closed canopy or bare rock | Sentinel-2 L2A, seasonal composite |
| **Forest** (`host_trees`) | Share of the host — or target — trees in the stand, stand age, crown closure | BC Vegetation Resources Inventory (VRI), rank-1 layer |
| **Moisture** (optional, any model) | Distance to streams, lakes and wetlands, weighted by stream order, blended with topographic position (hollows and toe slopes) | BC Freshwater Atlas (FWA) + the DEM |
| **Access** | Drive minutes from your origin + least-cost hike from the road | BC Digital Road Atlas, forest tenure roads, OSM trails |
| **Observations** | Proximity boost from real sightings | iNaturalist research-grade records |
| **Land status** | Tenure flag — *flagged, never silently down-ranked* | BC parks, ParcelMap BC, forest tenure |

Drive and hike are reported separately, never collapsed into one number, so you
can judge the trade-off yourself.

### Pipeline stages

```
terrain → vegetation | forest → [moisture] → access → observations → landstatus → scoring → export
```

`vegetation` and `forest` are alternatives: each skips itself unless the
species profile selects it, so a chanterelle run never downloads Sentinel-2.

Any stage can be re-run on its own (`forage scoring`), reading what it needs
from `data/interim/<aoi>/`. Layers that do not depend on the target (DEM,
imagery composites, drive times, tenure, raw inventory attributes) are shared
there; everything species-specific lives under `data/interim/<aoi>/<species>/`,
so two targets on one AOI never overwrite each other and the second one reuses
the first one's downloads. Retuning weights and rebuilding the map takes a few
seconds and re-downloads nothing.

```bash
forage run --reuse-imagery      # skip the Sentinel-2 download, recompute the rest
forage run --only scoring,export
forage areas                    # list available AOI polygons
```

---

## Configuration

Everything tunable lives in `config/`.

| File | Controls |
|---|---|
| `config/pipeline.json` | AOI selection, grid resolution, **drive-time origin**, imagery window, site clustering |
| `config/species/*.json` | Habitat model, elevation band, slope/aspect preference, vegetation thresholds or host-tree table, iNaturalist taxon, per-species weight overrides, validation taxa, and the **folk-magic block** |
| `config/weights.json` | Layer weights, hard filters, which land classes are excluded vs flagged |

### Setting your origin

Drive times are measured from one configurable point. It is not baked in
anywhere else:

```json
"origin": { "name": "Rossland, BC", "lon": -117.7997, "lat": 49.0781 }
```

### Re-targeting at a new region

Drop a polygon into `config/aoi/` and pass it on the command line:

```bash
forage run --aoi config/aoi/nelson.geojson
```

The analysis grid picks its own UTM zone from the AOI, every layer is fetched
for the new extent, and outputs land in a parallel `output/nelson/<species>/`
and `web/data/nelson/<species>/`. The map UI reads `web/data/index.json` and
offers a region-and-species switcher — no code changes. `config/aoi/nelson.geojson` ships as a worked
second example (80 sites); `forage areas` lists what is available.

The origin does **not** have to sit inside the AOI — the road fetch is extended
to cover the origin and the corridor to the area, so drive times from Rossland
to sites near Nelson route correctly. If the origin ends up far from any road,
the access stage says so rather than failing later.

### Adding a target species

Copy a species profile and pass it the same way:

```bash
forage run --species config/species/my_plant.json
```

First decide what the target's habitat signal actually is:

* **`"habitat_model": "spectral"`** (copy `arnica_latifolia.json`) when the
  target's habitat is visible from orbit — open meadow, a canopy gap, bare
  ground. The vegetation thresholds are the part most worth re-tuning; see the
  modelling notes below for how the current ones were calibrated.
* **`"habitat_model": "host_trees"`** (copy `cantharellus_formosus.json` for a
  fungus, `thuja_plicata.json` for a tree) for anything that tracks particular
  tree species — a mycorrhizal fungus, a target tree, or an epiphytic lichen.
  Edit the `forest.host_affinity` table (VRI species codes, matched by longest
  prefix, so `FD` covers `FDI` and `FDC`), the stand-age knots and the
  crown-closure band. For a tree target, the affinity table names the target
  itself and every other code falls to `default_affinity: 0.0`; set
  `habitat_label` and `forest_labels.share` so the popup says "Cedar share"
  rather than "Host share".
* **A `moisture` block** (copy `oplopanax_horridus.json`) for a plant whose
  constraint is water. This is *not* a habitat model — it is an extra score
  component that sits alongside whichever model the profile uses, because water
  composes with a vegetation or stand-composition signal rather than replacing
  it. Set the `water_distance_m` band, the `tpi` window and thresholds (a
  cell's elevation against its surroundings: negative is a hollow or toe slope,
  where water collects whether or not a stream is mapped there), the `blend`
  between them, and `min_stream_order` if only larger creeks should count.
  `hard_max_m` turns proximity into a requirement by rejecting cells further
  than that from water. The block and its weight must come together —
  `load_config` fails on one without the other rather than computing a layer
  that is then ignored.

A profile's `weights_override` replaces the `weights` and `terrain_subweights`
sections of `config/weights.json` for that species only. It cannot touch
access, legality or hard filters, because those layers are shared by every
species on the AOI.

Every profile must also carry a `folk_magic` block, and `load_config` rejects a
profile without one:

```json
"folk_magic": {
  "folk_names": ["yarrow", "milfoil", "soldier's woundwort"],
  "traditions": ["European folk magic", "Chinese tradition"],
  "associations": [
    { "theme": "divination", "note": "what the plant was used for ...",
      "origin": "which tradition and literature it is recorded in" }
  ],
  "safety": { "level": "caution", "note": "toxicity and drug interactions ..." },
  "sources": ["..."]
}
```

Themes come from a closed vocabulary in `src/foraging/folk_magic.py`, and
`origin` is required on every association — an unsourced association is how
European folk practice and Indigenous North American practice get conflated,
so the schema will not let one through. Safety levels are `none`, `caution`,
`toxic` and `restricted`.

---

## Notes on the modelling

A few decisions were driven by measurement rather than assumption, and are
worth knowing if you retune anything.

**Canopy closure comes from NDMI, not texture.** The plan was to use sub-pixel
NDVI texture to separate smooth herbaceous sward from broken canopy. Measured
against reference populations in this AOI, that signal runs *backwards* from
the usual assumption — open high ground is rougher at 10 m (0.035) than closed
canopy (0.020), because alpine is a rock/heath/krummholz mosaic while a closed
conifer canopy is uniform. It also separated weakly (AUC 0.71) against NDMI's
0.87. Texture is still computed and written as a diagnostic layer, and
`closure_texture_weight` folds it back in — correctly signed — if you want it.

**Open forest is not excluded.** *A. latifolia* grows in open subalpine forest
as well as treeless meadow, so vegetation is scored along a closure continuum
rather than cut into meadow/not-meadow. `openness_preference` in the species
profile sets the relative credit.

**Congener observations are down-weighted.** The AOI's *Arnica* records are
dominated by *A. cordifolia*, a forest-understory plant, not the subalpine
target. Weighting every record equally pulls the ranking downhill into low
forest, directly against the terrain filter. Cells with no nearby record fall
back to `no_observation_baseline` rather than zero — absence of a record is
absence of a botanist, not absence of the plant.

**Site selection is percentile-based.** An absolute score threshold does not
transfer between regions: at 0.45, 95% of scored cells here qualified, and
connected-component labelling fused an entire massif into one 25,000 ha
"site". Sites are now taken from the top 1% of the local score distribution,
with oversized patches split at their local maxima.

**Vector fetches are tiled, not paged.** BC's WFS gives no stable ordering
without an explicit sort, and sorting the larger layers times the request out.
Paging unsorted returns overlapping pages *silently* — a Rossland-to-Nelson
road fetch came back with 5,725 duplicate rows out of 26,428, which means the
same number of features were never returned at all. The holes shattered the
routed network into disconnected fragments. Extents are therefore fetched by
recursive quadrant subdivision until each tile fits under the feature cap.

**Regenerating clearcuts are penalised, not celebrated.** A cutblock a decade
after harvest has exactly the signature this model selects for — open canopy,
high summer NDVI — and it sits on a logging road, so it scores well on access
too. Before this was handled, the top-ranked sites were hard-edged cutblocks
strung along logging roads, which only became obvious when the pins were
rendered over satellite imagery. BC's consolidated cutblock layer now supplies
years-since-harvest; recently logged ground is penalised on a fading curve, and
labelled *regenerating cutblock* rather than *open meadow*. Tune it with
`vegetation.logging` in the species profile. Note that harvest records do not
go back indefinitely, so old cuts may still pass as meadow.

**A mushroom is scored by its trees, not its reflectance.** The arnica model
reads the target's own habitat off Sentinel-2. That cannot transfer to a
mycorrhizal fungus: it is invisible at 10 m, and NDVI/NDMI cannot tell a
Douglas-fir stand from an equally green, equally closed cedar or spruce one —
which is the one distinction that matters, since *C. formosus* fruits only
where its host's roots are, and cedar cannot host it at all (it forms
arbuscular, not ectomycorrhizal, associations). The forest stage reads species
composition per stand from VRI instead:
`host credit × stand-age credit × crown-closure credit`, where host credit is
the percentage-weighted affinity of the stand's up-to-six species, saturating at
a 50% host share. Stand age is the younger of the inventory's projected age and
years since harvest from the cutblock layer, so a block cut after the inventory
was last updated still reads as freshly logged, and logging is handled through
the hosts being removed rather than through a separate penalty. Below a 15% host
share a cell is not habitat at all.

On the West Kootenays this puts 27% of the AOI in host-rich forest, almost all
of it Douglas-fir-led (164 of the 178 ranked sites). Worth knowing:

* **Every host-tree number is an expert prior, not a fit.** The host table
  follows Pilz et al. (2003, USDA PNW-GTR-576) for Douglas-fir and hemlock; the
  values for pine, spruce and true firs are guesses, flagged as such in the
  profile. There are no usable records to fit or test against (see Validation).
* **Most of the best ground is private.** Douglas-fir dominates the warm valley
  bottoms and lower slopes, which is where the towns and private land are: 95
  of the 178 chanterelle sites carry a land-status flag, against 14 of arnica's
  188 subalpine sites.
  Use the *hide flagged land* filter.
* **The top of the list ties.** With the observation layer flat (no usable
  records) and host credit saturating, many patches reach the same ceiling
  score; ties are broken by patch mean score, then area. Treat the top few dozen
  as equally good candidates, not a strict order.
* *C. formosus* is chiefly a coastal and Cascades species. In the interior,
  golden chanterelles also include *C. roseocanus*, which follows spruce and
  pine. The map says where the right trees are; field ID is still yours.

**iNaturalist is queried by taxon ID, not by name.** The API's `taxon_name`
parameter also matches *common* names, so it is not a taxonomic filter: a
"Cantharellus" query returned the false chanterelle (*Hygrophoropsis*, a
different order) and *Hygrocybe cantharellus*, and the "Arnica" query had been
returning five *Erigeron divergens* records, which the observation layer then
weighted as "other Arnica". Names are now resolved to an exact-match taxon ID
first.

**Obscured records are dropped.** Records of threatened taxa, or from observers
who set their geoprivacy to obscured, publish a point randomised within a cell
of roughly 20 × 15 km here. The stated `positional_accuracy` is the observer's
GPS figure and does not reflect this, so the accuracy filter alone does not
catch them. A proximity boost around an obscured point boosts a random spot.
Foragers obscure their finds: every golden-chanterelle record in the Kootenays
is obscured.

**Water is a component, not a model.** Devil's club, nettle and elderberry all
want ground beside water, and neither habitat model can see that: NDVI cannot
tell a streamside thicket from an equally green dry slope, and VRI describes the
overstory rather than the soil. So moisture is an optional *fifth layer* rather
than a third habitat model — a profile keeps its vegetation or host-tree signal
and adds water to it. Two signals go into it. **Distance to mapped water** from
BC's Freshwater Atlas, with streams filtered by Strahler order, because a
first-order gully that runs dry in August is not the same habitat as a
fourth-order mainstem. And **topographic position** — a cell's elevation minus
the mean within a window around it — because a hollow or a toe slope collects
water whether or not a stream is drawn there, which is what a seepage plant
follows. The profile sets the blend.

For devil's club the two layers are doing different jobs, and the profile says
so: the forest layer finds the *forest type* (cedar-led wet forest, used as an
indicator — cedar forms arbuscular associations and hosts nothing, so this is
not a host relationship) and the moisture component finds the wet patch within
it. `forest.host_label` is what keeps the popup honest about that: it says
"cedar-rich forest", not "host-rich forest".

**Folk magic is content, not a signal.** The `folk_magic` block on each profile
never touches the score. It is documentary information — folk names, the
traditions a plant is documented in, the associations recorded for it, and a
hazard note — carried in the manifest and rendered on the site popup. Three
rules were applied when writing them, and the schema enforces the first: every
association names its `origin`, so European folk practice and Indigenous North
American practice are labelled rather than blended; the profiles say so when a
plant's magical record is thin (fireweed, the beard lichen) instead of inventing
an association to fill the field; and every block carries a safety note, because
several of these plants are dangerous — arnica is toxic taken internally, red
elderberry more so than the European elder its lore comes from, St John's wort
interacts with a long list of prescription medicines, and the fly agaric is a
deliriant poison. None of it is medical or magical advice, and the UI says so
wherever it appears.

**Hike cost is isotropic.** Tobler's hiking function is applied to terrain
slope magnitude, not slope along the direction of travel — the standard GIS
approximation. Expect hike times to be slightly conservative on traverses.

---

## Validation

### Against real occurrences

`scripts/validate.py` tests the model against research-grade iNaturalist
records over a 30,000 km² Kootenays extent at 90 m. Two confounds have to be
removed first: observations feed the score (circularity), and collectors walk
near roads, which the access layer rewards (sampling bias). So the test scores
against a **habitat-only** surface — terrain plus the species' habitat layer,
with access and observations dropped — and its sharper comparison is against a
**contrast taxon** that shares collectors, seasons and access bias with the
target but not its habitat. For arnica that is the forest congener
*A. cordifolia*.

```bash
python scripts/validate.py                                                  # arnica
python scripts/validate.py --species config/species/cantharellus_formosus.json
```

#### How the small sample is handled

With a few dozen records the useful question is not "what is the AUC" but
"what range of AUCs is this sample compatible with". The script, and the
statistics in `src/foraging/validation.py`, deal with five problems the
earlier version did not:

1. **Hard-filter rejects were silently dropped.** A record on ground the hard
   filters reject (NaN score) was excluded rather than counted as a miss. That
   conditions the test on the model already having succeeded, and it gutted
   the congener comparison: **54 of 65 usable *A. cordifolia* records sit below
   the 1,300 m hard floor**, so the old test compared the target against only
   the 11 cordifolia that happen to grow in subalpine terrain. The 8 target
   records on rejected ground were dropped too. Rejects now rank last.
2. **Layers were compared on different records.** The combined score is NaN
   wherever either hard filter fires, but the single-layer curves (elevation
   fit and so on) are finite almost everywhere, so each row of the old
   per-layer table was computed on a different subset of records. Every
   surface is now scored on the same records and the same null cells.
3. **Records are not independent.** 35 usable target records collapse to 22
   spatial clusters at 1 km: one person photographing one meadow repeatedly.
   The bootstrap resamples clusters, not records, and the permutation test
   exchanges cluster means.
4. **No uncertainty was reported**, and the claim "terrain alone beats terrain
   + vegetation" is a difference measured on the same records, which needs a
   paired interval. Every AUC now carries a 95% cluster-bootstrap interval,
   and layer comparisons use paired replicates.
5. **Obscured records were not excluded.** None of the arnica records are
   obscured, so this changes nothing for arnica; it matters for chanterelle
   (below).

Below 10 independent target clusters the script reports the record counts and
stops instead of printing an AUC.

#### Arnica results

35 target and 65 congener records (≤ 100 m stated accuracy; 22 and 54
clusters). Percentiles are among all cells of the extent, with rejected ground
ranked last.

| Group | Records | Median score percentile | On rejected ground |
|---|---|---|---|
| *A. latifolia* (target) | 35 | **0.81** | 8 |
| *A. cordifolia* (forest congener) | 65 | 0.00 | 54 |
| random null | 40,000 | 0.50 | — |

| Surface | AUC vs random null | AUC vs congener |
|---|---|---|
| habitat score (terrain + vegetation) | 0.70 [0.54, 0.81] | **0.83 [0.70, 0.91]** |
| terrain | 0.74 [0.60, 0.83] | 0.88 [0.76, 0.95] |
| — elevation fit | 0.65 [0.53, 0.73] | 0.86 [0.75, 0.93] |
| — slope fit | 0.60 [0.54, 0.64] | 0.53 [0.46, 0.59] |
| — aspect fit | 0.63 [0.52, 0.72] | 0.59 [0.43, 0.73] |
| vegetation layer | 0.68 [0.55, 0.76] | 0.57 [0.39, 0.71] |

AUC [95% interval]; 2,000 cluster-bootstrap replicates; 1 km clusters. Cluster
permutation p < 0.001 for the habitat score, terrain and elevation against the
congener; 0.44, 0.23 and 0.49 for slope, aspect and vegetation.

For comparison, the previous version of this script, re-run on the same data,
reported 0.72 against the null and 0.74 against the congener, on 27 and 11
records, with no intervals.

#### What the validation says the model is actually doing

**The model separates the target from the congener clearly.** AUC 0.83, with an
interval well clear of 0.5 — stronger than previously reported, because the
cordifolia records that the hard filters correctly reject now count. This is
the comparison that controls for collector bias, so it is the evidence that the
model tracks habitat rather than access.

**Elevation still carries almost all of it**, and the elevation band was set by
hand. If it was set while looking at these records, these figures are
in-sample and optimistic. Any further retune of the band against them makes
them training data; hold some records out first.

**Vegetation does useful work, but not species discrimination.** Against
random ground the vegetation layer alone scores 0.68 [0.55, 0.76]: it does pick
out arnica habitat from the landscape, which is its job (rejecting closed
canopy, scree and clearcut). Against the congener it is 0.57 [0.39, 0.71],
indistinguishable from chance. Adding it to terrain changes the AUC by −0.04
against both the null [−0.12, +0.03] and the congener [−0.11, +0.00]: no
detectable effect either way. The data cannot say that vegetation hurts, only
that it does not demonstrably help at 90 m, where meadow and open forest mix
inside one cell. The weights are left at their defaults.

**The intervals are what 22 clusters buy.** They are ±0.1 or wider. A layer
effect smaller than about 0.1 AUC cannot be detected with this sample, so a
re-weighting that moves the AUC by a few hundredths is not evidence of
anything. The conclusions above do not change between 0 m and 3 km clustering.
Treating records as independent (0 m) narrows every interval, and nearly
triples the apparent significance of the vegetation layer against the congener
(p = 0.14 against 0.49), which is exactly the overconfidence clustering
prevents.

#### Chanterelle: not validated, and why

The chanterelle model cannot be tested against occurrences in this region.
Across the whole 30,000 km² extent iNaturalist has one research-grade
*C. formosus* record, and it is obscured. So are all three *C. roseocanus*
records and the one *C. cascadensis* record. The only golden-chanterelle
records in the Kootenays have their public coordinates randomised over roughly
20 km, which is no use for testing a 30 m map. Usable *Cantharellus* records
of any species number 6 (all *C. subalbidus*, the white chanterelle). Running
the script for chanterelle reports these counts and stops.

This is not a statistical technicality to work around. No reweighting or
bootstrap recovers information that is not in the data. The honest status is
that the chanterelle map encodes published host associations and expert priors
(stated in the profile), and is unvalidated. Ways to fix that, in order of
value:

* **Your own finds.** Even 15–20 GPS points from distinct patches, kept private,
  would clear the minimum-sample guard. The script reads only iNaturalist
  today, so this needs a small local-CSV record source (not yet written).
* **A genus-level test**, with the target set to *Cantharellus* and a wider
  extent that reaches into the wetter Columbia mountains, once usable records
  exist there.
* The validation block's contrast is already set to false chanterelle
  (*Hygrophoropsis aurantiaca*, 9 usable records). It is a non-mycorrhizal
  saprotroph found in the same forests by the same pickers in the same weeks,
  so beating it would show that the model tracks host trees rather than
  "conifer forest where people pick mushrooms".

### Other checks

Drive times against known road distances from Rossland:

| Destination | Modelled | Actual road distance |
|---|---|---|
| Trail | 8.9 min | ~10 km |
| Castlegar | 30.2 min | ~28 km |
| Salmo | 41.6 min | ~42 km |

Slope and aspect are pinned by unit tests against synthetic planes on nine
bearings. `pytest` covers the membership curves, the terrain math, the
least-cost path attribution, host-tree scoring from VRI attributes, the
validation statistics, per-species config and paths, occurrence-record
filtering, the folk-magic schema, the moisture component, and a structural
check of every shipped species profile (393 tests).

```bash
pytest
```

Top-ranked sites were also rendered over satellite imagery and inspected by
eye — which is how the clearcut problem below was caught. It is worth repeating
after any significant retune.

---

## Troubleshooting

**`WarpOperationError: Chunk and warp failed`, or `TIFFFillTile: got 0 bytes`**
during the vegetation stage. A truncated HTTP range read from the imagery host
— a network hiccup, not bad data. Reads retry with backoff and re-sign the URL,
and a scene that still fails is skipped rather than sinking the run. If it
happens anyway, just re-run:

```bash
forage run --only vegetation,scoring,export
```

The DEM, road, tenure and cutblock layers are cached on disk, so this resumes
rather than starting over.

**`forage: command not found`** — the virtualenv is not active. `source
.venv/bin/activate`, or call `.venv/bin/forage` directly.

**Map loads but says "Could not load data"** — either `forage run` has not been
run yet, or `index.html` was opened as a file. It has to be served over HTTP:
`python -m http.server -d web 8000`.

---

## Outputs

```
output/<aoi>/<species>/
  sites.geojson         ranked sites with full attributes
  observations.geojson  iNaturalist records used
  suitability.tif       the score surface
  manifest.json         AOI/species/filter metadata for the UI
web/data/<aoi>/<species>/   PNG overlays + GeoJSON + manifest, ready to serve
```

Each ranked site carries: coordinates, score, per-layer score breakdown,
elevation, slope, aspect (degrees and compass), patch area, drive minutes, hike
km and minutes, nearby iNaturalist counts, and land-status flag; plus NDVI,
canopy closure and vegetation class for spectral targets, or forest class,
leading tree species, host share and stand age for host-tree targets.

Builds made before outputs were split by species sit directly under
`output/<aoi>/` and `web/data/<aoi>/`. They are ignored, not migrated; re-run
`forage run` (with `--reuse-imagery` to skip the Sentinel-2 download).

---

## Map UI

Leaflet, vendored locally so it works offline. Toggleable layers for
suitability, elevation, slope, aspect, and then whichever habitat layers the
species uses: NDVI and vegetation class (spectral targets), forest class,
host-tree share and stand age (host-tree targets), plus a moisture-credit
overlay for any profile that carries a moisture block. Plus years since
logging, land tenure,
roads, trails, protected areas and observations. Filters
for minimum score, max drive time, max hike distance, elevation band, hiding
flagged land and hiding recently logged ground. Vector layers are fetched only
when switched on, since roads alone is several MB.

**Folk magic on the pin.** Clicking a site opens the usual per-site data and,
below it, the species' folk-magic panel: folk names, theme chips, each recorded
association with the tradition it comes from, and the safety note. A sidebar
panel shows the same block for the species currently loaded, so two species can
be compared without clicking a pin. The panel is framed as folklore wherever it
appears — it is not advice, and the map does not tell you anything is safe to
touch or eat.

**Getting sites into the field:** the ranked list exports as **GPX** (waypoints
for a GPS or phone, carrying elevation, score, drive/hike and land status in
the description) or **CSV**. Both export exactly what the current filters
leave visible, not the whole list.

---

## Out of scope

* Snowpack, road-closure and seasonal-access modelling — you track those.
* Harvest timing and plant ID — that belongs in the field guide, not here.
