from __future__ import annotations

import unittest

from scripts import build_static_map_single
from scripts import build_static_map_split
from yesab_map import core


class ProjectFeatureJoinTests(unittest.TestCase):
    def test_project_number_uses_configured_field_precedence(self) -> None:
        record = {
            "ProjectID": " 2024-001 ",
            "Prj_ID": "2024-002",
            "YESAB_PROJ": "2024-003",
            "Number": "2024-004",
        }

        self.assertEqual(core.project_number_for(record), "2024-001")

    def test_project_number_accepts_api_project_number_field(self) -> None:
        record = {"projectNumber": " 2025-009 ", "ProjectID": "2024-001"}

        self.assertEqual(core.project_number_for(record), "2025-009")

    def test_clean_props_strips_values_and_drops_blank_fields(self) -> None:
        self.assertEqual(
            core.clean_props({"ProjectID": " 2024-001 ", "Notes": "   "}),
            {"ProjectID": "2024-001"},
        )

    def test_label_prefers_project_name_then_other_known_fields(self) -> None:
        self.assertEqual(
            core.label_for({"PROPERTY_N": "  Site label "}, "fallback"),
            "Site label",
        )
        self.assertEqual(core.label_for({}, "fallback"), "fallback")


class ApiCoordinateTests(unittest.TestCase):
    def test_classifies_plausible_yukon_coordinate(self) -> None:
        self.assertEqual(
            core.classify_api_coordinate(60.72123, -135.05682, 1),
            ("plausible_api_coordinates", []),
        )

    def test_classifies_low_precision_coordinate(self) -> None:
        self.assertEqual(
            core.classify_api_coordinate(60.72, -135.06, 1),
            ("low_precision_coordinates", ["low_precision_2dp"]),
        )

    def test_classifies_repeated_sentinel_coordinate_as_generic(self) -> None:
        coordinate_class, flags = core.classify_api_coordinate(65.0, -141.00001, 5)

        self.assertEqual(coordinate_class, "generic_coordinates")
        self.assertEqual(
            flags,
            [
                "repeated_coordinate_5plus",
                "sentinel_like_longitude",
                "near_integer_coordinate",
            ],
        )

    def test_classifies_non_yukon_world_coordinate_as_bad(self) -> None:
        self.assertEqual(
            core.classify_api_coordinate(49.2827, -123.1207, 1),
            ("bad_coordinates", ["outside_yukon_range"]),
        )


class ApiFallbackFeatureTests(unittest.TestCase):
    def test_builds_api_fallback_feature_from_first_valid_location(self) -> None:
        project = {
            "projectNumber": "2024-001",
            "projectId": "abc-123",
            "title": "Access Road",
            "projectTypeName": "Designated Office Evaluation",
            "proponentName": "Example Co",
            "stage": {"name": "Screening"},
            "locations": [
                {"latitude": None, "longitude": -135.0},
                {"latitude": "60.72123", "longitude": "-135.05682"},
            ],
        }

        feature = core.api_fallback_feature(project, 7, {}, {})

        self.assertIsNotNone(feature)
        assert feature is not None
        self.assertEqual(feature["id"], 7)
        self.assertEqual(feature["apiProjectNumber"], "2024-001")
        self.assertTrue(feature["isApiFallback"])
        self.assertEqual(feature["label"], "Access Road")
        self.assertEqual(feature["properties"]["locationCoordinateClass"], "plausible_api_coordinates")

    def test_bad_api_coordinates_use_display_fallback_but_keep_source_values(self) -> None:
        project = {
            "projectNumber": "2024-002",
            "projectId": "bad-location",
            "title": "Bad coordinate project",
            "stage": {"name": "Decision"},
            "locations": [{"latitude": "49.2827", "longitude": "-123.1207"}],
        }

        feature = core.api_fallback_feature(project, 1, {}, {})

        self.assertIsNotNone(feature)
        assert feature is not None
        properties = feature["properties"]
        self.assertEqual(properties["locationCoordinateClass"], "bad_coordinates")
        self.assertEqual(properties["locationCoordinateOverride"], "bad_coordinate_display_fallback")
        self.assertEqual(properties["latitude"], "65.0")
        self.assertEqual(properties["longitude"], "-127.0")
        self.assertEqual(properties["sourceLatitude"], "49.2827")
        self.assertEqual(properties["sourceLongitude"], "-123.1207")

    def test_location_override_replaces_map_coordinate(self) -> None:
        project = {
            "projectNumber": "2024-003",
            "projectId": "needs-override",
            "title": "Override project",
            "locations": [{"latitude": "49.2827", "longitude": "-123.1207"}],
        }

        feature = core.api_fallback_feature(
            project,
            1,
            {},
            {("2024-003", "needs-override"): (60.7, -135.1)},
        )

        self.assertIsNotNone(feature)
        assert feature is not None
        properties = feature["properties"]
        self.assertEqual(properties["locationCoordinateClass"], "bad_coordinates")
        self.assertEqual(properties["locationCoordinateOverride"], "location_overrides.csv")
        self.assertEqual(properties["latitude"], "60.7")
        self.assertEqual(properties["longitude"], "-135.1")


