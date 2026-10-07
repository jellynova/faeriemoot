# Foraging Suitability Mapper

Predicts and ranks likely wild-foraging sites by stacking terrain, vegetation,
access and observation data into a per-site suitability score, then serving the
ranked sites as clickable pins on an interactive map.

Two targets ship for the West Kootenays, BC (Rossland / Castlegar / Salmo):
mountain arnica (*Arnica latifolia*), a subalpine meadow plant, and mountain
chanterelle (*Cantharellus formosus*), a fungus that lives on Douglas-fir roots.
They need different habitat models: arnica is found from satellite imagery
of open ground, the chanterelle from BC's forest inventory of which trees grow
where. Nothing about the region is hard-coded: the AOI, the target species and
the scoring weights are all swappable config files.

> **This tool describes land tenure, not permission.** A site's land-status
> flag tells you what kind of ground it is, not whether you may harvest there.
> Confirm access and harvesting rules yourself. Snowpack and seasonal road
> closures are deliberately not modelled.

---

## Quick start

```bash
uv venv && uv pip install -e .        # or: pip install -e .
forage run                            # arnica, full pipeline, ~2 minutes
forage run --species config/species/cantharellus_formosus.json   # chanterelle, ~3 minutes
python -m http.server -d web 8000     # then open http://localhost:8000
```

No API keys, accounts or tokens are required. Every data source is public and
anonymous.

---

## What it does

Each 30 m cell in the area of interest is scored by four layers, then
high-scoring cells are clustered into discrete sites and ranked. The habitat
layer is one of two, chosen by the species profile's `habitat_model`.

| Layer | What it contributes | Source |
|---|---|---|
| **Terrain** | Elevation band, slope, aspect preference | Copernicus GLO-30 DEM via Planetary Computer |
| **Habitat: vegetation** (meadow plants) | Open meadow / open forest vs closed canopy or bare rock | Sentinel-2 L2A, seasonal composite |
| **Habitat: forest** (mycorrhizal fungi) | Host-tree share of the stand, stand age, crown closure | BC Vegetation Resources Inventory (VRI) |
| **Access** | Drive minutes from your origin + least-cost hike from the road | BC Digital Road Atlas, forest tenure roads, OSM trails |
| **Observations** | Proximity boost from real sightings | iNaturalist research-grade records |
| **Land status** | Tenure flag — *flagged, never silently down-ranked* | BC parks, ParcelMap BC, forest tenure |

Drive and hike are reported separately, never collapsed into one number, so you
can judge the trade-off yourself.

### Pipeline stages

```
terrain → vegetation | forest → access → observations → landstatus → scoring → export
```

Whichever of `vegetation` / `forest` the species does not use is skipped, so a
chanterelle run never downloads Sentinel-2 imagery.

Any stage can be re-run on its own (`forage scoring`), reading what it needs
from `data/interim/<aoi>/`. Retuning weights and rebuilding the map takes a few
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
| `config/species/*.json` | Habitat model, elevation band, slope/aspect preference, vegetation or forest thresholds, iNaturalist taxon, validation taxa, per-species weight overrides |
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

Every run is keyed by AOI *and* species — `data/interim/<aoi>/<species>/`,
`output/<aoi>/<species>/`, `web/data/<aoi>/<species>/` — so several targets on
one region sit side by side. (Directories from the older per-AOI layout, e.g.
`data/interim/west_kootenays/*.tif`, are no longer read and can be deleted.)

Pick the habitat model first:

* `"habitat_model": "vegetation"` — Sentinel-2 openness, for plants of meadow
  and open forest. Copy `arnica_latifolia.json`. The vegetation thresholds are
  the part most worth re-tuning; see the modelling notes below.
* `"habitat_model": "forest"` — BC VRI stand composition, for anything that
  depends on particular trees. Copy `cantharellus_formosus.json` and edit
  `forest.hosts`, which gives each VRI tree species code a host credit.

A profile can also carry `weights_override` (merged over `config/weights.json`
for that species only) and a `validation` block naming the target and contrast
taxa for `scripts/validate.py`.

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

**Hike cost is isotropic.** Tobler's hiking function is applied to terrain
slope magnitude, not slope along the direction of travel — the standard GIS
approximation. Expect hike times to be slightly conservative on traverses.

### The chanterelle model

**Why the arnica model does not transfer.** The vegetation stage rewards open
canopy. A conifer-root fungus is absent from exactly that ground, and mid-summer
Douglas-fir, larch, cedar and spruce are indistinguishable by NDVI anyway. What
matters is *which trees* and *how old the stand is*. BC's Vegetation Resources
Inventory records both for every stand in the province: up to six tree species
with percentages, projected age and crown closure. It covers 100% of this AOI
in 23,722 stands.

