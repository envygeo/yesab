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
            output = io.StringIO()

            with mock.patch.object(
                deploy_to_production,
                "git_status",
                return_value="",
            ), contextlib.redirect_stdout(output):
                exit_code = deploy_to_production.main(
                    ["--dest", str(dest), "--allow-any-dest"]
                )

            self.assertEqual(exit_code, 0)
            self.assertFalse(dest.exists())
            self.assertIn("Dry run: no files copied.", output.getvalue())
            self.assertIn("Mirror behavior:", output.getvalue())

    def test_dirty_dry_run_reports_bare_and_allow_dirty_outcomes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "yesab_map-toy-maker"
            output = io.StringIO()

            with mock.patch.object(
                deploy_to_production,
                "git_status",
                return_value=" M scripts/example.py\n",
            ), contextlib.redirect_stdout(output):
                exit_code = deploy_to_production.main(
                    ["--dest", str(dest), "--allow-any-dest"]
                )

            self.assertEqual(exit_code, 0)
            self.assertFalse(dest.exists())
            self.assertIn("Dirty checkout:", output.getvalue())
            self.assertIn("Scenario without --allow-dirty:", output.getvalue())
            self.assertIn(
                "Blocked. Bare --go will not deploy dirty changes.",
                output.getvalue(),
            )
            self.assertIn("Scenario with --allow-dirty:", output.getvalue())
            self.assertIn("Would proceed to preflight tests", output.getvalue())

    def test_go_refuses_dirty_tree_without_allow_dirty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "yesab_map-toy-maker"

            with mock.patch.object(
                deploy_to_production,
                "git_status",
                return_value=" M scripts/example.py\n",
            ), contextlib.redirect_stderr(io.StringIO()):
                exit_code = deploy_to_production.main(
                    ["--dest", str(dest), "--allow-any-dest", "--go"]
                )

            self.assertEqual(exit_code, 2)

    def test_non_default_destination_requires_explicit_override(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "yesab_map-toy-maker"

            with contextlib.redirect_stderr(io.StringIO()):
                exit_code = deploy_to_production.main(["--dest", str(dest)])

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
                        "--allow-any-dest",
                        "--go",
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
            self.assertIn(" --directory ", manifest["etl_command"])

    def test_manifest_is_not_written_when_smoke_check_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "yesab_map-toy-maker"

            with (
                contextlib.redirect_stdout(io.StringIO()),
                contextlib.redirect_stderr(io.StringIO()),
                mock.patch.object(deploy_to_production, "git_status", return_value=""),
                mock.patch.object(
                    deploy_to_production,
                    "git_commit",
                    return_value="abc123",
                ),
                mock.patch.object(deploy_to_production, "run_tests", return_value=0),
                mock.patch.object(deploy_to_production, "smoke_check", return_value=1),
            ):
                exit_code = deploy_to_production.main(
                    [
                        "--dest",
                        str(dest),
                        "--allow-any-dest",
                        "--go",
                        "--copy-engine",
                        "python",
                    ]
                )

            self.assertEqual(exit_code, 1)
            self.assertFalse((dest / "deploy_manifest.json").exists())

    def test_smoke_check_captures_help_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp)

            with mock.patch.object(
                deploy_to_production.subprocess,
                "run",
                return_value=mock.Mock(returncode=0),
            ) as run:
                exit_code = deploy_to_production.smoke_check(dest)

            self.assertEqual(exit_code, 0)
            run.assert_called_once()
            self.assertIn("--directory", run.call_args.args[0])
            self.assertIn(str(dest), run.call_args.args[0])
            self.assertTrue(run.call_args.kwargs["capture_output"])
            self.assertTrue(run.call_args.kwargs["text"])

    def test_python_mirror_deletes_stale_files_only_after_copy_success(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            stage = root / "stage"
            dest = root / "yesab_map-toy-maker"
            (stage / "new.txt").parent.mkdir(parents=True)
            (stage / "new.txt").write_text("new", encoding="utf-8")
            stale_file = dest / "stale.txt"
            stale_file.parent.mkdir(parents=True)
            stale_file.write_text("stale", encoding="utf-8")

            with mock.patch.object(
                deploy_to_production.shutil,
                "copy2",
                side_effect=OSError("copy failed"),
            ):
                with self.assertRaises(OSError):
                    deploy_to_production.mirror_with_python(stage, dest)

            self.assertTrue(stale_file.exists())


if __name__ == "__main__":
    unittest.main()