class QaSummaryTests(unittest.TestCase):
    def test_qa_project_summary_is_compact_and_stable(self) -> None:
        project = {
            "projectNumber": "2024-004",
            "projectId": "summary-id",
            "title": "Summary Project",
            "projectTypeName": "Type",
            "proponentName": "Proponent",
            "stage": {"name": "Complete"},
            "assessmentDistricts": [{"name": "Dawson"}],
            "sectors": [{"name": "Mining"}],
            "locations": [{}, {}],
        }

        self.assertEqual(
            core.qa_project_summary(project),
            {
                "projectNumber": "2024-004",
                "projectId": "summary-id",
                "title": "Summary Project",
                "projectTypeName": "Type",
                "proponentName": "Proponent",
                "stageName": "Complete",
                "districts": ["Dawson"],
                "sectors": ["Mining"],
                "locationCount": 2,
            },
        )


class BuilderSharedHelperTests(unittest.TestCase):
    def test_builders_use_the_shared_core_helpers_for_join_and_qa_behavior(self) -> None:
        helper_names = (
            "api_fallback_feature",
            "clean_props",
            "label_for",
            "load_api_location_overrides",
            "load_api_projects",
            "load_source_info",
            "project_number_for",
            "qa_project_summary",
            "round_coord",
        )

        for name in helper_names:
            with self.subTest(name=name):
                self.assertIs(getattr(build_static_map_single, name), getattr(core, name))
                self.assertIs(getattr(build_static_map_split, name), getattr(core, name))


class StaticMapBasemapChooserTests(unittest.TestCase):
    def test_single_file_map_includes_opt_in_basemap_chooser(self) -> None:
        html = build_static_map_single.build_html(
            {
                "archives": [],
                "bounds": [0, 0, 1, 1],
                "layers": [],
                "apiProjects": {},
                "apiSummary": {"available": False},
                "sourceInfo": {},
                "qa": {},
            }
        )

        self.assertIn('id="basemapSelect"', html)
        self.assertIn("No basemap (self-contained)", html)
        self.assertIn("mapservices.gov.yk.ca/arcgis/rest/services/Yukon_Basemap_Cache/MapServer/tile", html)
        self.assertIn("mapservices.gov.yk.ca/arcgis/rest/services/ShadedRelief_Cache/MapServer/tile", html)
        self.assertIn("tileInfo", html)
        self.assertIn('basemap: basemapSelect.value || "none"', html)
        self.assertNotIn("World_Topo_Map/MapServer/export", html)

    def test_split_map_includes_same_opt_in_basemap_chooser(self) -> None:
        html = build_static_map_split.site_html([], include_api_projects=False)
        css = build_static_map_split.site_css()
        js = build_static_map_split.site_js()

        self.assertIn('id="basemapSelect"', html)
        self.assertIn(".basemap-control", css)
        self.assertIn("No basemap (self-contained)", html)
        self.assertIn("mapservices.gov.yk.ca/arcgis/rest/services/Yukon_Basemap_Cache/MapServer/tile", js)
        self.assertIn("mapservices.gov.yk.ca/arcgis/rest/services/ShadedRelief_Cache/MapServer/tile", js)
        self.assertIn("tileInfo", js)
        self.assertIn('basemap: basemapSelect.value || "none"', js)
        self.assertNotIn("World_Topo_Map/MapServer/export", js)


class StaticMapLocalImportTests(unittest.TestCase):
    def test_single_file_map_includes_local_shape_and_kml_importer(self) -> None:
        html = build_static_map_single.build_html(
            {
                "archives": [],
                "bounds": [0, 0, 1, 1],
                "layers": [],
                "apiProjects": {},
                "apiSummary": {"available": False},
                "sourceInfo": {},
                "qa": {},
            }
        )

        self.assertIn('id="localFileInput"', html)
        self.assertIn('accept=".kml,.shp,.dbf"', html)
        self.assertIn("parseKmlDocument", html)
        self.assertIn("readShp", html)
        self.assertIn("readDbf", html)
        self.assertIn("DATA.layers.push(layer)", html)
        self.assertIn('archive: "local device"', html)

    def test_split_map_includes_same_local_shape_and_kml_importer(self) -> None:
        html = build_static_map_split.site_html([], include_api_projects=False)
        css = build_static_map_split.site_css()
        js = build_static_map_split.site_js()

        self.assertIn('id="localFileInput"', html)
        self.assertIn('accept=".kml,.shp,.dbf"', html)
        self.assertIn(".local-import", css)
        self.assertIn("parseKmlDocument", js)
        self.assertIn("readShp", js)
        self.assertIn("readDbf", js)
        self.assertIn("DATA.layers.push(layer)", js)
        self.assertIn('archive: "local device"', js)


if __name__ == "__main__":
    unittest.main()
