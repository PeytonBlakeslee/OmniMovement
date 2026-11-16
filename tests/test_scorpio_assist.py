"""Source-level and isolated runtime regressions for Axton's Scorpio Sprint Assist.

Use no BL2 UObject or native backend: test the actual Python helper/input
handler under minimal fake objects. Only verified Scorpio contexts are admitted.
"""
import ast
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
import unittest

SOURCE = (Path(__file__).resolve().parents[1] /
          "mod" / "OmniMovement" / "__init__.py").read_text(encoding="utf-8-sig")
TREE = ast.parse(SOURCE)


def isolated_scope():
    names = {"_pressed", "_skill_assist_context", "_assist_context"}
    functions = [item for item in TREE.body
                 if isinstance(item, ast.FunctionDef) and item.name in names]
    assert len(functions) == len(names), "Shared skill context/handler missing"
    scope = {}
    exec(compile(ast.Module(body=functions, type_ignores=[]),
                 "<omni-scorpio-assist>", "exec"), scope)
    return scope


def method(name):
    return next(x for x in TREE.body if isinstance(x, ast.FunctionDef)
                and x.name == name)


class FakePc:
    def __init__(self):
        self.PlayerInput = SimpleNamespace(CurrentInputContextName="Scorpio")
        self.PlayerClass = SimpleNamespace(InputContexts=[SimpleNamespace(ContextName="Scorpio")])
        self.Pawn = SimpleNamespace(Controller=self, DrivenVehicle=None, Physics=1,
                                    MyActionSkill=object(), IsActionSkillRunning=lambda: True)
        self.bInSprintState = True
        self.bWantsToSprint = True
        self.bPressedSprint = True
        self.end_calls = 0

    def EndSprint(self):
        self.end_calls += 1
        self.bInSprintState = False


class ScorpioAssistTests(unittest.TestCase):
    def setUp(self):
        self.pc = FakePc()
        self.s = isolated_scope()
        self.block = object()
        self.writes = []
        self.retry = []
        self.s.update({
            "_active": True,
            "_cfg_assist": True,
            "_cfg_omni": True,
            "_cfg_native_precision": False,
            "_cfg_debug": False,
            "_deathtrap_skill_active": False,
            "_armed": True,
            "_assist_press_held": False,
            "_travel_pending": False,
            "_travel_initial_rearm_at": 1.0,
            "_travel_mode": True,
            "_travel_epoch": 0,
            "_gate_sprinting": True,
            "_assist_airborne_hold": False,
            "Block": self.block,
            "_pc": lambda: self.pc,
            "_local_input": lambda obj: obj,
            "_same": lambda a, b: a is b,
            # Mock firing_pose_context only when the owned deployed skill
            # context is valid. All ordinary (strict) Scorpio gates stay closed.
            "_basic_context": lambda *args, **kw: (
                kw.get("fire_pose_only", False) and
                self.pc.Pawn.Controller is self.pc and
                any(x.ContextName == self.pc.PlayerInput.CurrentInputContextName
                    for x in self.pc.PlayerClass.InputContexts)
            ) or self.pc.PlayerInput.CurrentInputContextName == "DefaultOnFoot",
            "_precision": SimpleNamespace(
                intent_press=lambda *args, **kw: (not args[3], True)),
            "_debug": lambda *args: None,
            "_assist_raw_moving": lambda *args, **kw: True,
            "_set_try_sprint": lambda pi, value: self.writes.append(value),
            "_assist_recover_or_schedule": lambda pc: self.retry.append(pc),
            "_hold_assist_through_airborne": lambda *args: False,
            "_set_hook": lambda *args: None,
            "_stop_crosshair_resync": lambda: None,
            "_stop_assist_recovery": lambda: None,
            "_stop_travel_recovery": lambda *args: None,
            "_clear_direction": lambda: None,
            "_sync_sprint_hooks": lambda *args: None,
            "INPUT_ALLOWS_SPRINT": "InputAllowsSprint",
            "Type": SimpleNamespace(PRE="PRE"),
            "_assist_airborne_gate": lambda *args: None,
            "unrealsdk": SimpleNamespace(
                hooks=SimpleNamespace(prevent_hooking_direct_calls=nullcontext)),
        })

    def _press(self):
        return self.s["_pressed"](self.pc.PlayerInput, None, None, None)

    def test_scoped_context_requires_live_turret_owner(self):
        self.assertTrue(self.s["_skill_assist_context"](
            self.pc, self.pc.PlayerInput))
        self.assertTrue(self.s["_assist_context"](self.pc))
        self.pc.Pawn.DrivenVehicle = object()
        self.assertFalse(self.s["_assist_context"](self.pc))
        self.pc.Pawn.DrivenVehicle = None
        self.pc.Pawn.Physics = 7
        self.assertFalse(self.s["_assist_context"](self.pc))
        self.pc.Pawn.Physics = 1
        self.s["_deathtrap_skill_active"] = True
        self.assertFalse(self.s["_assist_context"](self.pc))

    def test_scorpio_cancels_existing_sprint_while_moving(self):
        self.assertIs(self._press(), self.block)
        self.assertFalse(self.s["_armed"])
        self.assertEqual(self.pc.end_calls, 1)
        self.assertEqual(self.writes, [False])
        self.assertFalse(self.pc.bPressedSprint)

    def test_scorpio_can_reselect_remembered_sprint(self):
        self._press()
        self.s["_assist_press_held"] = False
        self.assertIsNone(self._press())  # allows native SprintPressed afterward
        self.assertTrue(self.s["_armed"])
        self.assertEqual(self.writes, [False, True])
        self.assertEqual(self.retry, [self.pc])
        self.assertTrue(self.s["_assist_context"](self.pc))

    def test_scorpio_can_select_sprint_from_stationary_walk(self):
        self.s["_armed"] = False
        self.pc.bInSprintState = False
        self.assertIsNone(self._press())
        self.assertTrue(self.s["_armed"])
        self.assertEqual(self.writes, [True])
        self.assertEqual(self.retry, [self.pc])

    def test_other_special_contexts_still_rejected(self):
        for context in ("CustomUnknown", "DeathTrap", "Menu", ""):
            self.pc.PlayerInput.CurrentInputContextName = context
            self.assertFalse(self.s["_skill_assist_context"](self.pc))
            self._press()
            self.assertTrue(self.s["_armed"])
            self.assertEqual(self.pc.end_calls, 0)
        self.assertEqual(self.writes, [])

    def test_normal_on_foot_input_unchanged(self):
        self.pc.PlayerInput.CurrentInputContextName = "DefaultOnFoot"
        self.assertIs(self._press(), self.block)
        self.assertFalse(self.s["_armed"])
        self.assertEqual(self.pc.end_calls, 1)

    def test_recovery_native_python_and_airborne_paths_use_same_context(self):
        names = ("_assist_grounded", "_assist_can_retry", "_assist_recovery_tick",
                 "_assist_crouch_changed", "_native_assist_recovery",
                 "_assist_landed", "_hold_assist_through_airborne")
        for name in names:
            body = ast.get_source_segment(SOURCE, method(name))
            self.assertIn("_assist_context(", body, name)
        body = ast.get_source_segment(SOURCE, method("_pressed"))
        self.assertIn("_assist_recover_or_schedule(pc)", body)
        # The hot-path movement, coyote and physics gates are not widened.
        self.assertNotIn("_skill_assist_context(", ast.get_source_segment(
            SOURCE, method("_allows")))


if __name__ == "__main__":
    unittest.main()
