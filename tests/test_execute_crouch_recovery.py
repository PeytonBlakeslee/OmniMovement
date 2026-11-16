"""Source-grounded Zer0 Execute momentum and rapid crouch safety tests.

Source: Skills & Characters/REFERENCE-CHARACTER-SYSTEMS.md and
REFERENCE-PARENTS.md. Execute zeros horizontal velocity twice, while the
WillowPlayerInput pending Sprint path may clear crouch on accepted input.
"""
import ast
import math
from pathlib import Path
from types import SimpleNamespace as NS
import unittest

SOURCE = (Path(__file__).resolve().parents[1] / "mod" / "OmniMovement" /
          "__init__.py").read_text(encoding="utf-8-sig")
MODULE = ast.parse(SOURCE)


def function(name):
    return next(n for n in MODULE.body if isinstance(n, ast.FunctionDef) and n.name == name)


class ExecuteMotionTests(unittest.TestCase):
    def setUp(self):
        self.pawn = NS(
            Role=3, Physics=1, DrivenVehicle=None,
            bForceRMVelocity=False, bForceRegularVelocity=False,
            bRMForceStop=False, bIsInjured=False, bIsDead=False,
            GroundSpeed=500.0, MaxDesiredSpeed=1.0,
            Velocity=NS(X=0.0, Y=0.0, Z=-1.0)
        )
        self.pc = NS(
            bIgnoreMoveInput=False, bCinematicMode=False,
            bPerformingMeleeAttack=False, bPerformingSharedWeaponAction=False,
            Pawn=self.pawn, Rotation=NS(Yaw=0)
        )
        self.pi = NS(aBaseY=1.0, aStrafe=0.0)
        env = {
            "math": math,
            "_assist_crouching": lambda pc, pawn=None: False,
            "_basic_context": lambda pc, pi=None, pawn=None, fire_pose_only=False: True,
            "_movement_world_direction": lambda pc, forward, side, mag: (
                forward / mag, side / mag),
        }
        code = ast.Module(body=[function("_zero_execute_restore_momentum")],
                          type_ignores=[])
        exec(compile(code, "<execute-motion>", "exec"), env)
        self.resume = env["_zero_execute_restore_momentum"]

    def test_grounded_near_zero_reseed_30pct_normal_speed(self):
        self.assertTrue(self.resume(self.pc, self.pi, self.pawn))
        self.assertAlmostEqual(self.pawn.Velocity.X, 150.0)
        self.assertAlmostEqual(self.pawn.Velocity.Y, 0.0)
        self.assertEqual(self.pawn.Velocity.Z, -1.0)
        # Repeated native restoration must not repeatedly accelerate the pawn.
        self.assertFalse(self.resume(self.pc, self.pi, self.pawn))

    def test_sideways_and_backward_reseed_aligns_with_real_input(self):
        for forward, side, vx, vy in ((0, 1, 0, 150), (-1, 0, -150, 0)):
            with self.subTest(forward=forward, side=side):
                self.pawn.Velocity.X = self.pawn.Velocity.Y = 0
                self.pi.aBaseY, self.pi.aStrafe = forward, side
                self.assertTrue(self.resume(self.pc, self.pi, self.pawn))
                self.assertAlmostEqual(self.pawn.Velocity.X, vx)
                self.assertAlmostEqual(self.pawn.Velocity.Y, vy)

    def test_never_changes_nonzero_velocity_or_falling_motion(self):
        self.pawn.Velocity.X = 100
        self.assertFalse(self.resume(self.pc, self.pi, self.pawn))
        self.assertEqual(self.pawn.Velocity.X, 100)
        self.pawn.Velocity.X = 0
        self.pawn.Physics = 2
        self.assertFalse(self.resume(self.pc, self.pi, self.pawn))
        self.assertEqual(self.pawn.Velocity.X, 0)

    def test_never_resumes_during_melee_vehicle_crouch_or_client(self):
        for obj, field, value in (
            (self.pc, "bPerformingMeleeAttack", True),
            (self.pc, "bPerformingSharedWeaponAction", True),
            (self.pc, "bIgnoreMoveInput", True),
            (self.pawn, "bForceRMVelocity", True),
            (self.pawn, "bRMForceStop", True),
            (self.pawn, "DrivenVehicle", object()),
            (self.pawn, "Role", 1),
        ):
            with self.subTest(field=field):
                prior = getattr(obj, field)
                setattr(obj, field, value)
                self.assertFalse(self.resume(self.pc, self.pi, self.pawn))
                self.assertEqual(self.pawn.Velocity.X, 0.0)
                setattr(obj, field, prior)
        self.pi.aBaseY = 0.0
        self.assertFalse(self.resume(self.pc, self.pi, self.pawn))

    def test_invalid_values_fail_closed_without_motion_write(self):
        for value in (float("nan"), float("inf")):
            self.pi.aBaseY = value
            self.assertFalse(self.resume(self.pc, self.pi, self.pawn))
            self.assertEqual(self.pawn.Velocity.X, 0.0)
        self.pi.aBaseY = 1.0
        self.pawn.GroundSpeed = float("inf")
        self.assertFalse(self.resume(self.pc, self.pi, self.pawn))


class CrouchSprintGateTests(unittest.TestCase):
    def test_crouch_permission_guard_precedes_native_motion_gate(self):
        src = ast.get_source_segment(SOURCE, function("_allows"))
        guard = src.index("if _cfg_assist and _armed:")
        native = src.index("_precision.motion_fused")
        self.assertLess(guard, native)
        self.assertIn("_crouch_sprint_paused()", src[guard:native])
        self.assertIn("return Block, False", src[guard:native])

    def test_crouch_input_resets_queued_sprint_and_motion_grace(self):
        src = ast.get_source_segment(SOURCE, function("_assist_crouch_changed"))
        self.assertIn("_reset_gate_grace()", src)
        self.assertIn("_clear_center_transition()", src)
        self.assertIn("pc.bWantsToSprint = False", src)
        self.assertIn("pc.bPressedSprint = False", src)
        self.assertIn("_set_try_sprint(pi, False)", src)
        self.assertIn("_schedule_assist_recovery(pc)", src)
        self.assertIn("_force_end_sprint(pc)", src)
        self.assertIn("_crouch_sprint_pause_until = time.perf_counter() + 0.22", src)

    def test_sprint_gate_only_blocks_when_auto_sprint_is_armed(self):
        from test_motion_bridge import MotionBridgeTests

        ns, _, pc, pi, pawn, calls = MotionBridgeTests().supervisor()
        ns.update(_cfg_assist=True, _armed=True, _crouch_sprint_paused=lambda: False,
                  _assist_crouching=lambda controller, current_pawn: current_pawn is pawn)
        self.assertEqual(ns["_allows"](pi, None, None, None), ("block", False))
        self.assertEqual(calls, [])
        ns["_armed"] = False
        self.assertEqual(ns["_allows"](pi, None, None, None), ("block", True))
        self.assertTrue(calls)


if __name__ == "__main__":
    unittest.main()
