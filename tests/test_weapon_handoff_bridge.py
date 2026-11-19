"""Regressions for sprint recovery and reticle ownership at equipment handoff."""
from contextlib import nullcontext
import math
from types import SimpleNamespace as NS
import unittest

from test_combat_bridge import MOD, load_definitions


def obj(address, **fields):
    return NS(_get_address=lambda: address, **fields)


def same(left, right):
    return left is not None and right is not None and left._get_address() == right._get_address()


class EquipSprintHandoffTests(unittest.TestCase):
    def setUp(self):
        self.now = 10.0
        self.calls = []
        self.pc = obj(1, bInSprintState=True, PlayerInput=obj(
            2, CurrentInputContextName="DefaultOnFoot", bUsingGamepad=True), SprintModifiers=[],
                      WorldInfo=NS(Pauser=None), PlayerReplicationInfo=NS(bGFxMenuOpen=False),
                      bPressedJump=False,
                      bThrowingGrenade=False, bPerformingMeleeAttack=False,
                      bPerformingSharedWeaponAction=False)
        self.pawn = obj(3, Controller=self.pc, DrivenVehicle=None, Physics=1,
                        bDeleteMe=False, bReloading=False, bForceRMVelocity=False,
                        bForceRegularVelocity=False, bRMForceStop=False, DoSprint=self.do_sprint)
        self.main, self.left = (obj(i, Instigator=self.pawn, GetStateName=lambda: "WeaponEquipping")
                                for i in (4, 5))
        self.pc.Pawn = self.pawn
        self.pawn.Weapon, self.pawn.OffHandWeapon = self.main, self.left
        self.env = dict(
            _active=True, _cfg_omni=True, _cfg_assist=True, _armed=True,
            _hooks={"equip-sprint-post": object()}, _equip_sprint_handoffs={},
            _watch_equip_sprint_frames=lambda: True, _stop_equip_sprint_frames=lambda: None,
            _local_input=lambda pi: pi if pi is self.pc.PlayerInput else None,
            _travel_pending=False, _crosshair_modal_open=False, _deathtrap_skill_active=False,
            _travel_modal_open=False, _travel_close_pending=False, _travel_epoch=7,
            _travel_generation=(1, 2, 3), _travel_ready_validated=False,
            _travel_verified_sprint=False, _travel_initial_rearm_at=0.0,
            _capture_activation_input=lambda *a: None,
            _knockback_until=0.0, _same=same, _pc=lambda: self.pc,
            _crouch_sprint_paused=lambda: False, _zero_execute_waiting=lambda: False,
            _assist_crouching=lambda *args: False, _assist_context=lambda *args, **kwargs: True,
            _assist_raw_moving=lambda *args, **kwargs: True, _fast_zoomed=lambda *args: False,
            _pose_reload_active=lambda pawn: False,
            time=NS(perf_counter=lambda: self.now),
            _weapon_transition_begin=lambda obj, *args: self.calls.append(("equip", obj._get_address())))
        self.load("_equip_sprint_owner", "_equip_sprint_bringup_pre", "_equip_sprint_bringup_post",
                  "_equip_sprint_veto", "_try_equip_sprint", "_resume_equip_sprint",
                  "_equip_activation_travel_owner", "_equip_activation_world_ready",
                  "_equip_activation_world_loaded",
                  "_equip_sprint_skid", "_expire_equip_sprint_skid",
                  "_weapon_equip_begin", "_equip_sprint_frame", "_clear_equip_sprint_handoffs",
                  "_equip_sprint_context_pre", "_equip_sprint_context_post")

    def load(self, *names):
        load_definitions(MOD / "__init__.py", names, self.env)

    def do_sprint(self, updating):
        self.calls.append(("native", updating))
        self.pc.bInSprintState = True

    def event(self, fn, weapon):
        return self.env[fn](weapon, None, None, None)

    def interrupt(self, weapon):
        self.pc.bInSprintState = True
        self.event("_equip_sprint_bringup_pre", weapon)
        self.pc.bInSprintState = False
        self.event("_equip_sprint_bringup_post", weapon)

    def test_normal_swap_and_staggered_offhand_draw_use_native_entry(self):
        self.pawn.OffHandWeapon = None
        self.interrupt(self.main)
        self.event("_weapon_equip_begin", self.main)
        self.pawn.OffHandWeapon = self.left
        self.interrupt(self.left)
        self.event("_weapon_equip_begin", self.left)
        self.assertEqual(self.calls, [("equip", 4), ("native", False),
                                      ("equip", 5), ("native", False)])
        self.assertTrue(self.pc.bInSprintState)

    def configure_weapon_accuracy(self):
        pool = obj(9)
        self.pc.AccuracyPool = NS(Data=pool)
        self.env.update(
            _cfg_baseline=True, _drop_sprint_scope=None,
            _local_pc=lambda pc: pc if pc is self.pc else None,
            _suppressed=[(lambda: self.pc, None, lambda: pool, None)],
            _restore_baseline=lambda: self.calls.append(("restore-accuracy",)),
            _clear_direction=lambda: None, _basic_context=lambda pc, **kw: True,
            _skill_swap_keeps_sprint=lambda pc, pawn, incoming: False,
            _action_skid=NS(cancel=lambda pc: None), _schedule_action_skid_tick=lambda: None,
            _sync_sprint_hooks=lambda pc: None,
            unrealsdk=NS(hooks=NS(prevent_hooking_direct_calls=nullcontext)),
        )
        self.load("_weapon_accuracy")

        def end_sprint():
            self.calls.append(("end-sprint",))
            self.pc.bInSprintState = False

        self.pc.EndSprint = end_sprint
        return pool

    def run_accuracy_bringup(self, weapon):
        self.event("_equip_sprint_bringup_pre", weapon)
        action = self.env["_weapon_accuracy"](self.pc, NS(TheWeapon=weapon), None, None)
        self.assertIsNone(action)  # Stock accuracy initializer still runs.
        self.event("_equip_sprint_bringup_post", weapon)

    def test_airborne_swap_keeps_sprint_with_the_same_accuracy_pool(self):
        pool = self.configure_weapon_accuracy()
        for weapon, physics, pool_matches, is_activation, expect_reset in (
            (self.main, 2, True, False, False),
            (self.left, 2, True, False, False),
            (self.main, 2, False, False, True),
            (self.main, 1, True, False, True),
            (self.left, 2, True, True, True),
        ):
            with self.subTest(weapon=weapon._get_address(), physics=physics,
                              pool_matches=pool_matches, is_activation=is_activation):
                self.calls.clear()
                self.pawn.Physics = physics
                self.pc.AccuracyPool.Data = pool if pool_matches else obj(10)
                self.pc.PlayerInput.CurrentInputContextName = (
                    "DefaultOnFoot" if is_activation else "Gunzerking")
                self.pc.bInSprintState = True
                self.run_accuracy_bringup(weapon)
                self.assertEqual(self.pc.bInSprintState, not expect_reset)
                self.assertEqual(self.calls, (
                    [("restore-accuracy",), ("end-sprint",)] if expect_reset else []))

    def test_grounded_swap_preserves_existing_sprint_in_verified_single_weapon_skill(self):
        self.configure_weapon_accuracy()
        self.load("_skill_swap_keeps_sprint", "_skill_assist_context", "_combat_sprint_safe")
        self.pawn.OffHandWeapon = None
        self.pawn.MyActionSkill = object()
        self.pawn.IsActionSkillRunning = lambda: True
        self.pawn.bIsCrouched = self.pawn.bWantsToCrouch = self.pc.bDuck = False
        for context in ("Scorpio", "VisionMode", "Phaselock", "DefaultOnFoot"):
            with self.subTest(context=context):
                self.pc.PlayerInput.CurrentInputContextName = context
                self.run_accuracy_bringup(self.main)
                self.assertTrue(self.pc.bInSprintState)
                self.assertEqual(self.calls, [])
        self.pawn.bReloading = True
        self.run_accuracy_bringup(self.main)
        self.assertEqual(self.calls, [("restore-accuracy",), ("end-sprint",)])

    def test_guarded_summon_swap_keeps_sprint_without_bypassing_action_locks(self):
        self.configure_weapon_accuracy()
        self.load("_skill_swap_keeps_sprint", "_skill_assist_context", "_combat_sprint_safe")
        self.pawn.OffHandWeapon = None
        self.pawn.MyActionSkill = object()
        self.pawn.IsActionSkillRunning = lambda: True
        self.pawn.bIsCrouched = self.pawn.bWantsToCrouch = self.pc.bDuck = False
        self.pc.PlayerInput.CurrentInputContextName = "DeathTrap"
        self.env['_deathtrap_skill_active'] = True
        self.assertFalse(self.env['_skill_assist_context'](self.pc))
        self.run_accuracy_bringup(self.main)
        self.assertTrue(self.pc.bInSprintState)
        self.assertEqual(self.calls, [])
        self.pc.bPerformingMeleeAttack = True
        self.run_accuracy_bringup(self.main)
        self.assertEqual(self.calls, [("restore-accuracy",), ("end-sprint",)])

    def test_dual_wield_equip_keeps_sprint_with_other_hand_reloading(self):
        self.configure_weapon_accuracy()
        self.load("_skill_swap_keeps_sprint", "_combat_sprint_safe")
        self.pc.bDuck = self.pawn.bIsCrouched = self.pawn.bWantsToCrouch = False
        self.pc.PlayerInput.CurrentInputContextName = "Gunzerking"
        self.pawn.bReloading = True
        self.env.update(_skill_assist_context=lambda *a, **kw: True,
                        _pose_reload_active=lambda pawn: True,
                        _pose_reload_addresses={self.left._get_address()})
        self.run_accuracy_bringup(self.main)
        self.assertTrue(self.pc.bInSprintState)
        self.assertEqual(self.calls, [])
        self.run_accuracy_bringup(self.left)
        self.assertFalse(self.pc.bInSprintState)
        self.pc.bInSprintState = True
        self.pawn.bReloading = False
        self.env["_pose_reload_active"] = lambda pawn: False
        self.calls.clear()
        for incoming in (self.main, self.left):
            self.run_accuracy_bringup(incoming)
            self.assertTrue(self.pc.bInSprintState)
        self.assertEqual(self.calls, [])

    def test_equip_end_refreshes_pose_only_for_current_weapon(self):
        self.load("_weapon_equip_end")
        self.env.update(_cfg_hip_fire=True, _weapon_transition_end=lambda *a: None,
                        _sprint_fire_entry=lambda pc, **kw: self.calls.append(kw))
        self.event("_weapon_equip_end", obj(90, Instigator=self.pawn))
        self.event("_weapon_equip_end", self.main)
        self.assertEqual(self.calls, [{"after_equip": True}])

    def test_offhand_equip_reconciles_only_its_own_trigger_before_active(self):
        self.load("_offhand_equip_pending_fire", "_mainhand_equip_pending_fire")
        self.env["_basic_context"] = lambda *a, **kw: True
        self.pc.PlayerInput.CurrentInputContextName = "Gunzerking"
        self.pc.WorldInfo.NetMode = 2  # A LAN or online listen host.
        self.pawn.bNoWeaponFiring = False
        self.left.bOffHand = True
        self.left.Role = 3
        self.left.bWeaponPutDown = False
        self.left.GetPendingFireLength = lambda: 1
        self.left.ClearPendingFire = lambda mode: self.calls.append(("clear", mode))
        self.left.SetPendingFire = lambda mode: self.calls.append(("queue", mode))
        self.pc.bStartFireZeroBeforeGunzerking = False
        hook = self.env["_offhand_equip_pending_fire"]
        for switched in (False, True):
            self.pc.bGunzerkingAutoSwitch = switched
            self.env.update(_cfg_hip_fire=True, _fire_requested=not switched,
                            _pose_offhand_requested=switched)
            self.left.PendingFire = lambda mode: True
            hook(self.left, NS(NextStateName="Active"), None, None)
            self.env.update(_fire_requested=switched, _pose_offhand_requested=not switched)
            self.left.PendingFire = lambda mode: False
            hook(self.left, NS(NextStateName="Active"), None, None)
            hook(self.main, NS(NextStateName="Active"), None, None)
            hook(self.left, NS(NextStateName="WeaponPuttingDown"), None, None)
        self.left.Role = 2  # A remote client's weapon.
        hook(self.left, NS(NextStateName="Active"), None, None)
        self.assertEqual(self.calls, [("clear", 0), ("queue", 0)] * 2)

    def test_mainhand_equip_queues_only_held_local_trigger(self):
        self.load("_mainhand_equip_pending_fire")
        self.env.update(_cfg_hip_fire=True, _basic_context=lambda *a, **kw: True,
                        _fire_requested=True, _pose_offhand_requested=False)
        self.pawn.bNoWeaponFiring = False
        self.main.bOffHand, self.main.Role, self.main.bWeaponPutDown = False, 3, False
        self.main.GetPendingFireLength = lambda: 1
        self.main.PendingFire = lambda mode: False
        self.main.SetPendingFire = lambda mode: self.calls.append(("queue", mode))
        hook = self.env["_mainhand_equip_pending_fire"]
        self.pawn.OffHandWeapon = None
        hook(self.main, NS(NextStateName="Active"))
        self.main.PendingFire = lambda mode: True  # Firing Fix already queued it.
        hook(self.main, NS(NextStateName="Active"))
        self.main.PendingFire = lambda mode: False
        self.env["_fire_requested"] = False
        hook(self.main, NS(NextStateName="Active"))
        self.env["_fire_requested"] = True
        self.main.Role = 2
        hook(self.main, NS(NextStateName="Active"))
        self.main.Role = 3
        self.pawn.OffHandWeapon = self.left
        self.pc.PlayerInput.CurrentInputContextName = "Gunzerking"
        self.pc.bGunzerkingAutoSwitch = True
        self.env['_fire_requested'] = False
        self.env['_pose_offhand_requested'] = True
        hook(self.main, NS(NextStateName="Active"))
        self.env['_fire_requested'] = True
        self.env['_pose_offhand_requested'] = False
        hook(self.main, NS(NextStateName="Active"))
        self.assertEqual(self.calls, [("queue", 0), ("queue", 0)])

    def test_drop_preserves_sprint_through_replacement_and_synchronous_pickup(self):
        self.configure_weapon_accuracy()
        self.load("_drop_sprint_pre", "_drop_sprint_post")
        self.pawn.OffHandWeapon = None
        outgoing = self.main
        replacement = obj(8, Instigator=self.pawn)
        for pick_it_back_up in (False, True):
            with self.subTest(pick_it_back_up=pick_it_back_up):
                self.pawn.Weapon = outgoing
                self.pc.bInSprintState = True
                self.pc.SprintModifiers[:] = [object()]
                self.calls.clear()
                self.event("_drop_sprint_pre", outgoing)
                self.run_accuracy_bringup(outgoing)  # BL2 can re-equip it before removing it.
                self.pawn.Weapon = replacement
                self.run_accuracy_bringup(replacement)
                if pick_it_back_up:
                    self.pawn.Weapon = outgoing
                    self.run_accuracy_bringup(outgoing)
                self.event("_drop_sprint_post", outgoing)
                self.assertTrue(self.pc.bInSprintState)
                self.assertEqual(len(self.pc.SprintModifiers), 1)
                self.assertEqual(self.calls, [])
                self.assertIsNone(self.env["_drop_sprint_scope"])

        self.run_accuracy_bringup(self.pawn.Weapon)  # A later normal swap uses the usual path.
        self.assertEqual(self.calls, [("restore-accuracy",), ("end-sprint",)])

    def test_drop_scope_cannot_protect_another_input_owner_or_an_expired_equip(self):
        self.configure_weapon_accuracy()
        self.load("_drop_sprint_pre", "_drop_sprint_post")
        for replace_input in (False, True):
            with self.subTest(replace_input=replace_input):
                self.pc.bInSprintState = True
                self.calls.clear()
                self.event("_drop_sprint_pre", self.main)
                if replace_input:
                    self.pc.PlayerInput = obj(10, CurrentInputContextName="DefaultOnFoot")
                else:
                    self.now += 0.26
                self.run_accuracy_bringup(self.main)
                self.assertEqual(self.calls, [("restore-accuracy",), ("end-sprint",)])
                self.event("_drop_sprint_post", self.main)

    def test_native_entry_denial_does_not_force_sprint(self):
        self.pawn.DoSprint = lambda updating: self.calls.append(("denied", updating))
        self.interrupt(self.left)
        self.event("_weapon_equip_begin", self.left)
        self.assertFalse(self.pc.bInSprintState)
        self.assertEqual(self.calls, [("equip", 5), ("denied", False)])
        self.now += 0.02
        self.env["_equip_sprint_frame"](self.pc)
        self.now += 0.02
        self.env["_equip_sprint_frame"](self.pc)
        self.env["_equip_sprint_frame"](self.pc)
        self.assertEqual(self.calls, [("equip", 5)] + [("denied", False)] * 3)
        self.assertFalse(self.pc.bInSprintState)
        self.assertFalse(self.env["_equip_sprint_handoffs"])

    def test_retry_after_modifier_cleanup_on_next_input(self):
        self.interrupt(self.main)
        self.pc.SprintModifiers.append(object())
        self.event("_weapon_equip_begin", self.main)
        self.assertEqual(self.calls, [("equip", 4)])
        self.pc.SprintModifiers.clear()
        self.now += 0.02
        self.env["_equip_sprint_frame"](self.pc)
        self.assertEqual(self.calls[-1], ("native", False))

    def test_transient_hud_veto_waits_but_real_pause_cancels(self):
        self.pc.PlayerReplicationInfo.bGFxMenuOpen = True
        self.interrupt(self.left)
        self.event("_weapon_equip_begin", self.left)
        self.assertEqual(self.calls, [("equip", 5)])
        self.env["_equip_sprint_frame"](self.pc)
        self.assertEqual(self.calls, [("equip", 5)])
        self.pc.PlayerReplicationInfo.bGFxMenuOpen = False
        self.now += 0.02
        self.env["_equip_sprint_frame"](self.pc)
        self.assertEqual(self.calls[-1], ("native", False))

        self.pc.WorldInfo.Pauser = object()
        self.interrupt(self.left)
        self.event("_weapon_equip_begin", self.left)
        self.assertFalse(self.env["_equip_sprint_handoffs"])
        self.assertEqual(self.calls[-1], ("equip", 5))

    def test_only_the_fresh_dual_equip_skid_is_cancelled(self):
        self.pc.WorldInfo.TimeSeconds = 40.0
        self.pc.CurrentLurch = NS(StartTime=38.0, Duration=0.4,
                                  LocMagnitude=-1.0, RotMagnitude=-100.0)
        self.env.update(_action_skid=NS(cancel=lambda pc: self.calls.append("skid")),
                        _schedule_action_skid_tick=lambda: self.calls.append("skid-tick"))
        self.event("_equip_sprint_bringup_pre", self.left)
        self.pc.CurrentLurch.StartTime = 40.0
        self.pc.bInSprintState = False
        self.event("_equip_sprint_bringup_post", self.left)
        self.event("_weapon_equip_begin", self.left)
        self.assertEqual(self.calls, [("equip", 5), ("native", False), "skid", "skid-tick"])

        self.calls.clear()
        self.event("_equip_sprint_bringup_pre", self.left)
        self.pc.bInSprintState = False
        self.pc.CurrentLurch.StartTime = 40.01
        self.event("_equip_sprint_bringup_post", self.left)
        self.pc.CurrentLurch.LocMagnitude = -3.0  # A later writer owns it now.
        self.pc.WorldInfo.TimeSeconds = 40.02
        self.event("_weapon_equip_begin", self.left)
        self.assertEqual(self.calls, [("equip", 5), ("native", False)])

    def test_activation_controller_bridge_requires_same_held_direction_and_runs_once(self):
        pi = self.pc.PlayerInput
        pi.aBaseY, pi.aStrafe = 0.05, -0.9
        moved = []
        stick = [(-0.01, -0.9)]
        self.env.update(
            _activation_input_frame=None, math=math,
            _travel_input=NS(synthetic=False, activation_stick_sample=lambda pi: stick[0],
                             bridge_activation_frame=lambda pi, axes: moved.append(axes)),
            _skill_assist_context=lambda *a, **kw: True, INPUT_FRAME="pre-process",
            _set_hook=lambda *a: False, _set_optional_hook=lambda *a: True,
            Type=NS(POST_UNCONDITIONAL="post-any"))
        self.pc.WorldInfo.IsPreparingMapChange = lambda: False
        self.pc.WorldInfo.Game = NS(bLevelChange=False)
        self.load("_capture_activation_input", "_activation_gamepad_frame",
                  "_activation_stick_aligned", "_stop_activation_input_frame")

        capture = self.env["_capture_activation_input"]
        capture(pi, NS(NewContextName="Gunzerking"))
        pi.CurrentInputContextName = "Gunzerking"
        frame = self.env["_activation_gamepad_frame"]
        frame(pi, None, None, None)
        frame(pi, None, None, None)
        self.assertEqual(moved, [(0.05, -0.9)])

        pi.CurrentInputContextName = "DefaultOnFoot"
        capture(pi, NS(NewContextName="Gunzerking"))
        pi.CurrentInputContextName = "Gunzerking"
        stick[0] = (0.0, 0.9)  # The player already changed direction.
        frame(pi, None, None, None)
        self.assertEqual(moved, [(0.05, -0.9)])

        pi.CurrentInputContextName = "DefaultOnFoot"
        pi.aBaseY = pi.aStrafe = 0.0
        pi.RawJoyUp, pi.RawJoyRight = 0.04, -0.83
        stick[0] = (0.0, -0.85)
        capture(pi, NS(NewContextName="Gunzerking"))
        pi.CurrentInputContextName = "Gunzerking"
        frame(pi, None, None, None)
        frame(pi, None, None, None)
        self.assertEqual(moved, [(0.05, -0.9), (0.04, -0.83)])

        pi.CurrentInputContextName = "DefaultOnFoot"
        stick[0] = (0.0, -0.4)  # The stick is still left, but has eased off.
        capture(pi, NS(NewContextName="Gunzerking"))
        self.assertIsNone(self.env["_activation_input_frame"])

        stick[0] = (0.0, -0.85)
        pi.aStrafe = 0.75  # Live input disagrees with the old RawJoy sample.
        capture(pi, NS(NewContextName="Gunzerking"))
        self.assertIsNone(self.env["_activation_input_frame"])

    def test_activation_uses_native_backward_diagonal_if_xinput_is_unavailable(self):
        pi = self.pc.PlayerInput
        state = {"physical": None, "gamepad": True}
        moved = []
        self.env.update(
            _activation_input_frame=None, math=math,
            _travel_input=NS(synthetic=False,
                             activation_gamepad_active=lambda pi: state["gamepad"],
                             activation_stick_sample=lambda pi: state["physical"],
                             bridge_activation_frame=lambda pi, axes: moved.append(axes)),
            _skill_assist_context=lambda *a, **kw: True, INPUT_FRAME="pre-process",
            _set_hook=lambda *a: False, _set_optional_hook=lambda *a: True,
            Type=NS(POST_UNCONDITIONAL="post-any"))
        self.pc.WorldInfo.IsPreparingMapChange = lambda: False
        self.pc.WorldInfo.Game = NS(bLevelChange=False)
        self.load("_capture_activation_input", "_activation_gamepad_frame",
                  "_activation_stick_aligned", "_stop_activation_input_frame")
        capture = self.env["_capture_activation_input"]
        frame = self.env["_activation_gamepad_frame"]

        for side, raw in ((-0.2844, (-0.9621, -0.2726)), (0.6142, (-0.8537, 0.5208))):
            pi.CurrentInputContextName = "DefaultOnFoot"
            pi.aBaseY, pi.aStrafe = -1.0, side
            pi.RawJoyUp, pi.RawJoyRight = raw
            capture(pi, NS(NewContextName="Gunzerking"))
            self.assertIsNotNone(self.env["_activation_input_frame"])
            pi.CurrentInputContextName = "Gunzerking"
            frame(pi, None, None, None)
            frame(pi, None, None, None)
            self.assertEqual(moved[-1], raw)
        self.assertEqual(len(moved), 2)

        pi.CurrentInputContextName = "DefaultOnFoot"
        pi.bUsingGamepad = False
        capture(pi, NS(NewContextName="Gunzerking"))
        self.assertIsNone(self.env["_activation_input_frame"])

        pi.bUsingGamepad = True
        state["gamepad"] = False  # Native sampling can proceed before focus is verified.
        capture(pi, NS(NewContextName="Gunzerking"))
        self.assertIsNotNone(self.env["_activation_input_frame"])
        pi.CurrentInputContextName = "Gunzerking"
        frame(pi, None, None, None)
        self.assertEqual(len(moved), 2)  # No writes without focus.

        pi.CurrentInputContextName = "DefaultOnFoot"
        state["gamepad"] = True
        capture(pi, NS(NewContextName="Gunzerking"))
        pi.CurrentInputContextName = "Gunzerking"
        state["physical"] = (0.0, 0.0)  # XInput comes back showing a release.
        frame(pi, None, None, None)
        self.assertEqual(len(moved), 2)

    def test_gunzerk_retry_during_verified_spawn_settling(self):
        self.pc.WorldInfo.IsPreparingMapChange = lambda: False
        self.pc.WorldInfo.Game = NS(bLevelChange=False)
        self.pc.IsLocalPlayerController = lambda: True
        self.pc.GFxUIManager = NS(IsBlockingMoviePlaying=lambda: False)
        self.env.update(_travel_pending=True, _travel_budget=NS(active=True),
                        _travel_ready_validated=False, _travel_verified_sprint=False,
                        _travel_close_pending=True)

        def draw():
            self.pc.SprintModifiers[:] = [object()]  # Native sprint already started.
            self.pc.bInSprintState = True
            self.event("_equip_sprint_bringup_pre", self.left)
            self.pc.SprintModifiers.clear()
            self.pc.bInSprintState = False
            self.event("_equip_sprint_bringup_post", self.left)

        draw()
        self.event("_weapon_equip_begin", self.left)
        self.assertTrue(self.pc.bInSprintState)

        self.pc.WorldInfo.Game.bLevelChange = True
        draw()
        self.event("_weapon_equip_begin", self.left)
        self.assertFalse(self.pc.bInSprintState)
        self.pc.WorldInfo.Game.bLevelChange = False

        self.pc.GFxUIManager.IsBlockingMoviePlaying = lambda: True
        draw()
        self.event("_weapon_equip_begin", self.left)
        self.assertFalse(self.pc.bInSprintState)
        self.pc.GFxUIManager.IsBlockingMoviePlaying = lambda: False

        draw()
        self.env["_travel_epoch"] += 1
        self.event("_weapon_equip_begin", self.left)
        self.assertFalse(self.pc.bInSprintState)

    def test_gunzerk_waits_for_destination_hud_and_fresh_movement_input(self):
        self.pc.WorldInfo.IsPreparingMapChange = lambda: False
        self.pc.WorldInfo.Game = NS(bLevelChange=False)
        self.pc.IsLocalPlayerController = lambda: True
        hud_blocking = True
        moving = True
        self.pc.GFxUIManager = NS(IsBlockingMoviePlaying=lambda: hud_blocking)
        self.env.update(_travel_pending=True, _travel_budget=NS(active=True),
                        _travel_close_pending=True,
                        _assist_raw_moving=lambda *a, **kw: moving)
        pi = self.pc.PlayerInput
        for release in (True, False):
            with self.subTest(release=release):
                pi.CurrentInputContextName = "DefaultOnFoot"
                hud_blocking = moving = True
                self.pc.bInSprintState = True
                self.pc.SprintModifiers[:] = [object()]
                self.event("_equip_sprint_bringup_pre", self.left)
                self.pc.SprintModifiers.clear()
                self.pc.bInSprintState = False
                self.event("_equip_sprint_bringup_post", self.left)
                self.event("_weapon_equip_begin", self.left)
                self.now += 0.30  # Past the ordinary equip retry deadline.
                self.env["_equip_sprint_frame"](self.pc)
                self.assertFalse(self.pc.bInSprintState)
                self.assertIn(1, self.env["_equip_sprint_handoffs"])

                hud_blocking = False
                self.event("_equip_sprint_context_pre", pi)
                pi.CurrentInputContextName = "Gunzerking"
                moving = False
                self.env["_equip_sprint_context_post"](self.pc, pi)
                self.env["_equip_sprint_frame"](self.pc)
                self.assertFalse(self.pc.bInSprintState)
                self.assertIn(1, self.env["_equip_sprint_handoffs"])

                moving = not release
                self.now += 0.01
                self.env["_equip_sprint_frame"](self.pc)
                self.assertEqual(self.pc.bInSprintState, not release)
                if release:
                    self.assertFalse(self.env["_equip_sprint_handoffs"])

    def test_airborne_gunzerk_waits_for_landing_and_cancels_on_release(self):
        self.pawn.Physics = 2
        self.interrupt(self.left)
        self.event("_weapon_equip_begin", self.left)
        self.now += 0.6
        self.env["_equip_sprint_frame"](self.pc)
        self.assertEqual(self.calls, [("equip", 5)])
        self.pawn.Physics = 1
        self.env["_equip_sprint_frame"](self.pc)
        self.assertEqual(self.calls[-1], ("native", False))

        self.pawn.Physics = 2
        self.interrupt(self.left)
        self.event("_weapon_equip_begin", self.left)
        self.env["_assist_raw_moving"] = lambda *a, **kw: False
        self.env["_equip_sprint_frame"](self.pc)
        self.assertFalse(self.env["_equip_sprint_handoffs"])
        self.assertFalse(self.pc.bInSprintState)

    def test_activation_retries_once_for_confirmed_context_interruption(self):
        self.interrupt(self.left)
        self.event("_weapon_equip_begin", self.left)
        self.assertTrue(self.pc.bInSprintState)
        pi = self.pc.PlayerInput
        self.event("_equip_sprint_context_pre", pi)
        self.pc.bInSprintState = False
        pi.CurrentInputContextName = "Gunzerking"
        self.env["_equip_sprint_context_post"](self.pc, pi)
        self.assertTrue(self.pc.bInSprintState)
        self.env["_equip_sprint_context_post"](self.pc, pi)
        self.assertEqual(self.calls, [("equip", 5), ("native", False), ("native", False)])

    def test_rapid_swap_replaces_same_hand_and_keeps_other_hand(self):
        self.interrupt(self.main)
        self.interrupt(self.left)
        retired = self.main
        self.main = obj(8, Instigator=self.pawn, GetStateName=lambda: "WeaponEquipping")
        self.pawn.Weapon = self.main
        self.interrupt(self.main)
        self.assertEqual(set(self.env["_equip_sprint_handoffs"]), {0, 1})
        self.assertFalse(self.env["_resume_equip_sprint"](retired))
        self.assertTrue(self.env["_resume_equip_sprint"](self.main))
        self.assertIn(1, self.env["_equip_sprint_handoffs"])

    def test_non_equip_interruptions_and_lingering_modifiers_are_guarded(self):
        cases = (
            (self.pc, "SprintModifiers", [object()]),
            (self.pawn, "bReloading", True),
            (self.pawn, "Physics", 2),
            (self.pc, "bPerformingMeleeAttack", True),
        )
        for obj, field, value in cases:
            with self.subTest(field=field):
                old = getattr(obj, field)
                setattr(obj, field, value)
                self.interrupt(self.left)
                self.assertFalse(self.env["_resume_equip_sprint"](self.left))
                setattr(obj, field, old)
        for key, value in (("_armed", False), ("_assist_raw_moving", lambda *a, **k: False),
                           ("_assist_crouching", lambda *a: True), ("_crosshair_modal_open", True),
                           ("_pose_reload_active", lambda pawn: True)):
            with self.subTest(guard=key):
                old = self.env[key]
                self.interrupt(self.left)
                self.env[key] = value
                self.assertFalse(self.env["_resume_equip_sprint"](self.left))
                self.env[key] = old
        self.assertEqual(self.calls, [])

    def test_stale_owner_and_missing_bringup_exit_cannot_recover(self):
        self.interrupt(self.left)
        old_pi = self.pc.PlayerInput
        self.pc.PlayerInput = obj(99)
        self.assertFalse(self.env["_resume_equip_sprint"](self.left))
        self.pc.PlayerInput = old_pi
        self.now += 0.26
        self.assertFalse(self.env["_resume_equip_sprint"](self.left))
        self.pc.bInSprintState = True
        self.event("_equip_sprint_bringup_pre", self.left)
        self.pc.bInSprintState = False
        self.assertFalse(self.env["_resume_equip_sprint"](self.left))
        self.assertEqual(self.calls, [])


