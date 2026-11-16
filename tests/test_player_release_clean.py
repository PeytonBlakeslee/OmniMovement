"""Ensure the player source branch contains no developer-only runtime wiring."""
from pathlib import Path
import ast
import unittest

ROOT = Path(__file__).resolve().parents[1]
MOD = ROOT / "mod" / "OmniMovement"
FORBIDDEN_MODULES = (
    "_fpstester_bridge.py", "_fpstester_native_upgrade.py",
    "profiler.py", "profiling.py", "native_capture.py",
)
FORBIDDEN_MARKERS = (
    "_fpstester", "HookProfiler", "NativeCapture(",
    "_pause_trace", "_trace_crosshair",
    "def start_profiler_session", "def snapshot_profiler",
    'identifier="debug"', 'identifier="auto_capture"',
    "_debug(", "_sprint_fire_entry_counts", "_pose_context_notice",
)


class PlayerReleaseCleanTests(unittest.TestCase):
    def test_debug_and_profiler_modules_absent(self):
        for name in FORBIDDEN_MODULES:
            with self.subTest(name=name):
                self.assertFalse((MOD / name).exists())
        self.assertFalse((MOD / "reports").exists())

    def test_runtime_source_has_no_debug_instrumentation(self):
        for path in MOD.glob("*.py"):
            data = path.read_text(encoding="utf-8-sig")
            ast.parse(data, filename=str(path))
            for marker in FORBIDDEN_MARKERS:
                with self.subTest(file=path.name, marker=marker):
                    self.assertNotIn(marker, data)

    def test_travel_handoff_omits_unused_telemetry_but_keeps_recovery_state(self):
        source = (MOD / "travel_input.py").read_text(encoding="utf-8-sig")
        for marker in ("self.samples", "self.repairs", "self.restores",
                       "self.physical_seen", "self.no_device_samples"):
            with self.subTest(marker=marker):
                self.assertNotIn(marker, source)
        for marker in ("self.last_sample_at", "self.last_source", "self.last_physical",
                       "self._axis_owners", "self._pending_recovery", "self.last_error"):
            with self.subTest(marker=marker):
                self.assertIn(marker, source)

    def test_native_backend_has_no_developer_counters_or_stats_export(self):
        source = (ROOT / "native" / "omni_precision_backend.cpp").read_text(
            encoding="utf-8-sig")
        adapter = (MOD / "precision_native_adapter.py").read_text(
            encoding="utf-8-sig")
        for marker in (
            'py::dict stats()', 'mod.def("stats"',
            "skip_invalid_context", "motion_fusion_scans",
            "native_sprint_reads", "analog_partial",
        ):
            with self.subTest(marker=marker):
                self.assertNotIn(marker, source)
        self.assertNotIn(".stats()", adapter)
        self.assertNotIn("native Precision stats at reset", adapter)
        # These are production state and failure guards, not diagnostics.
        for marker in ("read_failures", "write_failures", "travel_epoch", "quarantined"):
            self.assertIn(marker, source)

    def test_gameplay_handlers_and_native_backend_still_present(self):
        data = (MOD / "__init__.py").read_text(encoding="utf-8-sig")
        for handler in (
            "AdaptiveFirePose", "def _assist_crouch_changed(",
            "def _zero_execute_cleanup_completed(",
            "def _zero_execute_try_early_handoff(",
            "def _skill_assist_context(",
            "ZERO_EXECUTE_PHYSICS_RESTORE",
        ):
            with self.subTest(handler=handler):
                self.assertIn(handler, data)
        self.assertTrue((MOD / "precision_native_adapter.py").is_file())


if __name__ == "__main__":
    unittest.main()
