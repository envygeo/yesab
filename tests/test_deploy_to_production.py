from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts import deploy_to_production


class DeployToProductionTests(unittest.TestCase):
    def test_dry_run_reports_plan_without_creating_destination(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "yesab_map-toy-maker"

            with mock.patch.object(
                deploy_to_production,
                "git_status",
                return_value="",
            ), contextlib.redirect_stdout(io.StringIO()):
                exit_code = deploy_to_production.main(["--dest", str(dest), "--dry-run"])

            self.assertEqual(exit_code, 0)
            self.assertFalse(dest.exists())

    def test_refuses_dirty_tree_without_allow_dirty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "yesab_map-toy-maker"

            with mock.patch.object(
                deploy_to_production,
                "git_status",
                return_value=" M scripts/example.py\n",
            ), contextlib.redirect_stderr(io.StringIO()):
                exit_code = deploy_to_production.main(["--dest", str(dest), "--dry-run"])

            self.assertEqual(exit_code, 2)

    def test_python_copy_engine_writes_manifest_and_removes_stale_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "yesab_map-toy-maker"
            stale_file = dest / "old.txt"
            stale_file.parent.mkdir(parents=True)
            stale_file.write_text("stale", encoding="utf-8")

            with (
                contextlib.redirect_stdout(io.StringIO()),
                mock.patch.object(deploy_to_production, "git_status", return_value=""),
                mock.patch.object(
                    deploy_to_production,
                    "git_commit",
                    return_value="abc123",
                ),
                mock.patch.object(
                    deploy_to_production,
                    "run_tests",
                    return_value=0,
                ),
                mock.patch.object(
                    deploy_to_production,
                    "smoke_check",
                    return_value=0,
                ),
            ):
                exit_code = deploy_to_production.main(
                    [
                        "--dest",
                        str(dest),
                        "--copy-engine",
                        "python",
                    ]
                )

            self.assertEqual(exit_code, 0)
            self.assertFalse(stale_file.exists())
            manifest = json.loads(
                (dest / "deploy_manifest.json").read_text(encoding="utf-8")
            )
            self.assertEqual(manifest["source_commit"], "abc123")
            self.assertIn("scripts/deploy_to_production.py", manifest["copied_paths"])


if __name__ == "__main__":
    unittest.main()
