"""Behavior checks against the installed-build performance candidate."""
from types import SimpleNamespace as NS
import unittest

from test_combat_bridge import MOD, load_definitions
import test_motion_bridge


def obj(address, **fields):
    return NS(_get_address=lambda: address, **fields)


class InstalledPerformanceTests(unittest.TestCase):
    def test_armed_gate_reads_live_pawn_once(self):
        fixture = test_motion_bridge.MotionBridgeTests()
        ns, _, _, pi, pawn, calls = fixture.supervisor()
        adapter, native, _, _, _, _ = fixture.bridge()
        native.motion_update = lambda *a: (
            True, (1.0, 0.0, True, True, False, False), None, False)
        class Controller:
            PlayerInput = pi
            bDuck = False
            reads = 0

            @property
            def Pawn(self):
                self.reads += 1
                return pawn

            def _get_address(self):
                return 10

        pc = Controller()
        pawn.bWantsToCrouch = pawn.bIsCrouched = False
        ns.update(_pc=lambda: pc, _precision=adapter, _cfg_assist=True,
                  _armed=True, _crouch_sprint_pause_until=0, _assist_airborne_hold=False,
                  time=NS(perf_counter=lambda: self.fail("No deadline to time")))
        load_definitions(MOD / "__init__.py",
                         ["_assist_crouching", "_crouch_sprint_paused"], ns)
        self.assertEqual(ns["_allows"](pi, None, None, None), ("block", True))
        self.assertEqual(pc.reads, 1)
        pawn.bWantsToCrouch = True
        self.assertEqual(ns["_allows"](pi, None, None, None), ("block", False))

    def test_fire_rearm_uses_sprint_state_and_waits_for_weapon(self):
        events = []
        pc = NS(bInSprintState=True)
        ns = dict(_active=True, _cfg_assist=True, _armed=True,
                  _fire_requested=False, _deathtrap_skill_active=False,
                  _stop_fire_state_recovery=lambda: events.append("stop"),
                  _weapons_still_firing=lambda pc: True,
                  _set_try_sprint=lambda pi, value: events.append(value),
                  _assist_recover_or_schedule=lambda pc: events.append("recover"))
        load_definitions(MOD / "__init__.py", ["_rearm_after_fire"], ns)
        self.assertTrue(ns["_rearm_after_fire"](pc))
        pc.bInSprintState = False
        pc.PlayerInput = obj(20)
        self.assertFalse(ns["_rearm_after_fire"](pc))
        ns["_weapons_still_firing"] = lambda pc: False
        ns["_rearm_after_fire"](pc)
        self.assertEqual(events, ["stop", False, "stop", "recover"])

    def test_weapon_end_filters_remote_and_keeps_tracked_putdown(self):
        events = []
        local = obj(1)
        remote = obj(2)
        old_weapon = obj(3, Instigator=None)
        offhand = obj(4, Instigator=None)
        other_weapon = obj(5, Instigator=remote)
        local.Weapon = None
        local.OffHandWeapon = offhand
        pc = NS(Pawn=local, bInSprintState=True)
        ns = dict(_active=True, _cfg_assist=False, _cfg_crosshair=True,
                  _weapon_transition_addresses={3},
                  _weapon_transition_refs={3: lambda: old_weapon},
                  _pc=lambda: pc, _local_pawn_address=1,
                  _schedule_crosshair_resync=lambda *a, **kw: events.append("repair"))
        load_definitions(MOD / "__init__.py",
                         ["_same", "_is_local_pawn", "_weapon_transition_end"], ns)
        end = ns["_weapon_transition_end"]
        end(other_weapon, None, None, None)
        self.assertEqual(events, [])
        end(old_weapon, None, None, None)
        end(offhand, None, None, None)
        self.assertEqual(events, ["repair", "repair"])
        self.assertEqual(ns["_weapon_transition_addresses"], set())
        self.assertEqual(ns["_weapon_transition_refs"], {})

    def test_crosshair_direct_lookup_and_replacement_bypass_scan_interval(self):
        scans = []
        movie = obj(10, CrosshairWidget=None)
        pc = NS(myHUD=NS(HUDMovie=movie), WorldInfo=NS(TimeSeconds=1.0))
        ns = dict(_crosshair_widget=None, _crosshair_movie_address=0,
                  _crosshair_retry_at=0.0,
                  _owned_crosshair_widget=lambda movie: movie.CrosshairWidget,
                  _find_crosshair_widget=lambda movie: scans.append(movie._get_address()),
                  unrealsdk=NS(unreal=NS(WeakPointer=lambda obj: lambda: obj)))
        load_definitions(MOD / "__init__.py", ["_same", "_get_crosshair_widget"], ns)
        lookup = ns["_get_crosshair_widget"]
        self.assertIsNone(lookup(pc, retry_interval=0.05))
        pc.WorldInfo.TimeSeconds = 1.01
        self.assertIsNone(lookup(pc, retry_interval=0.05))
        widget = obj(20, MyHUDMovie=movie)
        movie.CrosshairWidget = widget
        self.assertIs(lookup(pc, retry_interval=0.05), widget)
        self.assertEqual(scans, [10])
        pc.myHUD.HUDMovie = obj(11, CrosshairWidget=None)
        self.assertIsNone(lookup(pc, retry_interval=0.05))
        self.assertEqual(scans, [10, 11])


    def test_resync_falls_back_when_registered_hud_never_calls(self):
        hooks = {}
        repairs = []
        widget = obj(20, WWeap=obj(30), MyHUDMovie=obj(10),
                     bScopeCrosshair=False, bDualWieldCrosshair=False)
        pc = obj(1, bInSprintState=True, WorldInfo=NS(TimeSeconds=1.0))

        def set_hook(tag, enabled, path, phase, callback):
            if enabled:
                hooks[tag] = callback
            else:
                hooks.pop(tag, None)
            return enabled

        ns = dict(_hooks=hooks, _active=True, _cfg_crosshair=True,
                  _crosshair_resync_ticks=0, _crosshair_resync_attempts=0,
                  _crosshair_resync_truthful_cache=False, _crosshair_resync_force_tick=False,
                  _crosshair_resync_native_widget_address=0, _crosshair_resync_missed_updates=0,
                  _crosshair_resync_last_request=None, _crosshair_widget=None,
                  _crosshair_forced_widget=lambda: widget,
                  _crosshair_movie_address=0, _crosshair_retry_at=0,
                  CROSSHAIR_UPDATE="Update", PLAYER_TICK="PlayerTick",
                  Type=NS(POST_UNCONDITIONAL=0),
                  _set_hook=set_hook, _set_optional_hook=set_hook,
                  _local_pc=lambda obj: pc if obj is pc else None,
                  _clear_crosshair_widget=lambda: None,
                  _get_crosshair_widget=lambda *a, **k: widget,
                  _crosshair_widget_ready=lambda pc, widget: widget is not None,
                  _refresh_crosshair_native_presentation=lambda *a: True,
                  _reassert_crosshair_visible=lambda *a, **k: repairs.append("repair") or True,
                  unrealsdk=NS(unreal=NS(WeakPointer=lambda obj: lambda: obj)))
        load_definitions(MOD / "__init__.py",
                         ["_same", "_stop_crosshair_resync", "_schedule_crosshair_resync",
                          "_crosshair_resync_tick", "_crosshair_resync_update"], ns)
        schedule = ns["_schedule_crosshair_resync"]
        schedule(pc, ticks=1, preserve_widget=True, widget=widget)
        schedule(pc, ticks=1, preserve_widget=True, widget=widget)
        self.assertEqual(repairs, ["repair"])
        tick = hooks["crosshair-resync-tick"]
        tick(obj(2), None, None, None)
        self.assertEqual(ns["_crosshair_resync_missed_updates"], 0)
        for _ in range(2):
            tick(pc, None, None, None)
            self.assertIn("crosshair-resync-update", hooks)
        tick(pc, None, None, None)
        self.assertEqual(repairs, ["repair", "repair"])
        self.assertEqual(hooks, {})

    def test_definite_native_blocker_keeps_intent_reset_without_permission(self):
        pawn = obj(30, Physics=1)
        pi = obj(20)
        pc = obj(10, PlayerInput=pi, Pawn=pawn)
        observations = []
        snapshot = (False, True, False, True, False, False, False, False)
        precision = NS(intent_available=lambda **kw: True,
                       combat_observe=lambda *a, **k: (None, snapshot),
                       intent_observe=lambda *a, **k:
                       observations.append(k) or (4, True, False, False, False))
        ns = dict(_cfg_native_precision=True, _precision=precision, _armed=True,
                  _deathtrap_skill_active=False, _travel_epoch=4,
                  _assist_can_retry=lambda *a, **k: self.fail("known blocked"))
        load_definitions(MOD / "__init__.py", ["_native_assist_recovery"], ns)
        self.assertTrue(ns["_native_assist_recovery"](
            pc, pi, pawn=pawn, context_validated=True))
        self.assertEqual(len(observations), 1)
        self.assertFalse(observations[0]["permitted"])
