from __future__ import annotations

from urllib.parse import urlencode

try:
    from datasette import hookimpl
    from datasette.utils.asgi import Response
except ImportError:  # pragma: no cover - lets project tests import helper functions.
    def hookimpl(fn):
        return fn

    class Response:  # type: ignore[no-redef]
        @classmethod
        def redirect(cls, path, status=302, headers=None):
            return {"path": path, "status": status, "headers": headers or {}}

        @classmethod
        def text(cls, body, status=200, headers=None):
            return {"body": body, "status": status, "headers": headers or {}}


WEB_MERCATOR_HALF_WORLD = 20037508.342789244
TILE_SIZE = 256
EXPORT_SIZE = f"{TILE_SIZE},{TILE_SIZE}"

ARCGIS_SERVICES = {
    "topo": "https://mapservices.gov.yk.ca/arcgis/rest/services/Yukon_Basemap_Cache/MapServer",
    "relief": "https://mapservices.gov.yk.ca/arcgis/rest/services/ShadedRelief_Cache/MapServer",
}

ATTRIBUTION = (
    'Basemap: &copy; <a href="https://yukon.ca/">Government of Yukon</a>'
)


def web_mercator_tile_bbox(z: int, x: int, y: int) -> tuple[float, float, float, float]:
    """Return an EPSG:3857 bbox for a standard XYZ Web Mercator tile."""
    if z < 0:
        raise ValueError("z must be non-negative")
    tiles_per_axis = 1 << z
    if x < 0 or y < 0 or x >= tiles_per_axis or y >= tiles_per_axis:
        raise ValueError("x/y are outside the tile range for z")

    tile_span = (WEB_MERCATOR_HALF_WORLD * 2) / tiles_per_axis
    xmin = -WEB_MERCATOR_HALF_WORLD + x * tile_span
    xmax = xmin + tile_span
    ymax = WEB_MERCATOR_HALF_WORLD - y * tile_span
    ymin = ymax - tile_span
    return xmin, ymin, xmax, ymax


def _format_bbox_value(value: float) -> str:
    return f"{value:.6f}".rstrip("0").rstrip(".")


def arcgis_export_url(service_key: str, z: int, x: int, y: int) -> str:
    """Build an ArcGIS export URL for a Leaflet-compatible 256px tile."""
    service_url = ARCGIS_SERVICES[service_key]
    bbox = ",".join(_format_bbox_value(value) for value in web_mercator_tile_bbox(z, x, y))
    query = urlencode(
        {
            "f": "image",
            "bbox": bbox,
            "bboxSR": "3857",
            "imageSR": "3857",
            "size": EXPORT_SIZE,
            "format": "png32",
            "transparent": "false",
            "dpi": "96",
        }
    )
    return f"{service_url}/export?{query}"


async def yukon_basemap_tile(request):
    """Redirect a local XYZ tile request to a Yukon ArcGIS export image."""
    vars_ = request.url_vars
    service_key = vars_["service"]
    try:
        z = int(vars_["z"])
        x = int(vars_["x"])
        y = int(vars_["y"])
        url = arcgis_export_url(service_key, z, x, y)
    except (KeyError, ValueError):
        return Response.text("Invalid Yukon basemap tile", status=404)

    return Response.redirect(
        url,
        status=302,
        headers={"Cache-Control": "public, max-age=604800"},
    )


@hookimpl
def register_routes(datasette):
    return [
        (
            r"/-/yesab-yukon-basemap/(?P<service>topo|relief)/(?P<z>\d+)/(?P<x>\d+)/(?P<y>\d+)\.png$",
            yukon_basemap_tile,
        )
    ]


@hookimpl
def extra_body_script(template, database, table, columns, view_name, request, datasette):
    """Add a simple Leaflet base-layer picker to datasette-cluster-map maps."""
    return f"""
(function () {{
  const topoUrl = "/-/yesab-yukon-basemap/topo/{{z}}/{{x}}/{{y}}.png";
  const reliefUrl = "/-/yesab-yukon-basemap/relief/{{z}}/{{x}}/{{y}}.png";
  const attribution = {ATTRIBUTION!r};

  function shouldPatchClusterMap() {{
    return String(window.DATASETTE_CLUSTER_MAP_TILE_LAYER || "").indexOf("/-/yesab-yukon-basemap/") !== -1;
  }}

  function installBasemapControl(map, L) {{
    if (!shouldPatchClusterMap() || map.__yesabYukonBasemapControl) return;
    map.__yesabYukonBasemapControl = true;

    const baseOptions = Object.assign(
      {{}},
      window.DATASETTE_CLUSTER_MAP_TILE_LAYER_OPTIONS || {{}},
      {{ attribution, maxZoom: 19 }}
    );
    let topoLayer = null;
    map.eachLayer(function (layer) {{
      if (layer && layer._url && String(layer._url).indexOf("/-/yesab-yukon-basemap/topo/") !== -1) {{
        topoLayer = layer;
      }}
    }});
    if (!topoLayer) {{
      topoLayer = L.tileLayer(topoUrl, baseOptions).addTo(map);
    }}

    const reliefLayer = L.tileLayer(reliefUrl, baseOptions);
    const noBasemapLayer = L.layerGroup();
    L.control.layers(
      {{
        "Yukon topographic": topoLayer,
        "Yukon shaded relief": reliefLayer,
        "No basemap": noBasemapLayer
      }},
      null,
      {{ collapsed: false, position: "topright" }}
    ).addTo(map);
  }}

  function patchLeaflet(L) {{
    if (!L || L.__yesabYukonBasemapPatched || !L.map) return;
    L.__yesabYukonBasemapPatched = true;
    const originalMap = L.map;
    L.map = function () {{
      const map = originalMap.apply(this, arguments);
      window.setTimeout(function () {{ installBasemapControl(map, L); }}, 0);
      return map;
    }};
  }}

  let currentLeaflet = window.L;
  try {{
    Object.defineProperty(window, "L", {{
      configurable: true,
      get: function () {{ return currentLeaflet; }},
      set: function (value) {{
        currentLeaflet = value;
        patchLeaflet(value);
      }}
    }});
  }} catch (error) {{
    // If another script already made window.L non-configurable, fall back quietly.
  }}
  patchLeaflet(currentLeaflet);
}})();
"""
