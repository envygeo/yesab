from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class MetricsScriptTests(unittest.TestCase):
    def test_run_timed_records_command_result(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "command_runs.jsonl"
            result = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "run_timed.py"),
                    "--task-id",
                    "demo",
                    "--log",
                    str(log),
                    "--",
                    sys.executable,
                    "-c",
                    "print('ok')",
                ],
                check=False,
                text=True,
                capture_output=True,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            row = json.loads(log.read_text(encoding="utf-8").strip())
            self.assertEqual(row["task_id"], "demo")
            self.assertEqual(row["exit_code"], 0)
            self.assertGreaterEqual(row["elapsed_seconds"], 0)

    def test_log_agent_session_and_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            metrics_dir = Path(tmp)
            session_log = metrics_dir / "agent_sessions.jsonl"
            session_result = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "log_agent_session.py"),
                    "--log",
                    str(session_log),
                    "--task-id",
                    "demo",
                    "--session-id",
                    "s1",
                    "--model",
                    "gpt-5.5",
                    "--reasoning",
                    "medium",
                    "--model-fit",
                    "appropriate",
                    "--model-fit-notes",
                    "needed cross-file reasoning",
                    "--input-tokens",
                    "100",
                    "--cached-input-tokens",
                    "40",
                    "--output-tokens",
                    "10",
                    "--reasoning-output-tokens",
                    "2",
                    "--commit",
                    "abc123",
                    "--test",
                    "unit tests passed",
                    "--failure",
                    "first command failed, retried with fixed path",
                    "--notes",
                    "demo row",
                ],
                check=False,
                text=True,
                capture_output=True,
            )

            self.assertEqual(session_result.returncode, 0, session_result.stderr)
            row = json.loads(session_log.read_text(encoding="utf-8").strip())
            self.assertEqual(row["non_cached_input_tokens"], 60)
            self.assertEqual(row["model_fit"], "appropriate")
            self.assertEqual(row["commits"], ["abc123"])

            summary_result = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "summarize_metrics.py"),
                    "--metrics-dir",
                    str(metrics_dir),
                ],
                check=False,
                text=True,
                capture_output=True,
            )

            self.assertEqual(summary_result.returncode, 0, summary_result.stderr)
            self.assertIn("agent_sessions: 1", summary_result.stdout)
            self.assertIn("gpt-5.5 / medium: 1", summary_result.stdout)
            self.assertIn("appropriate: 1", summary_result.stdout)


if __name__ == "__main__":
    unittest.main()
