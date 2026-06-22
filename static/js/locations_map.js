document.addEventListener("DOMContentLoaded", function () {
  var params = new URLSearchParams(window.location.search);
  var kb = params.get("kb") || "";

  var map = L.map("map").setView([20, 0], 2);
  L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    attribution: "&copy; OpenStreetMap contributors",
    maxZoom: 18,
  }).addTo(map);

  fetch("/api/knowledge/locations/clusters?kb=" + encodeURIComponent(kb))
    .then(function (r) { return r.json(); })
    .then(function (data) {
      if (!data.clusters || data.clusters.length === 0) return;

      var bounds = [];
      data.clusters.forEach(function (c) {
        var radiusM = (c.eps_km || 1) * 1000;
        var circle = L.circle([c.centroid_lat, c.centroid_lon], {
          radius: radiusM,
          color: "#3b82f6",
          fillColor: "#3b82f6",
          fillOpacity: 0.2,
          weight: 2,
        }).addTo(map);

        circle.bindTooltip(c.label + " (" + c.file_count + " files)");
        circle.on("click", function () {
          var row = document.getElementById("row-" + c.id);
          if (row) {
            row.scrollIntoView({ behavior: "smooth", block: "nearest" });
            row.style.outline = "2px solid #3b82f6";
            setTimeout(function () { row.style.outline = ""; }, 1500);
          }
        });

        bounds.push([c.centroid_lat, c.centroid_lon]);
      });

      if (bounds.length > 0) {
        map.fitBounds(bounds, { padding: [40, 40] });
      }
    });
});
