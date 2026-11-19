"""Source-driven, isolated tests for Zer0 Decepti0n and final Execute cleanup.

Source evidence: Skills & Characters/REFERENCE-CHARACTER-SYSTEMS.md
ExecuteActionSkill.CleanUpExecute calls RestorePhysicsPostExecute AFTER
WeaponActionComplete and AFTER movement has already been restored. Native
RestorePhysicsPostExecute resets grounded X/Y velocity a second time.
"""
import ast
import math
from pathlib import Path
from types import SimpleNamespace as NS
import unittest

SRC = (Path(__file__).resolve().parents[1] /
       "mod" / "OmniMovement" / "__init__.py").read_text(encoding="utf-8-sig")
TREE = ast.parse(SRC)

def fn(name):
    return next(n for n in TREE.body if isinstance(n, ast.FunctionDef) and n.name == name)

class Clock:
    now = 10.0
    @classmethod
    def perf_counter(cls):
        return cls.now

class FinalExecuteResetTests(unittest.TestCase):
    def setUp(self):
        self.pawn = NS(
            Role=3, Physics=1, DrivenVehicle=None,
            GroundSpeed=500., Velocity=NS(X=180., Y=65., Z=-1.),
            bIsCrouched=False, bWantsToCrouch=False)
        self.pc = NS(
            Pawn=self.pawn, PlayerInput=NS(aBaseY=1., aStrafe=0.),
            bIgnoreMoveInput=False, bCinematicMode=False, bDuck=False,
            _get_address=lambda: 100)
        self.pawn._get_address = lambda: 200
        self.skill = NS(
            bCleaningUpExecute=True, bMovementHasBeenRestored=True,
            MyWillowPC=self.pc, MyWillowPawn=self.pawn,
            _get_address=lambda: 300)
        self.env = {
            "_active": True, "_cfg_assist": True, "_armed": True,
            "_zero_final_physics_snapshot": None,
            "time": Clock, "math": math,
            "_pc": lambda: self.pc,
            "_same": lambda a, b: a is b,
            "_assist_crouching": lambda pc, pawn=None: pc.bDuck or pawn.bIsCrouched,
            "_assist_raw_moving": lambda pc, pi=None: bool(
                pi.aBaseY or pi.aStrafe),
            "_basic_context": lambda *a, **k: True,
            "_debug": lambda m: None,
        }
        names = ("_zero_final_restore_pre", "_zero_final_restore_post")
        exec(compile(ast.Module(body=[fn(n) for n in names], type_ignores=[]),
                     "<zero-physics-hooks>", "exec"), self.env)

    def pre(self):
        self.env["_zero_final_restore_pre"](self.skill, None, None, None)

    def post(self):
        self.env["_zero_final_restore_post"](self.skill, None, None, None)

    def test_final_reset_preserves_existing_walking_motion_and_native_z(self):
        self.pre()
        self.assertIsNotNone(self.env["_zero_final_physics_snapshot"])
        self.pawn.Velocity.X = self.pawn.Velocity.Y = 0.
        self.pawn.Velocity.Z = -1.
        self.post()
        self.assertAlmostEqual(self.pawn.Velocity.X, 180.)
        self.assertAlmostEqual(self.pawn.Velocity.Y, 65.)
        self.assertEqual(self.pawn.Velocity.Z, -1.)
        self.assertIsNone(self.env["_zero_final_physics_snapshot"])
        # No repeated write from an old one-shot snapshot.
        self.pawn.Velocity.X = 0.
        self.post()
        self.assertEqual(self.pawn.Velocity.X, 0.)

    def test_dash_end_first_reset_is_never_restored(self):
        self.skill.bCleaningUpExecute = False
        self.pre()
        self.pawn.Velocity.X = self.pawn.Velocity.Y = 0.
        self.post()
        self.assertEqual(self.pawn.Velocity.X, 0.)

    def test_native_nonzero_velocity_always_wins(self):
        self.pre()
        self.pawn.Velocity.X, self.pawn.Velocity.Y = 80., 20.
        self.post()
        self.assertEqual(self.pawn.Velocity.X, 80.)
        self.assertEqual(self.pawn.Velocity.Y, 20.)

    def test_never_preserves_dash_like_speed_or_zero_input(self):
        self.pawn.Velocity.X = 1100.
        self.pre()
        self.assertIsNone(self.env["_zero_final_physics_snapshot"])
        self.pawn.Velocity.X = 150.
        self.pc.PlayerInput.aBaseY = 0.
        self.pre()
        self.assertIsNone(self.env["_zero_final_physics_snapshot"])

    def test_native_ownership_falling_client_vehicle_crouch_and_control_locks(self):
        settings = [
            (self.pawn, "Role", 1),
            (self.pawn, "Physics", 2),
            (self.pawn, "DrivenVehicle", object()),
            (self.pc, "bDuck", True),
            (self.pc, "bIgnoreMoveInput", True),
            (self.pc, "bCinematicMode", True),
            (self.skill, "bMovementHasBeenRestored", False),
            (self.skill, "MyWillowPC", object()),
            (self.skill, "MyWillowPawn", object()),
        ]
        for obj, key, val in settings:
            with self.subTest(key=key):
                original = getattr(obj, key)
                setattr(obj, key, val)
                self.pre()
                self.assertIsNone(self.env["_zero_final_physics_snapshot"])
                setattr(obj, key, original)

    def test_expired_or_replaced_event_fails_closed(self):
        self.pre()
        self.pawn.Velocity.X = self.pawn.Velocity.Y = 0.
        Clock.now = 10.2
        self.post()
        self.assertEqual(self.pawn.Velocity.X, 0.)
        Clock.now = 10.0

    def test_hooks_are_post_cleanup_ordered_and_skips_unrelated_chars(self):
        code = ast.get_source_segment(SRC, fn("_sync_feature_hooks"))
        self.assertIn("ZERO_EXECUTE_PHYSICS_RESTORE", code)
        self.assertIn("_zero_final_restore_pre", code)
        self.assertIn("_zero_final_restore_post", code)
        self.assertIn("Type.POST_UNCONDITIONAL", code)
        self.assertIn("Type.PRE", code)
        self.assertNotIn("_zero_final_restore_post", ast.get_source_segment(
            SRC, fn("_assist_crouch_changed")))