**How a stand is scored.** `host_fit × age_fit × closure_fit`. Host fit comes
from the stand's species percentages weighted by `forest.hosts` (Douglas-fir
1.0, western hemlock 0.5), with full credit from 50%, i.e. a Douglas-fir-leading
stand. It has no floor, because a mycorrhizal obligate has no habitat without
its host. Stand age gives nothing to fresh cutblocks and full credit from 40
years. Crown closure is a soft preference with a 0.4 floor. Stand age is
cross-checked against the consolidated cutblock layer, and the younger of the
two wins, because VRI depletions lag recent harvest. That override touched
300,000 cells here. Weights are overridden for this species (host forest 0.45,
terrain 0.15) because the host's own range already encodes most of what
elevation would.

**What the records say, and what they cannot.** Precise *Cantharellus* records
across the 30,000 km² validation extent sit in Douglas-fir-leading stands 60% of
the time (random ground 20%), at a median 666 m (random ground 1,545 m), and
none sit in a stand younger than about 55 years. Those observations set the
priors in the profile. But there are only **10** such records at **7** sites.
13 of 25 are obscured, which is expected for a choice edible. That is far too
few to calibrate against, and the formal validation below cannot tell the model
from chance. Treat the thresholds as informed priors, not measurements.

**This is a Douglas-fir chanterelle model, not a *formosus* model.**
*C. formosus* is chiefly coastal. It has no research-grade records in this AOI
and one in the whole validation extent. Kootenay chanterelle records are
*C. subalbidus* (white chanterelle) and *C. roseocanus*, so the observation
layer and the validation both work at genus level.

**Genus queries return look-alikes.** iNaturalist resolves `taxon_name` through
synonyms, so a `Cantharellus` query also returns the false chanterelle
(*Hygrophoropsis aurantiaca*, once *Cantharellus aurantiacus*) and
*Craterellus tubaeformis*. In this AOI that was 6 of 8 records. Records outside
the queried genus are now dropped, along with obscured and imprecise ones
(`max_accuracy_m`, `drop_obscured`), whose public coordinates are randomised
across ~20 km.

**The top of the ranking is a tie.** Every mature Douglas-fir-leading stand near
a road saturates terrain, forest and access, so the top 1% of cells all score
0.920. Nothing in ten records justifies finer grading within such stands, so
the model does not pretend to have any. Ties are broken by the patch's mean
score and then its area, i.e. by how much contiguous good ground surrounds the
peak. Read the list as *a few hundred equally plausible stands, nearest
first*, not as a ranking of quality.

---

## Validation

### Against real occurrences

