from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import run_datasette_explorer as runner


class DatasetteExplorerRunnerTests(unittest.TestCase):
    def test_default_command_uses_standard_explorer_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = runner.ExplorerPaths.from_args(
                root,
                db_path=Path("out/yesab-explorer.db"),
                metadata_path=None,
                plugins_dir=Path("datasette_plugins"),
                bundle_root=Path("out/project-bundles"),
            )

            self.assertEqual(
                runner.datasette_command(paths, ["--port", "8011"]),
                [
                    "uvx",
                    "--with",
                    "datasette-cluster-map",
                    "datasette",
                    str(root / "out/yesab-explorer.db"),
                    "-m",
                    str(root / "out/yesab-explorer.metadata.json"),
                    "--plugins-dir",
                    str(root / "datasette_plugins"),
                    "--static",
                    f"bundles:{root / 'out/project-bundles'}",
                    "--port",
                    "8011",
                ],
            )

    def test_ensure_outputs_builds_only_when_needed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = runner.ExplorerPaths.from_args(
                root,
                db_path=Path("out/yesab-explorer.db"),
                metadata_path=None,
                plugins_dir=Path("datasette_plugins"),
                bundle_root=Path("out/project-bundles"),
            )

            with patch.object(runner.builder, "build_explorer") as build_explorer:
                runner.ensure_outputs(paths, rebuild=False, build_missing=True)

            build_explorer.assert_called_once_with(
                paths.db_path,
                metadata_output=paths.metadata_path,
                bundle_root=paths.bundle_root,
            )

            paths.db_path.parent.mkdir(parents=True, exist_ok=True)
            paths.metadata_path.parent.mkdir(parents=True, exist_ok=True)
            paths.db_path.write_bytes(b"sqlite")
            paths.metadata_path.write_text("{}", encoding="utf-8")

            with patch.object(runner.builder, "build_explorer") as build_explorer:
                runner.ensure_outputs(paths, rebuild=False, build_missing=True)

            build_explorer.assert_not_called()


if __name__ == "__main__":
    unittest.main()
