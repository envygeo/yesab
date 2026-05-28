from __future__ import annotations

import base64
import gzip
import json
import re
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

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
            "format_file_size",
            "label_for",
            "latest_api_record_info",
            "latest_api_record_summary",
            "load_api_location_overrides",
            "load_api_projects",
            "load_source_info",
            "project_number_for",
            "qa_project_summary",
            "round_coord",
            "source_date_summary",
            "total_path_size",
        )

        for name in helper_names:
            with self.subTest(name=name):
                self.assertIs(getattr(build_static_map_single, name), getattr(core, name))
                self.assertIs(getattr(build_static_map_split, name), getattr(core, name))


class BuildStatsTests(unittest.TestCase):
    def test_format_file_size_uses_compact_binary_units(self) -> None:
        self.assertEqual(core.format_file_size(512), "512 bytes")
        self.assertEqual(core.format_file_size(1536), "1.5 KB")
        self.assertEqual(core.format_file_size(2 * 1024 * 1024), "2.0 MB")

    def test_total_path_size_counts_files_recursively(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "index.html").write_text("12345", encoding="utf-8")
            (root / "data").mkdir()
            (root / "data" / "layer.js").write_text("1234567", encoding="utf-8")

            self.assertEqual(core.total_path_size(root), 12)

    def test_source_date_summary_reports_existing_map_file_date_only(self) -> None:
        self.assertEqual(
            core.source_date_summary(
                {"shapefile": {"sourceDate": "2026-05-11 12:26 YST"}}
            ),
            "YESAB shapefile date: 2026-05-11 12:26 YST",
        )
        self.assertEqual(core.source_date_summary({}), "")

    def test_latest_api_record_summary_uses_latest_stage_history_timestamp(self) -> None:
        self.assertEqual(
            core.latest_api_record_summary(
                {
                    "2026-0003": {
                        "projectNumber": "2026-0003",
                        "stageHistory": [{"stageStart": 1775169791865}],
                    },
                    "2026-0004": {
                        "projectNumber": "2026-0004",
                        "stageHistory": [{"stageStart": 1776783773483}],
                    },
                    "legacy": {"projectNumber": "legacy"},
                }
            ),
            "Latest registry change: 2026-04-21 08:02 YST (2026-0004)",
        )
        self.assertEqual(core.latest_api_record_summary({}), "")

    def test_latest_api_record_info_returns_structured_date_and_number(self) -> None:
        self.assertEqual(
            core.latest_api_record_info(
                {
                    "2026-0004": {
                        "projectNumber": "2026-0004",
                        "stageHistory": [
                            {
                                "stageName": "Draft",
                                "stageStart": 1776783773483,
                            }
                        ],
                    },
                }
            ),
            {
                "date": "2026-04-21 08:02 YST",
                "event": "started",
                "projectNumber": "2026-0004",
                "stageName": "Draft",
            },
        )


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


class StaticMapCompressedOutputTests(unittest.TestCase):
    def test_compressed_wrapper_embeds_recoverable_app_html(self) -> None:
        source_html = "<!doctype html><html><body><h1>YESAB</h1><script>window.ok = true;</script></body></html>"

        wrapper = build_static_map_single.build_compressed_html(source_html)

        self.assertIn('DecompressionStream("gzip")', wrapper)
        self.assertIn("document.write(appHtml)", wrapper)
        payload_match = re.search(
            r"const COMPRESSED_APP_BASE64 = ([^;]+);", wrapper
        )
        self.assertIsNotNone(payload_match)
        encoded_payload = json.loads(payload_match.group(1))
        recovered_html = gzip.decompress(base64.b64decode(encoded_payload)).decode(
            "utf-8"
        )
        self.assertEqual(recovered_html, source_html)
        self.assertLess(len(encoded_payload), len(source_html) * 2)

    def test_default_compressed_path_sits_next_to_single_file_output(self) -> None:
        self.assertEqual(
            build_static_map_single.default_compressed_output_path(
                Path("out/yesab-map-in-one.html")
            ),
            Path("out/yesab-map-in-one.compressed.html"),
        )