`scripts/validate.py` tests the model against research-grade iNaturalist
records over a 30,000 km² Kootenays extent. Two confounds have to be removed
first: observations feed the score (circularity), and people walk near roads,
which the access layer rewards (sampling bias). So the test scores against a
**habitat-only** surface (terrain plus the species' habitat layer), with
access and observations dropped. Records coarser than 100 m positional accuracy
are excluded.

```bash
python scripts/validate.py                                                   # arnica
python scripts/validate.py --species config/species/cantharellus_formosus.json
```

Each target is compared with a random null and with a **contrast taxon** that
shares its collectors, season and access bias but not its habitat:
*A. cordifolia* (a forest congener) for arnica, and the false chanterelle (a
wood-rotting saprotroph with no host tree) for the chanterelle. Beating the
contrast means the model tracks *habitat* rather than *where people walk*.

**How the figures are computed.** The samples are small and clustered, so
every AUC carries a stratified bootstrap 95% CI and a one-sided Mann-Whitney p.
Each AUC is reported per record and per **site**, where same-species records
within 1 km are merged, because twelve photos of one meadow are one piece of
evidence. Records the hard filters reject are counted. In the "rejected = 0"
rows they score zero, which is what the model actually says about them. The
statistics live in `src/foraging/stats.py` and are unit-tested.

#### Arnica (*A. latifolia*), October 2026 records

35 precise target records at 22 sites; 65 congener records at 54 sites.

| Comparison, rejected = 0 | per record | per site |
|---|---|---|
| target vs random null | 0.70 [0.60–0.79] | 0.59 [0.47–0.71], p = 0.07 |
| target vs forest congener | 0.83 [0.75–0.91] | **0.75 [0.63–0.86]**, p = 2e-5 |
| terrain alone, target vs congener | 0.88 [0.79–0.95] | **0.81 [0.70–0.91]**, p = 3e-7 |

Per-layer, target vs congener, per site: elevation **0.80 [0.69–0.90]**, aspect
0.57 [0.42–0.70], vegetation 0.56 [0.40–0.72], slope 0.53 [0.44–0.61]. Only
elevation's interval clears 0.5.

#### What changed from the earlier write-up, and why

The earlier figures (target n = 26, congener n = 10, target-vs-congener
AUC 0.71) came from a script that **dropped every record on a cell the hard
filters reject**. That quietly removed 54 of 65 congener records, nearly all
below the 1,300 m elevation floor. The "congener" group was therefore the
handful of *A. cordifolia* records that happen to sit in subalpine terrain,
the ones most like the target. The same filter hid the model's own misses:
**8 of 35 target records (23%) fall on ground the model rejects outright** (6
by terrain, 2 more by vegetation). Counting both properly, the model separates
the two species better than previously reported, and it is wrong about a
quarter of known target locations. The "admitted cells only" rows in the
script output reproduce the old, conditional analysis for comparison.
Percentiles now count ties half (mid-rank), so a group's mean percentile
equals its AUC.

### What the validation says the model is actually doing

**Elevation carries almost all of it.** It is the only layer whose interval
excludes chance. If you retune one thing, retune the elevation band, and look
at the 23% of target records it rejects first.

**Vegetation does not add species discrimination; it slightly costs it.** On
the same records, a paired bootstrap of AUC(terrain + vegetation) minus
AUC(terrain alone) gives −0.04 [−0.10 to +0.00] against the congener, with 97% of
replicates at or below zero. Against the random null it is −0.04 [−0.10 to +0.02].
That is a small cost, not the −0.10 the earlier unpaired comparison suggested,
which mixed two different record sets. The caveats stand: the validation
extent runs at 90 m, where meadow and open forest mix inside one cell, and the
congener test cannot reward vegetation for its actual job of rejecting closed
canopy, scree and clearcut. The weights are left at their defaults.

**Beating chance is the weak result, not the strong one.** Per site, target
vs random null is 0.59 [0.47–0.71]. Arnica is subalpine and so is much of the
random extent, so this test asks more of the graded score within the band
than the congener test does. With 22 sites it cannot confirm that the model
ranks *within* subalpine ground better than chance.

#### Chanterelle: inconclusive, and that is the result

Only 10 precise *Cantharellus* records exist in the extent, at 7 sites; 13 of
25 are obscured. Against the false chanterelle (9 sites) per-site AUC is 0.44
[0.11–0.76]. Against the null it is 0.57 [0.32–0.83]. No interval comes close
to excluding 0.5, and no per-layer AUC does either. VRI is sampled at the
evaluated points rather than downloaded for the whole extent (~317,000
stands), so stand age here is VRI's own, without the cutblock override. Read
the chanterelle profile as informed priors until there are records to test
them.

**Samples are small everywhere.** Per-site counts are the honest sample size.
With under ~15 sites per group, percentile bootstrap intervals run narrow, so
treat them as the *least* uncertainty there is.

### Other checks

Drive times against known road distances from Rossland:

| Destination | Modelled | Actual road distance |
|---|---|---|
| Trail | 8.9 min | ~10 km |
| Castlegar | 30.2 min | ~28 km |
| Salmo | 41.6 min | ~42 km |

Slope and aspect are pinned by unit tests against synthetic planes on nine
bearings. `pytest` covers the membership curves, the terrain math and the
least-cost path attribution, VRI forest scoring, config
loading and the validation statistics (82 tests).

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
elevation, slope, aspect (degrees and compass), habitat class, patch area,
drive minutes, hike km and minutes, nearby iNaturalist counts, and land-status
flag; plus NDVI and canopy closure for a vegetation-model species, or stand
composition, host-tree share, stand age and VRI crown closure for a
forest-model one.

---

## Map UI

Leaflet, vendored locally so it works offline. Toggleable layers for
suitability, elevation, slope, aspect, NDVI and vegetation class (or, for a
forest-model species, forest stand class, host-tree share and stand age), years
since logging, land tenure, roads, trails, protected areas and observations. Filters
for minimum score, max drive time, max hike distance, elevation band, hiding
flagged land and hiding recently logged ground. Vector layers are fetched only
when switched on, since roads alone is several MB.

**Getting sites into the field:** the ranked list exports as **GPX** (waypoints
for a GPS or phone, carrying elevation, score, drive/hike and land status in
the description) or **CSV**. Both export exactly what the current filters
leave visible, not the whole list.

---

## Out of scope

* Snowpack, road-closure and seasonal-access modelling — you track those.
* Harvest timing and plant ID — that belongs in the field guide, not here.
