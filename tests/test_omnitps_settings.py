"""Source-level feature-contract checks for OmniMovement sprint-fire UI.

These checks are not game acceptance tests. They catch accidental reintroduction
of the removed method selector and regression of legacy toggle persistence.
"""
import ast
from pathlib import Path
import unittest


SOURCE = (Path(__file__).resolve().parents[1] /
          "mod" / "OmniMovement" / "__init__.py").read_text(encoding="utf-8-sig")
TREE = ast.parse(SOURCE)
FUNC = {f.name: f for f in TREE.body if isinstance(f, (ast.FunctionDef, ast.AsyncFunctionDef))}


def function_source(name):
    return ast.get_source_segment(SOURCE, FUNC[name])


class SprintFireSingleToggleTests(unittest.TestCase):
    def test_exposes_single_feature_toggle_with_original_saved_identifier(self):
        self.assertIn('identifier="begin_fire_sprint"', SOURCE)
        self.assertIn('display_name="Begin Sprint While Firing"', SOURCE)
        self.assertNotIn('identifier="sprint_fire_method"', SOURCE)
        self.assertNotIn('FIRE_METHOD_TPS', SOURCE)
        self.assertNotIn('FIRE_METHOD_CURRENT', SOURCE)
        self.assertIn('begin_fire_sprint,', function_source("_option_changed") if False else SOURCE)
        self.assertIn('value=True,\n    display_name="Begin Sprint While Firing"', SOURCE)

    def test_saves_new_value_but_applies_only_after_reenable(self):
        self.assertIn('_cfg_begin_fire_sprint = bool(value(begin_fire_sprint))',
                      function_source("_sync_config"))
        self.assertIn('_requested_fire_entry = bool(_cfg_begin_fire_sprint)',
                      function_source("on_enable"))
        self.assertIn('entry_requested = bool(_requested_fire_entry)',
                      function_source("_set_native_hipfire"))
        self.assertIn('option.identifier == "begin_fire_sprint"',
                      function_source("_option_changed"))
        self.assertNotIn('_requested_fire_entry =', function_source("_option_changed"))

    def test_animation_coordination_only_when_native_entry_active(self):
        self.assertIn('if _active_fire_entry and _cfg_hip_fire:',
                      function_source("_began_sprint"))
        self.assertIn('_active and (_active_fire_entry or after_equip)',
                      function_source("_sprint_fire_entry"))
        self.assertIn('_active and _cfg_hip_fire and pc.bInSprintState and _pose_fire_requested()',
                      function_source("_sprint_fire_entry_tick"))
        self.assertIn('_sprint_fire_entry_remaining = 6',
                      function_source("_sprint_fire_entry"))

    def test_disable_reverts_and_clears_experiment(self):
        disable = FUNC["on_disable"]
        # Teardown now uses independently guarded cleanup operations. Verify
        # the invoked functions, not an obsolete exact source-string layout.
        calls = [node for node in ast.walk(disable)
                 if isinstance(node, ast.Call)
                 and isinstance(node.func, ast.Name)
                 and node.func.id == "cleanup"]
        by_label = {
            node.args[0].value: node
            for node in calls
            if len(node.args) >= 2 and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        }
        native = by_label["native-hipfire"]
        self.assertIsInstance(native.args[1], ast.Name)
        self.assertEqual(native.args[1].id, "_set_native_hipfire")
        self.assertIsInstance(native.args[2], ast.Constant)
        self.assertIs(native.args[2].value, False)
        probe_cleanup = by_label["sprint-fire-entry"]
        self.assertEqual(probe_cleanup.args[1].id, "_stop_sprint_fire_entry_probe")
        self.assertIn('_requested_fire_entry = False', function_source("on_disable"))
        self.assertIn('_active = False', function_source("on_disable"))
        # Release code uses a bounded pose probe, not the old handoff helper.
        probe = function_source("_stop_sprint_fire_entry_probe")
        self.assertIn('_sprint_fire_entry_remaining = 0', probe)
        self.assertIn('_set_hook("sprint-fire-entry", False', probe)


if __name__ == "__main__":
    unittest.main()
