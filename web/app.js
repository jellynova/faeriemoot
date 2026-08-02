/* Foraging Suitability Mapper - map UI.
 *
 * Everything here is driven by web/data/<aoi>/manifest.json, so adding a new
 * area of interest means running the pipeline against a new polygon; no code
 * in this file knows anything about the West Kootenays.
 */
(function () {
  "use strict";

  var DATA = "data/";
  var map, sitesLayer, aoiLayer;
  var current = null;          // { id, manifest, sites }
  var rasterLayers = {};       // key -> L.imageOverlay
  var vectorLayers = {};       // key -> L.geoJSON (lazily fetched)
  var vectorLoading = {};
  var markers = [];
  var activeLegend = null;

  var VECTOR_SPECS = {
    roads: { label: "Roads & FSRs", file: "roads.geojson", colour: "#c98b52", weight: 1.2 },
    trails: { label: "Trails", file: "trails.geojson", colour: "#e8d07a", weight: 1.2, dash: "4,3" },
    protected_areas: { label: "Protected areas", file: "protected_areas.geojson", colour: "#5fbf8f", fill: true },
    observations: { label: "iNaturalist records", file: "observations.geojson", colour: "#e2679a", points: true }
  };

  var VEG_LEGEND = [
    ["#78c85a", "Open meadow"],
    ["#3c8c50", "Open forest"],
    ["#19462d", "Closed forest"],
    ["#c67a3e", "Regenerating cutblock"],
    ["#aa9682", "Bare / rock / scree"]
  ];

  var TENURE_LEGEND = [
    ["#5aaa6e", "Provincial park"],
    ["#dc7878", "Private property"],
    ["#c8aa5a", "Woodlot licence"],
    ["#96be78", "Recreation area"]
  ];

  // ---------------------------------------------------------------- helpers
  function $(id) { return document.getElementById(id); }

  function fetchJSON(url) {
    return fetch(url).then(function (r) {
      if (!r.ok) throw new Error(url + " -> " + r.status);
      return r.json();
    });
  }

  function fmt(v, digits, suffix) {
    if (v === null || v === undefined || isNaN(v)) return "-";
    return Number(v).toFixed(digits === undefined ? 0 : digits) + (suffix || "");
  }

  function minutesLabel(m) {
    if (m === null || m === undefined || isNaN(m)) return "-";
    if (m < 90) return Math.round(m) + " min";
    var h = Math.floor(m / 60);
    return h + " h " + Math.round(m - h * 60) + " min";
  }

  function scoreColour(s) {
    // Same viridis-ish family as the suitability raster, so pins and overlay agree.
    var stops = [[0, 68, 1, 84], [0.25, 59, 82, 139], [0.5, 33, 145, 140],
                 [0.75, 94, 201, 98], [1, 253, 231, 37]];
    var t = Math.max(0, Math.min(1, s));
    for (var i = 1; i < stops.length; i++) {
      if (t <= stops[i][0]) {
        var a = stops[i - 1], b = stops[i];
        var f = (t - a[0]) / (b[0] - a[0]);
        return "rgb(" + Math.round(a[1] + f * (b[1] - a[1])) + "," +
                        Math.round(a[2] + f * (b[2] - a[2])) + "," +
                        Math.round(a[3] + f * (b[3] - a[3])) + ")";
      }
    }
    return "rgb(253,231,37)";
  }

  // ------------------------------------------------------------------- init
  function init() {
    map = L.map("map", { zoomControl: true, preferCanvas: true });

    var topo = L.tileLayer("https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png", {
      maxZoom: 17, attribution: "&copy; OpenTopoMap, OpenStreetMap contributors"
    });
    var sat = L.tileLayer(
      "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
      { maxZoom: 19, attribution: "Imagery &copy; Esri" });
    var osm = L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 19, attribution: "&copy; OpenStreetMap contributors"
    });

    topo.addTo(map);
    L.control.layers({ "Topographic": topo, "Satellite": sat, "Street": osm }, null,
                     { position: "topright" }).addTo(map);
    L.control.scale({ imperial: false }).addTo(map);

    sitesLayer = L.layerGroup().addTo(map);

    bindControls();

    fetchJSON(DATA + "index.json")
      .then(function (idx) {
        var sel = $("area-select");
        idx.areas.forEach(function (a) {
          var o = document.createElement("option");
          o.value = a.id;
          o.textContent = a.label + " (" + a.sites + " sites)";
          sel.appendChild(o);
        });
        sel.addEventListener("change", function () { loadArea(sel.value); });
        if (!idx.areas.length) throw new Error("no built areas");
        loadArea(idx.areas[0].id);
      })
      .catch(function (err) {
        $("site-list").innerHTML =
          '<li style="color:#e0a03c">Could not load data (' + err.message +
          ').<br><br>Run the pipeline first:<br><code>forage run</code><br><br>' +
          'then serve this directory over HTTP:<br><code>python -m http.server</code></li>';
      });
  }

  // -------------------------------------------------------------- area load
  function loadArea(id) {
    Object.keys(rasterLayers).forEach(function (k) { map.removeLayer(rasterLayers[k]); });
    Object.keys(vectorLayers).forEach(function (k) { map.removeLayer(vectorLayers[k]); });
    rasterLayers = {}; vectorLayers = {}; vectorLoading = {};
    if (aoiLayer) { map.removeLayer(aoiLayer); aoiLayer = null; }

    var base = DATA + id + "/";
    Promise.all([fetchJSON(base + "manifest.json"), fetchJSON(base + "sites.geojson")])
      .then(function (res) {
        current = { id: id, base: base, manifest: res[0], sites: res[1].features || [] };
        applyManifest();
        fetch(base + "aoi.geojson").then(function (r) { return r.ok ? r.json() : null; })
          .then(function (g) {
            if (!g) return;
            aoiLayer = L.geoJSON(g, {
              style: { color: "#9ccf5a", weight: 1.5, fill: false, dashArray: "6,4" }
            }).addTo(map);
          });
        var b = current.manifest.image_bounds;
        if (b) map.fitBounds(b);
        buildLayerToggles();
        render();
      });
  }

  function applyManifest() {
    var m = current.manifest;
    var sp = m.species || {};
    $("species-line").textContent =
      (sp.common_name || "") + (sp.scientific_name ? " - " + sp.scientific_name : "");

    var f = m.filters || {};
    var r = m.ranges || {};

    setRange("f-drive", 0, Math.max(180, Math.ceil((r.drive_minutes || [0, 180])[1] / 5) * 5), f.max_drive_minutes);
    setRange("f-hike", 0, Math.max(10, Math.ceil((r.hike_km || [0, 10])[1])), f.max_hike_km);

    var elev = f.elevation_m || {};
    var lo = Math.floor((elev.hard_min || 0) / 100) * 100;
    var hi = Math.ceil((elev.hard_max || 3000) / 100) * 100;
    setRange("f-elev-min", lo, hi, lo);
    setRange("f-elev-max", lo, hi, hi);
    setRange("f-score", 0, 1, 0);
    $("f-score").step = 0.01;

    var w = m.imagery_window || {};
    var origin = m.origin || {};
    $("provenance").textContent =
      "Drive times from " + (origin.name || "origin") + ". Imagery " +
      (w.window_start || "") + " to " + (w.window_end || "") +
      " across " + ((w.years || []).join(", ")) + ".";

    syncOutputs();
  }

  function setRange(id, min, max, value) {
    var el = $(id);
    el.min = min; el.max = max;
    el.value = value === undefined || value === null ? max : value;
  }

  // ---------------------------------------------------------------- filters
  function bindControls() {
    ["f-score", "f-drive", "f-hike", "f-elev-min", "f-elev-max"].forEach(function (id) {
      $(id).addEventListener("input", function () { syncOutputs(); render(); });
    });
    $("f-exclude-flagged").addEventListener("change", render);
    $("f-exclude-cutblock").addEventListener("change", render);

    $("f-opacity").addEventListener("input", function () {
      var v = Number(this.value);
      $("out-opacity").textContent = v + "%";
      Object.keys(rasterLayers).forEach(function (k) { rasterLayers[k].setOpacity(v / 100); });
    });

    $("reset-filters").addEventListener("click", function () {
      $("f-score").value = $("f-score").min;
      $("f-drive").value = $("f-drive").max;
      $("f-hike").value = $("f-hike").max;
      $("f-elev-min").value = $("f-elev-min").min;
      $("f-elev-max").value = $("f-elev-max").max;
      $("f-exclude-flagged").checked = false;
      $("f-exclude-cutblock").checked = false;
      syncOutputs(); render();
    });

    $("sidebar-toggle").addEventListener("click", function () {
      $("sidebar").classList.toggle("open");
    });
  }

  function syncOutputs() {
    // Keep the elevation handles from crossing over.
    var lo = $("f-elev-min"), hi = $("f-elev-max");
    if (Number(lo.value) > Number(hi.value)) {
      if (document.activeElement === lo) hi.value = lo.value; else lo.value = hi.value;
    }
    $("out-score").textContent = Number($("f-score").value).toFixed(2);
    $("out-drive").textContent = $("f-drive").value + " min";
    $("out-hike").textContent = Number($("f-hike").value).toFixed(2) + " km";
    $("out-elev").textContent = lo.value + "-" + hi.value + " m";
  }

  function passesFilters(p) {
    if (p.score < Number($("f-score").value)) return false;
    if (p.drive_minutes !== null && p.drive_minutes > Number($("f-drive").value)) return false;
    if (p.hike_km !== null && p.hike_km > Number($("f-hike").value)) return false;
    var e = p.elevation_m;
    if (e !== null && (e < Number($("f-elev-min").value) || e > Number($("f-elev-max").value))) return false;
    if ($("f-exclude-flagged").checked && p.land_flagged) return false;
    if ($("f-exclude-cutblock").checked && p.on_cutblock &&
        p.years_since_logging !== null && p.years_since_logging < 45) return false;
    return true;
  }

  // ----------------------------------------------------------------- render
  function render() {
    if (!current) return;
    sitesLayer.clearLayers();
    markers = [];

    var visible = current.sites.filter(function (f) { return passesFilters(f.properties); });

    visible.forEach(function (f) {
      var p = f.properties;
      var c = f.geometry.coordinates;
      var radius = 5 + Math.min(Math.sqrt(p.area_ha || 1) * 1.4, 9);
      var marker = L.circleMarker([c[1], c[0]], {
        radius: radius,
        color: p.land_flagged ? "#e0a03c" : "#10130f",
        weight: p.land_flagged ? 2 : 1,
        fillColor: scoreColour(p.score),
        fillOpacity: 0.9
      });
      marker.bindPopup(popupHTML(p, c), { maxWidth: 320 });
      marker.addTo(sitesLayer);
      markers.push(marker);
    });

    $("site-count").textContent = visible.length + " of " + current.sites.length;
    renderList(visible);
  }

  function renderList(visible) {
    var ol = $("site-list");
    ol.innerHTML = "";
    visible.slice(0, 200).forEach(function (f, i) {
      var p = f.properties;
      var li = document.createElement("li");
      li.className = "site-item";
      li.innerHTML =
        '<span class="r">' + p.rank + "</span>" +
        "<span>" + fmt(p.elevation_m) + " m " + (p.aspect_compass || "") +
        '<br><span class="meta">' + minutesLabel(p.drive_minutes) + " drive &middot; " +
        fmt(p.hike_km, 1) + " km hike" +
        (p.land_flagged ? ' &middot; <span class="flag">' + p.land_status_label + "</span>" : "") +
        "</span></span>" +
        '<span class="s">' + p.score.toFixed(2) + "</span>";
      li.addEventListener("click", function () {
        var c = f.geometry.coordinates;
        map.setView([c[1], c[0]], Math.max(map.getZoom(), 14));
        markers[i] && markers[i].openPopup();
      });
      ol.appendChild(li);
    });
    if (!visible.length) {
      ol.innerHTML = '<li style="color:#9aa892">No sites match these filters.</li>';
    }
  }

  function bar(label, value) {
    var v = value === null || value === undefined ? 0 : value;
    return '<div class="bar-row"><span>' + label + "</span>" +
           '<span class="bar-track"><span class="bar-fill" style="width:' +
           Math.round(v * 100) + '%"></span></span><span>' + v.toFixed(2) + "</span></div>";
  }

  function popupHTML(p, coords) {
    var lat = coords[1].toFixed(5), lon = coords[0].toFixed(5);
    var html =
      '<div class="pop"><h3>Rank ' + p.rank + " &middot; " + fmt(p.elevation_m) + " m</h3>" +
      '<div class="score-line">Suitability ' + p.score.toFixed(3) + "</div>" +
      "<dl>" +
      "<dt>Aspect</dt><dd>" + (p.aspect_compass || "-") + " (" + fmt(p.aspect_deg) + "&deg;)</dd>" +
      "<dt>Slope</dt><dd>" + fmt(p.slope_deg, 1, "&deg;") + "</dd>" +
      "<dt>Vegetation</dt><dd>" + (p.veg_class || "-") + "</dd>" +
      (p.on_cutblock
        ? "<dt>Logged</dt><dd>" + p.years_since_logging + " yr ago</dd>"
        : "") +
      "<dt>NDVI</dt><dd>" + fmt(p.ndvi, 2) + "</dd>" +
      "<dt>Patch area</dt><dd>" + fmt(p.area_ha, 1, " ha") + "</dd>" +
      "<dt>Drive</dt><dd>" + minutesLabel(p.drive_minutes) + "</dd>" +
      "<dt>Hike</dt><dd>" + fmt(p.hike_km, 2, " km") + " / " + minutesLabel(p.hike_minutes) + "</dd>" +
      "<dt>iNat nearby</dt><dd>" + (p.inat_nearby || 0) +
        (p.inat_nearby_exact ? " (" + p.inat_nearby_exact + " exact)" : "") + "</dd>" +
      "</dl>" +
      '<div class="bars">' +
        bar("Terrain", p.score_terrain) +
        bar("Vegetation", p.score_vegetation) +
        bar("Access", p.score_access) +
        bar("Observations", p.score_observations) +
      "</div>";

    if (p.on_cutblock && p.years_since_logging !== null && p.years_since_logging < 45) {
      html += '<div class="flagbox"><strong>Regenerating cutblock</strong><br>' +
              "Logged " + p.years_since_logging + " years ago. Open ground here is " +
              "harvest regrowth, not natural meadow - the score is already " +
              "penalised for this.</div>";
    }

    if (p.land_flagged) {
      html += '<div class="flagbox"><strong>' + p.land_status_label + "</strong><br>" +
              "Foraging here may be restricted or prohibited. Verify tenure and " +
              "permission before harvesting.</div>";
    }

    html += '<div class="coords">' + lat + ", " + lon +
            ' &middot; <a href="https://www.google.com/maps/search/?api=1&query=' +
            lat + "," + lon + '" target="_blank" rel="noopener">open in maps</a></div></div>';
    return html;
  }

  // ---------------------------------------------------------- layer toggles
  function buildLayerToggles() {
    var box = $("layer-toggles");
    box.innerHTML = "";
    var rasters = current.manifest.raster_layers || {};

    Object.keys(rasters).forEach(function (key) {
      addToggle(box, key, rasters[key].label, null, function (on) {
        toggleRaster(key, rasters[key], on);
      });
    });

    Object.keys(VECTOR_SPECS).forEach(function (key) {
      var spec = VECTOR_SPECS[key];
      addToggle(box, key, spec.label, spec.colour, function (on) {
        toggleVector(key, spec, on);
      });
    });
  }

  function addToggle(box, key, label, colour, handler) {
    var row = document.createElement("label");
    row.className = "layer-row";
    row.setAttribute("data-key", key);
    var cb = document.createElement("input");
    cb.type = "checkbox";
    var sw = "";
    if (colour) sw = '<span class="swatch" style="background:' + colour + '"></span>';
    row.appendChild(cb);
    row.insertAdjacentHTML("beforeend",
      sw + "<span>" + label + '<span class="layer-note"></span></span>');
    cb.addEventListener("change", function () { handler(this.checked); });
    box.appendChild(row);
  }

  function toggleRaster(key, spec, on) {
    if (!on) {
      if (rasterLayers[key]) { map.removeLayer(rasterLayers[key]); delete rasterLayers[key]; }
      if (activeLegend === key) { activeLegend = null; drawLegend(); }
      return;
    }
    var opacity = Number($("f-opacity").value) / 100;
    var layer = L.imageOverlay(current.base + spec.file, current.manifest.image_bounds,
                               { opacity: opacity, interactive: false });
    layer.addTo(map);
    rasterLayers[key] = layer;
    activeLegend = key;
    drawLegend();
  }

  function toggleVector(key, spec, on) {
    if (!on) {
      if (vectorLayers[key]) { map.removeLayer(vectorLayers[key]); delete vectorLayers[key]; }
      return;
    }
    if (vectorLayers[key]) { vectorLayers[key].addTo(map); return; }
    if (vectorLoading[key]) return;

    // Vector files are fetched only when first switched on - roads alone are
    // several MB and most sessions never ask for them.
    vectorLoading[key] = true;
    setLayerBusy(key, true);
    fetchJSON(current.base + spec.file)
      .then(function (gj) {
        var layer = L.geoJSON(gj, {
          style: {
            color: spec.colour,
            weight: spec.weight || 1,
            dashArray: spec.dash || null,
            fill: !!spec.fill,
            fillColor: spec.colour,
            fillOpacity: spec.fill ? 0.16 : 0
          },
          pointToLayer: function (f, latlng) {
            return L.circleMarker(latlng, {
              radius: 4, color: spec.colour, weight: 1,
              fillColor: spec.colour, fillOpacity: 0.75
            });
          },
          onEachFeature: function (f, l) {
            var p = f.properties || {};
            if (spec.points) {
              l.bindPopup('<div class="pop"><strong>' + (p.taxon || "observation") + "</strong><br>" +
                          (p.observed_on || "") +
                          (p.url ? '<br><a href="' + p.url + '" target="_blank" rel="noopener">iNaturalist</a>' : "") +
                          "</div>");
            } else if (p.PROTECTED_LANDS_NAME || p.ROAD_NAME_FULL || p.name) {
              l.bindPopup("<strong>" + (p.PROTECTED_LANDS_NAME || p.ROAD_NAME_FULL || p.name) + "</strong>");
            }
          }
        });
        layer.addTo(map);
        vectorLayers[key] = layer;
        vectorLoading[key] = false;
        setLayerBusy(key, false);
      })
      .catch(function () {
        vectorLoading[key] = false;
        setLayerBusy(key, false, "failed to load");
      });
  }

  function setLayerBusy(key, busy, failed) {
    var row = document.querySelector('.layer-row[data-key="' + key + '"] .layer-note');
    if (!row) return;
    row.textContent = failed ? " " + failed : busy ? " loading..." : "";
  }

  function drawLegend() {
    var box = $("legend");
    if (!activeLegend || !current) { box.innerHTML = ""; return; }
    var spec = (current.manifest.raster_layers || {})[activeLegend];
    if (!spec) { box.innerHTML = ""; return; }

    var html = "<h3>" + spec.label + "</h3>";
    if (activeLegend === "vegetation" || activeLegend === "land_tenure") {
      var rows = activeLegend === "vegetation" ? VEG_LEGEND : TENURE_LEGEND;
      rows.forEach(function (r) {
        html += '<div class="row"><span class="swatch" style="background:' + r[0] + '"></span>' + r[1] + "</div>";
      });
    } else if (activeLegend === "aspect") {
      html += '<div class="bar" style="background:linear-gradient(90deg,#eb5a5a,#ebdc5a,#5ac8eb,#8c6edc,#eb5a5a)"></div>' +
              '<div class="ends"><span>N</span><span>E</span><span>S</span><span>W</span><span>N</span></div>';
    } else {
      var grad = activeLegend === "suitability"
        ? "linear-gradient(90deg,#440154,#3b528b,#21918c,#5ec962,#fde725)"
        : activeLegend === "slope" ? "linear-gradient(90deg,#ffffcc,#fdb04a,#e35a3c,#800026)"
        : activeLegend === "ndvi" ? "linear-gradient(90deg,#8c643c,#dcd28c,#5aaa46,#0a501e)"
        : "linear-gradient(90deg,#3c6e46,#96af6e,#bea578,#a0826e,#fafafc)";
      html += '<div class="bar" style="background:' + grad + '"></div>' +
              '<div class="ends"><span>' + fmt(spec.min, 2) + "</span><span>" + fmt(spec.max, 2) + "</span></div>";
    }
    box.innerHTML = html;
  }

  document.addEventListener("DOMContentLoaded", init);
})();
