"""Duck event handoff: tests actual code, not just source-string presence."""
import ast
from pathlib import Path
from types import SimpleNamespace as NS
import unittest

SRC = (Path(__file__).resolve().parents[1] /
       "mod" / "OmniMovement" / "__init__.py").read_text(encoding="utf-8-sig")
TREE = ast.parse(SRC)


def body(name):
    return next(n for n in TREE.body
                if isinstance(n, ast.FunctionDef) and n.name == name)


class DuckPriorityEventTests(unittest.TestCase):
    def setUp(self):
        self.events = []
        self.pc = NS(bInSprintState=True, PlayerInput=NS())
        self.crouched = True
        self.now = [10.0]
        self.scope = {
            "_active": True, "_cfg_assist": True, "_armed": True, "_cfg_omni": True,
            "_moving_nonforward_on_foot": lambda pc: True,
            "_action_skid": NS(cancel=lambda pc: self.events.append("cancel-skid")),
            "_schedule_action_skid_tick": lambda: self.events.append("skid-cleanup"),
            "_assist_airborne_hold": False, "_crouch_sprint_pause_until": 0.0,
            "_same": lambda a, b: a is b,
            "_assist_crouching": lambda pc: self.crouched,
            "_assist_context": lambda *a, **kw: True,
            "_force_end_sprint": self.end_sprint,
            "_reset_gate_grace": lambda: self.events.append("reset-grace"),
            "_clear_direction": lambda: self.events.append("reset-direction"),
            "_clear_center_transition": lambda: self.events.append("reset-center"),
            "_set_hook": lambda *a: None, "INPUT_ALLOWS_SPRINT": "sprint",
            "Type": NS(PRE="pre"), "_assist_airborne_gate": None,
            "_set_try_sprint": lambda pi, val: self.events.append(("pending", val)),
            "_schedule_assist_recovery": lambda pc: self.events.append("watch"),
            "_hold_assist_through_airborne": lambda pc: False,
            "_assist_recover_or_schedule": lambda pc: self.events.append("recover"),
            "time": NS(perf_counter=lambda: self.now[0]),
        }
        code = ast.Module(body=[body("_assist_crouch_changed")], type_ignores=[])
        exec(compile(code, "<crouch>", "exec"), self.scope)

    def end_sprint(self, pc):
        self.events.append("EndSprint")
        pc.bInSprintState = False

    def event(self):
        self.scope["_assist_crouch_changed"](self.pc, self.pc.PlayerInput)

    def test_pressed_crouch_exits_actual_sprint_before_recovery(self):
        self.event()
        self.assertEqual(self.events[0], "EndSprint")
        self.assertFalse(self.pc.bInSprintState)
        self.assertIn(("pending", False), self.events)
        self.assertIn("watch", self.events)
        self.assertAlmostEqual(self.scope["_crouch_sprint_pause_until"], 10.22)

    def test_crouch_cancels_only_eligible_sprint_exit_lurch(self):
        self.event()
        self.assertEqual(self.events[:3], ["EndSprint", "cancel-skid", "skid-cleanup"])
        self.setUp()
        self.scope["_moving_nonforward_on_foot"] = lambda pc: False
        self.event()
        self.assertNotIn("cancel-skid", self.events)
        self.setUp()
        self.scope["_cfg_omni"] = False
        self.event()
        self.assertNotIn("cancel-skid", self.events)
        self.setUp()
        self.crouched = False
        self.event()
        self.assertNotIn("cancel-skid", self.events)

    def test_failed_skid_cleanup_does_not_break_crouch_priority(self):
        def fail(pc):
            raise RuntimeError("unavailable lurch")
        self.scope["_action_skid"].cancel = fail
        self.event()
        self.assertFalse(self.pc.bInSprintState)
        self.assertIn(("pending", False), self.events)
        self.assertIn("watch", self.events)

    def test_rapid_release_extends_holdoff_without_forcing_sprint(self):
        self.event()
        self.events.clear()
        self.crouched = False
        self.now[0] = 10.12
        self.event()
        self.assertNotIn("EndSprint", self.events)
        self.assertIn("recover", self.events)
        self.assertAlmostEqual(self.scope["_crouch_sprint_pause_until"], 10.34)

    def test_repeated_taps_while_native_sprinting_still_exit(self):
        self.event()
        self.pc.bInSprintState = True
        self.now[0] += .1
        self.events.clear()
        self.event()
        self.assertEqual(self.events[0], "EndSprint")
        self.assertFalse(self.pc.bInSprintState)

    def test_other_input_and_walk_mode_never_change_sprint(self):
        self.scope["_armed"] = False
        self.event()
        self.assertEqual(self.events, [])
        self.scope["_armed"] = True
        self.scope["_cfg_assist"] = False
        self.event()
        self.assertEqual(self.events, [])
        self.scope["_cfg_assist"] = True
        self.scope["_same"] = lambda a, b: False
        self.event()
        self.assertEqual(self.events, [])


if __name__ == "__main__":
    unittest.main()
