from __future__ import annotations

import contextlib
import io
import unittest
from pathlib import Path
from unittest import mock

from scripts import download_project_map_archive
from scripts import refresh_api_cache
from scripts import refresh_and_build_geopackage


class RefreshAndBuildGeoPackageTests(unittest.TestCase):
    def test_download_archive_help_does_not_check_remote_endpoint(self) -> None:
        with (
            contextlib.redirect_stdout(io.StringIO()),
            self.assertRaises(SystemExit) as raised,
            mock.patch.object(
                download_project_map_archive,
                "conditional_download",
            ) as conditional_download,
        ):
            download_project_map_archive.main(["--help"])

        self.assertEqual(raised.exception.code, 0)
        conditional_download.assert_not_called()

    def test_chains_download_api_cache_and_geopackage_build(self) -> None:
        calls: list[tuple[str, object]] = []

        with (
            contextlib.redirect_stdout(io.StringIO()),
            mock.patch.object(
                refresh_and_build_geopackage.download_project_map_archive,
                "main",
                side_effect=lambda argv: calls.append(("download", argv)),
            ),
            mock.patch.object(
                refresh_and_build_geopackage.refresh_api_cache,
                "main",
                side_effect=lambda argv: calls.append(("cache", argv)) or 0,
            ),
            mock.patch.object(
                refresh_and_build_geopackage.build_geopackage,
                "write_geopackage",
                side_effect=lambda output: calls.append(("geopackage", output))
                or {"Projects": 3},
            ),
        ):
            exit_code = refresh_and_build_geopackage.main(["out/custom.gpkg"])

        self.assertEqual(exit_code, 0)
        self.assertEqual(
            calls,
            [
                ("download", []),
                ("cache", []),
                ("geopackage", refresh_and_build_geopackage.ROOT / "out/custom.gpkg"),
            ],
        )

    def test_absolute_output_path_is_preserved(self) -> None:
        calls: list[Path] = []
        output_path = Path("C:/temp/yesab-projects.gpkg")

        with (
            contextlib.redirect_stdout(io.StringIO()),
            mock.patch.object(
                refresh_and_build_geopackage.download_project_map_archive,
                "main",
            ),
            mock.patch.object(
                refresh_and_build_geopackage.refresh_api_cache,
                "main",
                return_value=0,
            ),
            mock.patch.object(
                refresh_and_build_geopackage.build_geopackage,
                "write_geopackage",
                side_effect=lambda output: calls.append(output) or {},
            ),
        ):
            exit_code = refresh_and_build_geopackage.main([str(output_path)])

        self.assertEqual(exit_code, 0)
        self.assertEqual(calls, [output_path])

    def test_forwards_cache_refresh_arguments(self) -> None:
        with (
            contextlib.redirect_stdout(io.StringIO()),
            mock.patch.object(
                refresh_and_build_geopackage.download_project_map_archive,
                "main",
            ),
            mock.patch.object(
                refresh_and_build_geopackage.refresh_api_cache,
                "main",
                return_value=0,
            ) as cache_main,
            mock.patch.object(
                refresh_and_build_geopackage.build_geopackage,
                "write_geopackage",
                return_value={},
            ),
        ):
            exit_code = refresh_and_build_geopackage.main(
                [
                    "--years",
                    "2024",
                    "2025",
                    "--force",
                    "out/custom.gpkg",
                ]
            )

        self.assertEqual(exit_code, 0)
        cache_main.assert_called_once_with(["--years", "2024", "2025", "--force"])

    def test_refresh_api_cache_empty_argv_does_not_read_process_argv(self) -> None:
        with (
            contextlib.redirect_stdout(io.StringIO()),
            mock.patch("sys.argv", ["refresh_and_build_geopackage.py", "out.gpkg"]),
            mock.patch.object(refresh_api_cache, "bucket_specs_from_args", return_value=[]),
            mock.patch.object(refresh_api_cache, "load_state", return_value={}),
            mock.patch.object(refresh_api_cache, "sync_state_to_bucket_files"),
            mock.patch.object(
                refresh_api_cache,
                "merge_cached_buckets",
                return_value={
                    "projectCount": 0,
                    "bucketCount": 0,
                    "buckets": [],
                },
            ),
            mock.patch.object(refresh_api_cache, "save_state"),
        ):
            exit_code = refresh_api_cache.main([])

        self.assertEqual(exit_code, 0)


if __name__ == "__main__":
    unittest.main()
