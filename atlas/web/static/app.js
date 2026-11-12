/* Nomad Atlas dashboard: the few things that need JavaScript.
   No inline scripts (the page's security policy forbids them): everything
   is wired here from data- attributes. */
(function () {
  "use strict";

  function busy(text) {
    var box = document.getElementById("busy");
    if (!box) { return; }
    document.getElementById("busy-text").textContent = text || "";
    box.hidden = false;
  }

  // forms: "are you sure?" and the "working..." overlay
  document.addEventListener("submit", function (event) {
    var form = event.target;
    if (form.dataset.confirm && !window.confirm(form.dataset.confirm)) {
      event.preventDefault();
      return;
    }
    if (form.dataset.busy) { busy(form.dataset.busy); }
  });
  // coming back with the browser's Back button must not leave the overlay up
  window.addEventListener("pageshow", function () {
    var box = document.getElementById("busy");
    if (box) { box.hidden = true; }
  });

  // drag-and-drop log upload
  document.querySelectorAll(".dropzone").forEach(function (zone) {
    var input = zone.querySelector("input[type=file]");
    ["dragenter", "dragover"].forEach(function (name) {
      zone.addEventListener(name, function (event) {
        event.preventDefault();
        zone.classList.add("over");
      });
    });
    ["dragleave", "drop"].forEach(function (name) {
      zone.addEventListener(name, function () { zone.classList.remove("over"); });
    });
    zone.addEventListener("drop", function (event) {
      event.preventDefault();
      if (event.dataTransfer && event.dataTransfer.files.length) {
        input.files = event.dataTransfer.files;
        zone.requestSubmit();
      }
    });
    input.addEventListener("change", function () {
      if (input.files.length) { zone.requestSubmit(); }
    });
  });

  // field boundary: click the corners on a map
  function initDraw(box) {
    if (box.dataset.ready || typeof L === "undefined") { return; }
    box.dataset.ready = "1";
    var el = box.querySelector(".drawmap");
    var hidden = box.querySelector("input[name=drawn]");
    var count = box.querySelector(".count");
    var map = L.map(el, { attributionControl: true }).setView(
      [parseFloat(el.dataset.lat), parseFloat(el.dataset.lon)], 15);
    L.tileLayer("/tiles/{z}/{x}/{y}.png", {
      maxZoom: 19,
      attribution: "&copy; OpenStreetMap contributors"
    }).addTo(map);

    var style = { color: "#CFFF3E", weight: 3, fillOpacity: 0.15 };
    if (el.dataset.existing) {
      try {
        var current = L.geoJSON(JSON.parse(el.dataset.existing),
          { style: { color: "#8A97AD", weight: 2, dashArray: "6 6", fillOpacity: 0.05 } }).addTo(map);
        map.fitBounds(current.getBounds(), { padding: [30, 30] });
      } catch (err) { /* keep the default view */ }
    }

    var points = [];
    var shape = L.polygon([], style).addTo(map);
    var dots = L.layerGroup().addTo(map);
    function redraw() {
      shape.setLatLngs(points);
      dots.clearLayers();
      points.forEach(function (p) {
        L.circleMarker(p, { radius: 6, color: "#070B14", weight: 2, fillColor: "#CFFF3E", fillOpacity: 1 }).addTo(dots);
      });
      hidden.value = points.length ? JSON.stringify(points) : "";
      count.textContent = points.length;
    }
    map.on("click", function (event) {
      points.push([+event.latlng.lat.toFixed(7), +event.latlng.lng.toFixed(7)]);
      redraw();
    });
    box.querySelectorAll("[data-draw]").forEach(function (button) {
      button.addEventListener("click", function () {
        if (button.dataset.draw === "undo") { points.pop(); } else { points = []; }
        redraw();
      });
    });
    setTimeout(function () { map.invalidateSize(); }, 50);
  }

  function initVisibleDraws() {
    document.querySelectorAll(".drawbox").forEach(function (box) {
      if (box.offsetParent !== null) { initDraw(box); }
    });
  }
  document.querySelectorAll("details").forEach(function (details) {
    details.addEventListener("toggle", initVisibleDraws);
  });
  initVisibleDraws();
})();