class ReticleHandoffTests(unittest.TestCase):
    def setUp(self):
        self.now = 0.0
        self.movie = obj(10)
        fields = dict(bWeaponPutDown=False, IsEquipping=lambda: False, IsPuttingDown=lambda: False)
        self.main = obj(11, **fields)
        self.offhand = obj(12, **fields)
        self.pc = obj(1, Pawn=obj(2, Weapon=self.main, OffHandWeapon=self.offhand),
                      myHUD=NS(HUDMovie=self.movie), bInSprintState=True)
        self.widgets = []
        self.env = dict(
            _active=True, _cfg_crosshair=True, _crosshair_modal_open=False,
            _offhand_crosshair=None, _offhand_crosshair_retry=None, _offhand_native_updates={},
            _offhand_associated_widget=None,
            _crosshair_forced_widget=None, _crosshair_inner_visible=None,
            _weapon_transition_addresses=set(), _same=same, _pc=lambda: self.pc,
            _local_pc=lambda obj: self.pc if obj is self.pc else None,
            _crosshair_eligible=lambda pc, *a, **kw: pc.bInSprintState,
            _fast_zoomed=lambda *args: False, PLAYER_TICK="tick",
            CROSSHAIR_UPDATE="update", Type=NS(POST="post", POST_UNCONDITIONAL="post-any"),
            _set_hook=lambda tag, active, *args: active,
            _set_optional_hook=lambda tag, active, *args: active, Block="block",
            time=NS(perf_counter=lambda: self.now),
            unrealsdk=NS(find_all=lambda *a: self.widgets,
                        unreal=NS(WeakPointer=lambda obj: lambda: obj),
                        hooks=NS(prevent_hooking_direct_calls=nullcontext)))
        load_definitions(MOD / "__init__.py", [
            "_crosshair_weapon_ready", "_right_crosshair_ready", "_crosshair_widget_ready",
            "_hide_weapon_crosshair", "_offhand_crosshair_matches", "_offhand_crosshair_eligible",
            "_release_offhand_crosshair", "_release_swapping_reticle", "_select_offhand_crosshair",
            "_show_offhand_crosshair", "_offhand_crosshair_visibility", "_offhand_crosshair_update",
            "_stop_offhand_crosshair_retry", "_schedule_offhand_crosshair", "_offhand_crosshair_tick",
            "_release_crosshair_visibility", "_take_crosshair_ownership", "_crosshair_associate_post",
        ], self.env)

    def widget(self, address, weapon, dual=True):
        def clip(address):
            c = obj(address, visible=False)
            c.SetVisible = lambda v: setattr(c, "visible", v)
            c.GetBool = lambda key: c.visible
            return c
        w = obj(address, WWeap=weapon, MyHUDMovie=self.movie, bDualWieldCrosshair=dual,
                bScopeCrosshair=False, OuterClip=clip(100+address), InnerClip=clip(200+address),
                CrosshairVisibility=0, events=[])
        w.SetCrosshairVisibility = lambda bVisible: w.OuterClip.SetVisible(bVisible)
        w.UpdateCrosshairFrame = lambda: w.events.append("frame")
        w.Update = lambda dt: w.events.append("native-update")
        return w

    def test_late_association_and_duplicate_widget_use_native_updates(self):
        stale = self.widget(20, self.offhand)
        live = self.widget(21, None)
        self.widgets.extend([stale, live])
        self.pc.Pawn.OffHandWeapon = None
        self.env["_schedule_offhand_crosshair"](self.pc, pending_draw=True)
        tick = self.env["_offhand_crosshair_tick"]
        tick(self.pc, None, None, None)
        self.assertFalse(live.OuterClip.visible)
        self.pc.Pawn.OffHandWeapon = self.offhand
        live.WWeap = self.offhand
        self.assertIsNone(self.env["_select_offhand_crosshair"](self.pc, self.widgets))
        self.env["_offhand_crosshair_update"](live, None, None, None)
        self.now = 0.11
        tick(self.pc, None, None, None)
        self.assertTrue(live.OuterClip.visible)
        self.assertFalse(stale.OuterClip.visible)
        self.assertEqual(live.events, ["frame"])
        live.OuterClip.SetVisible(False)
        self.now = 0.22
        tick(self.pc, None, None, None)
        self.assertTrue(live.OuterClip.visible)
        self.assertEqual(live.events, ["frame"])
        self.pc.bInSprintState = False
        self.now = 0.33
        tick(self.pc, None, None, None)
        self.assertFalse(live.OuterClip.visible)
        self.assertIsNone(self.env["_offhand_crosshair"])

    def test_native_offhand_association_waits_for_its_own_clips(self):
        stale = self.widget(20, self.offhand)
        live = self.widget(21, self.offhand)
        self.widgets.extend((stale, live))
        clip, live.InnerClip = live.InnerClip, None
        self.env["_crosshair_associate_post"](live, None, None, None)
        tick = self.env["_offhand_crosshair_tick"]
        tick(self.pc, None, None, None)
        self.assertFalse(stale.OuterClip.visible)
        self.assertFalse(live.OuterClip.visible)
        live.InnerClip = clip
        self.now = 0.11
        tick(self.pc, None, None, None)
        self.assertTrue(live.OuterClip.visible)
        self.assertFalse(stale.OuterClip.visible)
        live.InnerClip.SetVisible(False)
        self.now = 0.22
        tick(self.pc, None, None, None)
        self.assertTrue(live.InnerClip.visible)

    def test_right_only_swap_hides_outgoing_launcher_and_keeps_left(self):
        launcher = self.widget(20, self.main, dual=False)
        self.movie.CrosshairWidget = launcher
        left = self.widget(21, self.offhand)
        self.env["_take_crosshair_ownership"](launcher)
        self.env["_show_offhand_crosshair"](self.pc, left)
        self.main.IsPuttingDown = lambda: True
        self.env["_release_swapping_reticle"](self.main)
        self.assertFalse(launcher.OuterClip.visible)
        self.assertTrue(left.OuterClip.visible)
        self.assertNotIn("native-update", launcher.events)
        replacement = obj(99, bWeaponPutDown=False, IsEquipping=lambda: False,
                          IsPuttingDown=lambda: False)
        self.pc.Pawn.Weapon = replacement
        self.assertFalse(self.env["_right_crosshair_ready"](self.pc, launcher))
        launcher.WWeap = replacement
        # A missed equip EndState should not leave the reticle hidden when
        # the current weapon and HUD association have both settled.
        self.env["_weapon_transition_addresses"].add(replacement._get_address())
        self.assertTrue(self.env["_right_crosshair_ready"](self.pc, launcher))
        self.env["_take_crosshair_ownership"](launcher)
        self.assertTrue(launcher.OuterClip.visible)
        self.assertTrue(left.OuterClip.visible)


if __name__ == "__main__":
    unittest.main()
