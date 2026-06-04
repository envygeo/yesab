# YESAB API location fallback flow

This note describes how cached YESAB registry locations become approximate map
points, and where coordinate classification happens.

## Cache refresh

`scripts/refresh_api_cache.py` does not generate map points or classify
coordinates. It fetches YESAB registry records, normalizes the fields consumed by
downstream builders, and preserves the API `locations` array in each project.

Important retained fields include:

- `projectId`
- `projectNumber`
- `title`
- `projectTypeName`
- `proponentName`
- `assessmentDistricts`
- `sectors`
- `projectScope`
- `stage`
- `stageHistory`
- `outcomes`
- `locations`

Fetched buckets are written under `data/api/buckets/`. The merged,
deduplicated dataset is written to:

```text
data/api/projects_merged.json.zst
```

During merging, records are deduplicated with this key:

```python
record.get("projectId") or record.get("projectNumber")
```

If the same project appears in multiple cached buckets, the copy from the bucket
with the newest `cachedAt` value wins.

## When API locations become map points

The map builders load `data/api/projects_merged.json.zst` through
`yesab_map.core.load_api_projects()`, keyed by `projectNumber`.

The builders first try to join API projects to shapefile geometry by project
number. API coordinates are only used for projects that do not already have
shapefile geometry.

Those unmatched API projects are rendered in the fallback layer:

```text
API_Approximate_Points
```

## Choosing a project location

Fallback point creation happens in:

```python
yesab_map.core.api_fallback_feature()
```

For each unmatched API project, the function:

1. Requires a non-empty `projectNumber`.
2. Iterates through `project["locations"]`.
3. Skips non-dictionary location entries.
4. Skips entries without both `latitude` and `longitude`.
5. Converts latitude and longitude to floats.
6. Uses the first valid coordinate pair.
7. Returns `None` if no usable coordinate pair exists.

The current logic does not average multiple locations and does not calculate a
centroid.

## Coordinate classification

Coordinate classification happens in:

```python
yesab_map.core.classify_api_coordinate(latitude, longitude, coordinate_count)
```

Classification is based on the source API coordinate before any display
fallback is applied.

Rules are applied in this order:

| Condition | Class | Flag |
| --- | --- | --- |
| Outside valid world lat/lon range | `bad_coordinates` | `outside_world_range` |
| Outside broad Yukon range | `bad_coordinates` | `outside_yukon_range` |
| Same rounded coordinate used by 5 or more unmatched projects | `generic_coordinates` | `repeated_coordinate_5plus` |
| Longitude near `-141.00001`, `-140.00001`, or `-124.00001` | `generic_coordinates` | `sentinel_like_longitude` |
| Latitude and longitude both near whole numbers | `generic_coordinates` | `near_integer_coordinate` |
| Latitude or longitude has 2 or fewer decimal places | `low_precision_coordinates` | `low_precision_2dp` |
| None of the above | `plausible_api_coordinates` | none |

The broad Yukon range is:

```text
latitude:  59.0 to 70.5
longitude: -142.5 to -123.0
```

## Overrides and display fallback

Manual coordinate replacements are loaded from:

```text
data/api/location_overrides.csv
```

Overrides are keyed by:

```python
(projectNumber, projectId)
```

If an override exists, the map point uses the override coordinate and records:

```text
locationCoordinateOverride = location_overrides.csv
```

If the source coordinate is classified as `bad_coordinates` and there is no
manual override, the point is placed at the display fallback coordinate:

```text
latitude:  65.0
longitude: -127.0
```

and records:

```text
locationCoordinateOverride = bad_coordinate_display_fallback
```

Important nuance: the coordinate class still describes the original API
coordinate. For example, a project with a bad API coordinate and a good manual
override can still have:

```text
locationCoordinateClass = bad_coordinates
locationCoordinateOverride = location_overrides.csv
```

## Point generation

After selecting the map latitude and longitude, the fallback helper projects the
coordinate to Yukon Albers with:

```python
project_lonlat_to_yukon_albers(map_longitude, map_latitude)
```

The emitted feature uses a GeoJSON-like shape:

```python
{
    "geometry": {
        "type": "Point",
        "coordinates": [x, y],
    },
    "apiProjectNumber": project_number,
    "isApiFallback": True,
}
```

Feature properties include both the mapped coordinate and the original source
coordinate:

```python
"latitude": str(map_latitude)
"longitude": str(map_longitude)
"sourceLatitude": str(source_latitude)
"sourceLongitude": str(source_longitude)
```

## End-to-end flow

```text
scripts/refresh_api_cache.py
  fetches API records
  preserves locations[]
  writes data/api/projects_merged.json.zst

static map builder
  joins API records to shapefile geometry by projectNumber
  finds API projects without shapefile geometry
  calls yesab_map.core.api_fallback_feature()

api_fallback_feature()
  picks the first valid locations[] lat/lon
  classifies the source coordinate
  applies a manual override or bad-coordinate display fallback when needed
  projects lon/lat to Yukon Albers
  emits an API_Approximate_Points feature
```