class StaticMapAboutPanelTests(unittest.TestCase):
    def test_about_panel_includes_latest_api_stage_date_label(self) -> None:
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
        js = build_static_map_split.site_js()

        self.assertIn("Latest registry change", html)
        self.assertIn("latestRecordProjectNumber", html)
        self.assertIn("Latest registry change", js)
        self.assertIn("latestRecordProjectNumber", js)

    def test_about_panel_includes_qa_coverage_summary(self) -> None:
        payload = {
            "archives": [],
            "bounds": [0, 0, 1, 1],
            "layers": [],
            "apiProjects": {},
            "apiSummary": {"available": True},
            "sourceInfo": {},
            "qa": {
                "summary": {
                    "cachedApiProjectCount": 10,
                    "matchedApiProjectCount": 3,
                    "fallbackApiProjectCount": 2,
                    "mappedApiProjectCount": 5,
                    "unmappedApiProjectCount": 5,
                    "matchedFeatureCount": 7,
                }
            },
        }

        html = build_static_map_single.build_html(payload)
        split_js = build_static_map_split.site_js()

        self.assertIn("Data QA coverage", html)
        self.assertIn("matched API project(s) with shapefile geometry", html)
        self.assertIn("API-only fallback point project(s)", html)
        self.assertIn("cached API project(s) still unmapped", html)
        self.assertIn('"yesab-map-in-one.qa.html"', html)
        self.assertIn('"yesab-map-in-one.qa.json"', html)
        self.assertIn("HTML report", html)
        self.assertIn("JSON data", html)
        self.assertIn('"mappedApiProjectCount":5', html)
        self.assertIn("Data QA coverage", split_js)
        self.assertIn("matched API project(s) with shapefile geometry", split_js)

    def test_split_manifest_includes_qa_summary_for_about_panel(self) -> None:
        with TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            build_static_map_split.write_data_files(
                {
                    "archives": [],
                    "bounds": [0, 0, 1, 1],
                    "layers": [],
                    "apiProjects": {},
                    "apiSummary": {"available": False},
                    "sourceInfo": {},
                    "qa": {
                        "summary": {
                            "cachedApiProjectCount": 10,
                            "matchedApiProjectCount": 3,
                            "fallbackApiProjectCount": 2,
                            "mappedApiProjectCount": 5,
                            "unmappedApiProjectCount": 5,
                            "matchedFeatureCount": 7,
                        }
                    },
                },
                data_dir,
            )

            manifest_js = (data_dir / "manifest.js").read_text(encoding="utf-8")

        self.assertIn('"qaSummary"', manifest_js)
        self.assertIn('"mappedApiProjectCount":5', manifest_js)


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
        self.assertIn('accept=".kml,.shp,.dbf,.prj"', html)
        self.assertIn("parseKmlDocument", html)
        self.assertIn("readShp", html)
        self.assertIn("readDbf", html)
        self.assertIn("projectionModeForPrj", html)
        self.assertIn("shouldProjectShpBounds", html)
        self.assertIn('byLowerName.get(`${stem.toLowerCase()}.prj`)', html)
        self.assertIn("isLikelyLonLatBounds", html)
        self.assertIn("projectLonLatToYukonAlbers(point[0], point[1])", html)
        self.assertIn("DATA.layers.push(layer)", html)
        self.assertIn('archive: "local device"', html)

    def test_split_map_includes_same_local_shape_and_kml_importer(self) -> None:
        html = build_static_map_split.site_html([], include_api_projects=False)
        css = build_static_map_split.site_css()
        js = build_static_map_split.site_js()

        self.assertIn('id="localFileInput"', html)
        self.assertIn('accept=".kml,.shp,.dbf,.prj"', html)
        self.assertIn(".local-import", css)
        self.assertIn("parseKmlDocument", js)
        self.assertIn("readShp", js)
        self.assertIn("readDbf", js)
        self.assertIn("projectionModeForPrj", js)
        self.assertIn("shouldProjectShpBounds", js)
        self.assertIn('byLowerName.get(`${stem.toLowerCase()}.prj`)', js)
        self.assertIn("isLikelyLonLatBounds", js)
        self.assertIn("projectLonLatToYukonAlbers(point[0], point[1])", js)
        self.assertIn("DATA.layers.push(layer)", js)
        self.assertIn('archive: "local device"', js)


if __name__ == "__main__":
    unittest.main()