class InitialDeceptionTests(unittest.TestCase):
    def test_cast_marker_is_only_active_for_local_execute_skill(self):
        pc = NS(Pawn=object(), _get_address=lambda: 33)
        args = NS(TheController=pc)
        skill = NS(Class=NS(Name="ExecuteActionSkill"), MyWillowPawn=pc.Pawn)
        env = {
            "_active": True, "_cfg_omni": True, "_cfg_crosshair": False,
            "_zero_deception_cast_edge": None, "_coyote_enabled": lambda: False,
            "_local_pc": lambda c: c,
            "_same": lambda a, b: a is b,
            "_debug": lambda m: None,
            "time": Clock,
        }
        exec(compile(ast.Module(body=[fn("_action_skill_started")],
                                type_ignores=[]), "<zero-cast>", "exec"), env)
        env["_action_skill_started"](skill, args, None, None)
        self.assertEqual(env["_zero_deception_cast_edge"], (33, 10.75))
        env["_zero_deception_cast_edge"] = None
        skill.Class.Name = "PhaselockActionSkill"
        env["_action_skill_started"](skill, args, None, None)
        self.assertIsNone(env["_zero_deception_cast_edge"])
        skill.Class.Name = "ExecuteActionSkill"
        skill.MyWillowPawn = object()
        env["_action_skill_started"](skill, args, None, None)
        self.assertIsNone(env["_zero_deception_cast_edge"])

    def test_cast_skid_suppression_is_only_on_end_sprint_and_temporary(self):
        skid = ast.get_source_segment(SRC, fn("_ended_sprint"))
        self.assertIn("cast_edge[0] == pc._get_address()", skid)
        self.assertIn("time.perf_counter() <= cast_edge[1]", skid)
        self.assertIn("_action_skid.cancel(pc)", skid)
        self.assertIn("_schedule_action_skid_tick()", skid)
        # Native sprint-state transitions still take place normally.
        self.assertNotIn("return Block", skid)
        self.assertIn("_assist_recover_or_schedule(pc)", skid)

    def test_existing_crouch_fix_kept_unchanged_in_behavior(self):
        crouch = ast.get_source_segment(SRC, fn("_assist_crouch_changed"))
        self.assertIn("_crouch_sprint_pause_until = time.perf_counter() + 0.22", crouch)
        self.assertIn("_force_end_sprint(pc)", crouch)
        self.assertIn("pc.bWantsToSprint = False", crouch)

if __name__ == "__main__":
    unittest.main()
