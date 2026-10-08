# Species selection: folk-magic plants and fungi of the West Kootenays

Working notes for the expansion from two targets (mountain arnica, golden
chanterelle) to a set of plants and fungi with documented folk-magic,
herbal or folkloric traditions. Kept as a running log: each species gets a
line here when its profile lands.

Two tests had to pass for a species to be added:

1. **It actually grows here.** Checked against research-grade iNaturalist
   records inside the West Kootenays AOI (Rossland / Castlegar / Salmo,
   ~2,900 km²) and the wider Kootenays validation extent (~30,000 km²),
   resolved by exact taxon ID. Record counts are a floor, not a census:
   lichens and fungi are badly under-recorded, valley bottoms over-recorded.
2. **It has a documented tradition**, not just a modern correspondence-table
   entry. Each profile's `folklore` block tags every association with how
   well it is attested (see Part 2 below), and sources are named.

## Presence check

Records as of October 2026. "AOI" is the West Kootenays target area;
"Kootenays" the wider validation extent. Elevations are sampled from the
Copernicus 30 m DEM (90 m grid) at non-obscured records with ≤100 m stated
accuracy, so they describe where people *recorded* the plant, which is biased
toward roads and valley floors. Lower tails understate how high a species
goes; upper tails are the more trustworthy bound.

| Candidate | AOI | Kootenays | Elev. p05–p50–p95 (m) | Decision |
|---|---|---|---|---|
| Devil's club *Oplopanax horridus* | 21 | 290 | 547–805–1497 | **added** |
| Fireweed *Chamaenerion angustifolium* | 131 | 625 | 523–1307–2200 | **added** |
| Stinging nettle *Urtica dioica* | 13 | 67 | 518–744–1743 | **added** |
| Yarrow *Achillea millefolium* | 79 | 402 | 475–708–2135 | **added** |
| Common mullein *Verbascum thapsus* (introduced) | 111 | 403 | 446–549–1063 | **added** |
| Blue elderberry *Sambucus cerulea* | 52 | 188 | 449–589–971 | **added** |
| Red elderberry *Sambucus racemosa* | 14 | 165 | 536–1677–1983 | not added — see below |
| Common juniper *Juniperus communis* | 50 | 237 | 536–1185–2367 | **added** |
| Rocky Mountain juniper *J. scopulorum* | 76 | 151 | 406–534–951 | folded into juniper as a down-weighted congener |
| Wild roses *Rosa* spp. (genus) | 23 | 73 | 423–559–897 | **added** at genus level |
| Common mugwort *Artemisia vulgaris* | **0** | 9 | — | **not present** in the AOI |
| Wormwood *A. absinthium* (introduced) | 4 | 32 | — | too sparse, and the toxicity story dominates |
| Western mugwort *A. ludoviciana* | 15 | 54 | 403–450–561 | **added** as the regional mugwort |
| St John's wort *Hypericum perforatum* (introduced) | 43 | 150 | 436–560–1080 | **added** |
| Western redcedar *Thuja plicata* | 262 | 791 | 465–808–1336 | **added** |
| Douglas-fir *Pseudotsuga menziesii* | 179 | 423 | 415–595–1399 | not added — see below |
| Old man's beard *Usnea* spp. (genus) | 3 | 6 | 453–567–1139 | **added** — under-recorded, see below |
| Mountain-ash *Sorbus* (genus; *S. scopulina* 8 AOI) | 16 | 73 | 460–652–1922 | **added** (not on the original list) |
| Black hawthorn *Crataegus douglasii* | 9 | 33 | 411–514–713 | **added** (not on the original list) |
| Fly agaric *Amanita muscaria* | 43 | 93 | 434–612–1762 | **added** (not on the original list) |

### Notes on the calls

* **Elderberry does reach this far inland**, and the better-recorded one in
  the AOI is *blue* elderberry (52 records vs 14 for red). Blue elderberry is
  often treated as *Sambucus nigra* subsp. *cerulea*, the same species as the
  European elder of the elder-mother folklore, so it is the better fit on
  both counts. Red elderberry here is mostly subalpine (median 1,677 m) and
  carries no tradition of its own beyond the genus, so it would add a map
  without adding anything to the folklore side.
* **Mugwort:** the plant of the European tradition, *A. vulgaris*, has no
  records in the AOI, so it is not forced in. *A. ludoviciana* (western
  mugwort, "prairie sage") is the regional mugwort: a plant of the dry
  Columbia valley terraces, used in purification smudging in North American
  Indigenous practice. The European mugwort lore attaches to it only at the
  genus level, and its folklore block says so.
* **Douglas-fir** is common, but its documented ceremonial uses are generic
  Salish conifer-bough cleansing, which the cedar and juniper profiles
  already cover, and its habitat map would duplicate the chanterelle host
  layer, which is already mostly Douglas-fir. Western redcedar has the
  stronger, more specific tradition, so it is the conifer added.
* **Usnea** has only a handful of iNaturalist records, because lichens are
  rarely photographed to research grade. The genus is common on conifers in
  the humid ICH forests here, so it is added with its observation layer
  treated as a flat baseline, the same situation as chanterelle.
* **Added beyond the original list:** mountain-ash (rowan) and hawthorn are
  two of the most heavily documented protective trees in British and Irish
  folk magic, and both genera have native species here. Fly agaric is the
  archetypal "magic mushroom" of European folklore and occurs here with
  conifers and birch. All three are confirmed present above.

## Habitat approaches

The pipeline had two habitat models; the new species needed a third signal.

| Approach | Habitat signal | Species |
|---|---|---|
| Spectral, meadow / open | Sentinel-2 NDVI + NDMI canopy closure | arnica, yarrow, fireweed, juniper |
| Spectral, dry & disturbed | as above, with an NDVI *ceiling* and no clearcut penalty | mullein, St John's wort, western mugwort |
| Spectral, edge / open forest | as above, open forest preferred over meadow | rose, mountain-ash, blue elderberry |
| Spectral + moisture | as above, plus distance to water and topographic hollows | nettle, hawthorn, (elderberry, lightly) |
| Forest composition (VRI) | stand species mix as host or indicator | chanterelle, fly agaric (mycorrhizal); cedar (the tree itself); usnea (substrate) |
| Forest composition + moisture | as above, plus moisture | devil's club |

## Log

* Moisture component (Freshwater Atlas distance to water + topographic
  position index) added as an optional score layer.
* Observation matching extended to descendants, so genus-level targets and
  subspecies records count as the target.
