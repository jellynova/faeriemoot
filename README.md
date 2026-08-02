# Foraging Suitability Mapper

Predicts and ranks likely wild-foraging sites by stacking terrain, vegetation,
access and observation data into a per-site suitability score, then serving the
ranked sites as clickable pins on an interactive map.

The first target is mountain arnica (*Arnica latifolia*) in the West Kootenays,
BC — Rossland / Castlegar / Salmo. Nothing about that region is hard-coded:
the AOI, the target species and the scoring weights are all swappable config
files.

> **This tool describes land tenure, not permission.** A site's land-status
> flag tells you what kind of ground it is, not whether you may harvest there.
> Confirm access and harvesting rules yourself. Snowpack and seasonal road
> closures are deliberately not modelled.

---

## Quick start

```bash
uv venv && uv pip install -e .        # or: pip install -e .
forage run                            # full pipeline, ~2 minutes
python -m http.server -d web 8000     # then open http://localhost:8000
```

No API keys, accounts or tokens are required. Every data source is public and
anonymous.

---

## What it does

Each 30 m cell in the area of interest is scored by four layers, then
high-scoring cells are clustered into discrete sites and ranked.

| Layer | What it contributes | Source |
|---|---|---|
| **Terrain** | Elevation band, slope, aspect (south-to-southwest preferred) | Copernicus GLO-30 DEM via Planetary Computer |
| **Vegetation** | Open meadow / open forest vs closed canopy or bare rock | Sentinel-2 L2A, seasonal composite |
| **Access** | Drive minutes from your origin + least-cost hike from the road | BC Digital Road Atlas, forest tenure roads, OSM trails |
| **Observations** | Proximity boost from real sightings | iNaturalist research-grade records |
| **Land status** | Tenure flag — *flagged, never silently down-ranked* | BC parks, ParcelMap BC, forest tenure |

Drive and hike are reported separately, never collapsed into one number, so you
can judge the trade-off yourself.

### Pipeline stages

```
terrain → vegetation → access → observations → landstatus → scoring → export
```

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
| `config/species/*.json` | Elevation band, slope/aspect preference, vegetation thresholds, iNaturalist taxon |
| `config/weights.json` | Layer weights, hard filters, which land classes are excluded vs flagged |

### Setting your origin

Drive times are measured from one configurable point. It is not baked in
anywhere else:

```json
"origin": { "name": "Rossland, BC", "lon": -117.7997, "lat": 49.0781 }
```

### Re-targeting at a new region

Drop a polygon into `config/aoi/`, point `pipeline.json` at it, and re-run:

```bash
cp my_area.geojson config/aoi/nelson.geojson
# set "aoi": "config/aoi/nelson.geojson" in config/pipeline.json
forage run
```

The analysis grid picks its own UTM zone from the AOI, every layer is fetched
for the new extent, and outputs land in a parallel `output/nelson/` and
`web/data/nelson/`. The map UI reads `web/data/index.json` and offers a region
switcher — no code changes.

### Adding a target species

Copy a species profile, edit the bands, and point `pipeline.json` at it. The
vegetation thresholds are the part most worth re-tuning; see the notes below on
how the current ones were calibrated.

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

**Hike cost is isotropic.** Tobler's hiking function is applied to terrain
slope magnitude, not slope along the direction of travel — the standard GIS
approximation. Expect hike times to be slightly conservative on traverses.

---

## Validation

Drive times were checked against known road distances from Rossland:

| Destination | Modelled | Actual road distance |
|---|---|---|
| Trail | 8.9 min | ~10 km |
| Castlegar | 30.2 min | ~28 km |
| Salmo | 41.6 min | ~42 km |

Slope and aspect are pinned by unit tests against synthetic planes on nine
bearings. `pytest` covers the membership curves, the terrain math and the
least-cost path attribution (42 tests).

```bash
pytest
```

---

## Outputs

```
output/<aoi>/
  sites.geojson         ranked sites with full attributes
  observations.geojson  iNaturalist records used
  suitability.tif       the score surface
  manifest.json         AOI/species/filter metadata for the UI
web/data/<aoi>/         PNG overlays + GeoJSON + manifest, ready to serve
```

Each ranked site carries: coordinates, score, per-layer score breakdown,
elevation, slope, aspect (degrees and compass), NDVI, canopy closure,
vegetation class, patch area, drive minutes, hike km and minutes, nearby
iNaturalist counts, and land-status flag.

---

## Map UI

Leaflet, vendored locally so it works offline. Toggleable layers for
suitability, elevation, slope, aspect, NDVI, vegetation class, land tenure,
roads, trails, protected areas and observations. Filters for minimum score,
max drive time, max hike distance, elevation band, and hiding flagged land.
Vector layers are fetched only when switched on.

---

## Out of scope

* Snowpack, road-closure and seasonal-access modelling — you track those.
* Harvest timing and plant ID — that belongs in the field guide, not here.
