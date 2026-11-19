"""High-performance native-sprint omnidirectional movement for Borderlands 2."""
from __future__ import annotations

import math
import secrets
import time

import unrealsdk
from mods_base import SETTINGS_DIR, BoolOption, GroupedOption, Mod, SliderOption, SpinnerOption, build_mod, get_ordered_mod_list, get_pc
from unrealsdk import logging
from unrealsdk.hooks import Block, Type, Unset

from .fire_pose import AdaptiveFirePose, DUAL_FIRE_RELEASE_DELAY, on_foot, firing_pose_context
from .travel_input import HeldInputHandoff
from .recovery_budget import RecoveryBudget
from .interruptions import ActionSkid, action_interrupted
from .native_hipfire import NativeHipFirePatch
from .precision_native_adapter import PrecisionSprint
from .reversal import INSTANT, RESPONSIVE, VANILLA, Reversal
from .coyote import CoyoteTime


PRESSED = "WillowGame.WillowPlayerInput:SprintPressed"
RELEASED = "WillowGame.WillowPlayerInput:SprintReleased"
DUCK_PRESSED = "WillowGame.WillowPlayerInput:DuckPressed"
DUCK_RELEASED = "WillowGame.WillowPlayerInput:DuckReleased"
SPAWNED = "WillowGame.WillowPlayerController:SpawningProcessComplete"
PRE_CLIENT_TRAVEL = "WillowGame.WillowPlayerController:PreClientTravel"
NOTIFY_LOADED_WORLD = "Engine.PlayerController:NotifyLoadedWorld"
FAST_TRAVEL_REQUEST = "WillowGame.WillowPlayerController:ServerTeleportPlayerToStation"
FAST_TRAVEL_LOADED = "WillowGame.WillowPlayerController:WillowClientDisableLoadingMovie"
INPUT_ALLOWS_SPRINT = "WillowGame.WillowPlayerInput:InputAllowsSprint"
CONTINUE = "WillowGame.WillowPlayerPawn:CanContinueSprinting"
CAN_SPRINT = "WillowGame.WillowPlayerPawn:CanSprint"
SHARED_WEAPON_ACTION = "WillowGame.WillowPlayerController:PerformSharedWeaponActions"
FIRING_STATE = "Engine.Weapon:WeaponFiring.IsFiring"
START_FIRE = "WillowGame.WillowPlayerController:StartFire"
STOP_FIRE = "WillowGame.WillowPlayerController:StopFire"
WEAPON_FIRING_END_PATHS = (
    "WillowGame.WillowWeapon:WeaponFiring.EndState",
    "Engine.Weapon:WeaponFiring.EndState",
)
ADD_VELOCITY = "Engine.Pawn:AddVelocity"
BEGIN_SPRINT = "WillowGame.WillowPlayerController:BeginSprint"
END_SPRINT = "WillowGame.WillowPlayerController:EndSprint"
DO_SPRINT = "WillowGame.WillowPlayerPawn:DoSprint"
WEAPON_ACTION_COMPLETE = "WillowGame.WillowPlayerController:WeaponActionComplete"
POSE_RELOAD_BEGIN = "WillowGame.WillowWeapon:WeaponReloading.BeginState"
POSE_RELOAD_END = "WillowGame.WillowWeapon:WeaponReloading.EndState"
# Zer0's Execute restores controls at a separate skill boundary, before the
# normal weapon-action-complete cleanup at the end of the melee animation.
ZERO_EXECUTE_CONTROLS_RESTORED = "WillowGame.ExecuteActionSkill:RestorePlayerMovementPostExecute"
ZERO_EXECUTE_CLEANUP = "WillowGame.ExecuteActionSkill:CleanUpExecute"
ZERO_EXECUTE_PHYSICS_RESTORE = "WillowGame.ExecuteActionSkill:RestorePhysicsPostExecute"
MELEE_DISPATCH = "WillowGame.WillowPlayerController:Behavior_Melee"
# All stock character melee inputs are routed through Behavior_Melee; overridden
# attacks may never call the ordinary MeleeAttack function.
INIT_ACCURACY = "WillowGame.WillowPlayerController:InitAccuracyFromWeapon"
ATTRIBUTE_MODIFIER = "Engine.AttributeDefinitionBase:AddAttributeModifier"
PLAYER_TICK = "WillowGame.WillowPlayerController:PlayerTick"
INPUT_FRAME = "WillowGame.WillowPlayerInput:PreProcessInput"
POST_PLAYER_INPUT = "WillowGame.WillowPlayerInput:PlayerInput"
# SDK hook dispatch matches the canonical path, with the state after the colon.
WALKING_PROCESS_MOVE = "WillowGame.WillowPlayerController:PlayerWalking.ProcessMove"
# Called between Engine.PlayerInput.RawJoy snapshot and native PIProcessInput.
# The old WillowPlayerInput.PlayerInput PRE hook was too early (overwritten by super).
CROSSHAIR_UPDATE = "WillowGame.HUDWidget_Crosshairs:Update"
CROSSHAIR_VISIBILITY = "WillowGame.HUDWidget_Crosshairs:SetCrosshairVisibility"
CROSSHAIR_ASSOCIATE = "WillowGame.HUDWidget_Crosshairs:AssociateWeapon"
CROSSHAIR_PAUSE_STATE = "WillowGame.WillowPlayerController:SetGFxPauseMenuOpenState"
CROSSHAIR_MODAL_OPENING = "WillowGame.WillowPlayerController:ModalGameMenuOpening"
CROSSHAIR_CROUCH = "WillowGame.WillowPlayerPawn:StartCrouch"
CROSSHAIR_UNCROUCH = "WillowGame.WillowPlayerController:FinishUncrouch"
CROSSHAIR_FALLING = "WillowGame.WillowPlayerPawn:Falling"
CROSSHAIR_LANDED = "WillowGame.WillowPlayerPawn:Landed"
ACTION_SKILL_STARTED = "WillowGame.ActionSkill:OnActionSkillStarted"
ACTION_SKILL_ENDED = "WillowGame.ActionSkill:OnActionSkillEnded"
SET_INPUT_CONTEXT = "WillowGame.WillowPlayerInput:SetInputContext"
JUMP_INPUT = "WillowGame.WillowPlayerInput:Jump"
DO_JUMP = "WillowGame.WillowPlayerPawn:DoJump"
BASE_CAN_JUMP = "Engine.Pawn:CanJump"
SET_PHYSICS = "Engine.Actor:SetPhysics"

WEAPON_BRINGUP = "WillowGame.WillowWeapon:InitializeWeaponAfterBringUp"
WEAPON_DROP = "WillowGame.WillowWeapon:DropFrom"
WEAPON_STATE_PATHS = (
    (
        "equip-begin",
        (
            "WillowGame.WillowWeapon:WeaponEquipping.BeginState",
            "Engine.Weapon:WeaponEquipping.BeginState",
        ),
        True,
    ),
    (
        "equip-end",
        (
            "WillowGame.WillowWeapon:WeaponEquipping.EndState",
            "Engine.Weapon:WeaponEquipping.EndState",
        ),
        False,
    ),
    (
        "putdown-begin",
        (
            "WillowGame.WillowWeapon:WeaponPuttingDown.BeginState",
            "Engine.Weapon:WeaponPuttingDown.BeginState",
        ),
        True,
    ),
    (
        "putdown-end",
        (
            "WillowGame.WillowWeapon:WeaponPuttingDown.EndState",
            "Engine.Weapon:WeaponPuttingDown.EndState",
        ),
        False,
    ),
)

HIP_FIRE_AVAILABLE = True
BASELINE_AVAILABLE = True

_hooks = {}
_known_functions = set()
# The SDK can report False after inserting a hook, and only exposes identifier
# presence, not callback identity. Isolate this module instance's identifiers
# from predictable collisions while retaining the SDK's compatibility fallback.
_hook_instance_nonce = secrets.token_hex(16)
_hook_sequence = 0
_active = False

_pc_ref = None
_pi_ref = None
_pc_address = 0
_pi_address = 0
_local_pawn_address = 0
_weapon_address = 0

_armed = True
_assist_press_held = False
_zero_execute_waiting_cleanup = None
_zero_execute_seed_pending = None
_zero_execute_early_ready = False  # Execute control restored and native sprint permits
_zero_final_physics_snapshot = None  # (skill, pc, pawn, horizontal, tick)
_zero_deception_cast_edge = None      # (pc address, real-time expiry)
_crouch_sprint_pause_until = 0.0
_pause_sprint_grace_until = 0.0
_ads_sprint_edge = None
_combat_action_active = False
_combat_fire_mask = 0
_combat_fire_tail = None  # (controller, pawn, offhand addresses, grace end, deadline)
_try_sprint_written = None
_assist_recovery_ready_frames = 0
_assist_airborne_hold = False
_gate_last_moving_at = None
_gate_sprinting = False
_reversal_last_at = None
_native_reversal_was_active = False

_reversal = Reversal()
_native_hipfire = NativeHipFirePatch()
_native_hipfire_active = False
_requested_fire_entry = False
_active_fire_entry = False
_sprint_fire_entry_remaining = 0
_fire_pose = AdaptiveFirePose()
_action_skid = ActionSkid()
_travel_input = HeldInputHandoff()
_activation_input_frame = None  # same-pawn, one native input frame after Gunzerk context entry
_precision = PrecisionSprint()

# Kept separately from airborne/action interruption handling. A map transition
# can clear native pending sprint after Omni has already remembered Sprint.
_travel_pending = False
_travel_mode = None
_travel_budget = RecoveryBudget()
_travel_ready_ticks = 0
_travel_initial_rearm_at = 0.0
_travel_modal_open = False
_travel_close_pending = False
_travel_close_ready_ticks = 0
_travel_generation = None
_travel_epoch = 0
_travel_operation = 0
_travel_native_owner = None
_travel_ready_validated = False
_travel_verified_sprint = False
_travel_last_rearm_at = 0.0
_travel_walk_hook_live = False
_travel_walk_last_attempt = 0.0
_melee_nonforward_event_at = 0.0
_melee_nonforward_controller = 0

_deathtrap_skill_active = False
_deathtrap_skill_ref = None
_deathtrap_context_seen = False
_deathtrap_recovery_ready_at = 0.0
_center_origin_velocity = None
_center_transition_origin = None
_center_transition_target = None
_center_transition_speed = 0.0
_center_transition_started = 0.0
# Python reference observer for center crossings in the production input hook.
# Native mode runs the same state machine inside the existing C++ gate call.
_center_input_previous = None
_center_input_started = None

_fire_requested = False
_pose_offhand_requested = False
_pose_reload_addresses = set()
_pose_reload_retry = 0
_fire_until = 0.0
_pose_release_at = 0.0
_hip_hooks_until = 0.0
_hip_hooks_live = False
_weapon_firing_end_available = False

_knockback_until = 0.0
_continuation_depth = 0

_weapon_transition_addresses = set()
_equip_sprint_handoffs = {}  # hand -> exact equip record, valid only for a bounded local handoff
_drop_sprint_scope = None  # exact local equip owner and deadline for a synchronous DropFrom
_weapon_transition_refs = {}
_weapon_state_events_complete = False

_sprint_depth = 0
_suppressed = []
_restoring = False
_accuracy_rules = {
    "AccuracyMinValue": 15.0,
    "AccuracyMaxValue": 15.0,
    "AccuracyOnIdleRegenerationRate": 3.0,
}

_crosshair_widget = None
_crosshair_forced_widget = None
_offhand_crosshair = None  # weak widget and original inner clip visibility
_offhand_crosshair_retry = None  # pawn identity, deadline, next sample
_offhand_associated_widget = None  # exact widget from native AssociateWeapon
_offhand_native_updates = {}  # exact widgets updated since the last retry sample
_crosshair_inner_visible = None
_crosshair_movie_address = 0
_crosshair_weapon_address = 0
_crosshair_retry_at = 0.0
_crosshair_failure_logged = False
_crosshair_strategy = "unknown"  # unknown -> visibility/update/tick
_crosshair_visibility_available = False
_crosshair_resync_ticks = 0
_crosshair_resync_attempts = 0
_crosshair_resync_truthful_cache = False
_crosshair_resync_force_tick = False
_crosshair_resync_missed_updates = 0
_crosshair_resync_last_request = None
_crosshair_resync_native_widget_address = 0
_crosshair_modal_open = False

_cfg_omni = True
_cfg_assist = True
_cfg_starting = True
_cfg_direction = VANILLA
_cfg_gap = 0.100
_cfg_hip_fire = True
_cfg_begin_fire_sprint = True
_cfg_crosshair = True
_cfg_baseline = True
_cfg_precision = True
_cfg_precision_strength = 0.70
_cfg_native_precision = False
_cfg_coyote = True
_cfg_coyote_window = 0.200
_coyote = CoyoteTime()
_coyote_ready = False
_coyote_attempt = None
_coyote_failure_logged = False
_coyote_traveling = False


class _DirectionOption(SpinnerOption):
    """Direction selector with compatibility for the old Vanilla label."""

    def _from_json(self, value):
        if isinstance(value, str) and value.startswith("Vanilla"):
            value = VANILLA
        super()._from_json(value)


omni = BoolOption(
    identifier="omni",
    value=True,
    display_name="Omnidirectional Sprint",
    description="Allow sprinting in any horizontal direction.",
)
assist = BoolOption(
    identifier="assist",
    value=True,
    display_name="Sprint Assist",
    description="Toggle remembered Sprint and Walk modes with your normal Sprint action.",
)
starting = BoolOption(
    identifier="starting",
    value=True,
    display_name="Starting Mode",
    true_text="Sprint",
    false_text="Walk",
    description="Choose the starting Sprint Assist mode on enable or a new character.",
)
direction = _DirectionOption(
    identifier="direction",
    value=VANILLA,
    choices=[VANILLA, RESPONSIVE, INSTANT],
    wrap_enabled=True,
    display_name="Direction Changes",
    description="Sprint direction changes: native momentum, faster turns, or near-instant reversal.",
)
gap_window = SliderOption(
    identifier="gap_window",
    value=100,
    min_value=0,
    max_value=250,
    step=10,
    is_integer=True,
    display_name="Center Gap Window (ms)",
    description="Keep sprint briefly through neutral input, including gaps between key presses.",
)
coyote_time = BoolOption(
    identifier="coyote_time",
    value=True,
    display_name="Coyote Time",
    description="Allow one normal jump shortly after walking off a ledge.",
)
coyote_window = SliderOption(
    identifier="coyote_window",
    value=200,
    min_value=0,
    max_value=1000,
    step=10,
    is_integer=True,
    display_name="Coyote Time Window (ms)",
    description="Time after leaving a ledge to jump; 0 ms disables the window.",
)
hip_fire = BoolOption(
    identifier="hip_fire",
    value=True,
    display_name="Sprint While Hip-Firing",
    true_text="On",
    false_text="Off",
    description="Continue an existing sprint while firing from the hip.",
)
begin_fire_sprint = BoolOption(
    identifier="begin_fire_sprint",
    value=True,
    display_name="Begin Sprint While Firing",
    true_text="On",
    false_text="Off",
    description="Start sprinting while hip-firing. Requires Sprint While Hip-Firing; re-enable mod to apply.",
)
crosshair = BoolOption(
    identifier="crosshair",
    value=True,
    display_name="Crosshair While Sprinting",
    true_text="On",
    false_text="Off",
    description="Show the normal weapon crosshair while sprinting.",
)
baseline = BoolOption(
    identifier="baseline",
    value=True,
    display_name="Walking Accuracy While Sprinting",
    description="Use your weapon's walking accuracy while sprinting.",
)
precision = BoolOption(
    identifier="precision",
    value=True,
    display_name="Precision Sprint",
    description="Scale sprint speed with partial controller-stick input. Full stick stays unchanged.",
)
native_precision = BoolOption(
    identifier="native_precision",
    value=False,
    display_name="Movement Backend",
    true_text="C++",
    false_text="Python",
    description="Python is the default. Try C++ for potentially better performance.",
)
precision_strength = SliderOption(
    identifier="precision_strength",
    value=70,
    min_value=0,
    max_value=100,
    step=5,
    is_integer=True,
    display_name="Precision Strength (%)",
    description="Set partial-stick sprint slowdown. Requires Precision Sprint; full stick stays unchanged.",
)





def _sync_config(overrides=None):
    global _cfg_omni, _cfg_assist, _cfg_starting, _cfg_direction, _cfg_gap
    global _cfg_hip_fire, _cfg_begin_fire_sprint
    global _cfg_crosshair, _cfg_baseline
    global _cfg_precision, _cfg_precision_strength, _cfg_native_precision
    global _cfg_coyote, _cfg_coyote_window
    if overrides is None:
        overrides = {}

    def value(option):
        return overrides.get(option.identifier, option.value)

    _cfg_omni = bool(value(omni))
    _cfg_assist = bool(value(assist))
    _cfg_starting = bool(value(starting))
    _cfg_direction = str(value(direction))
    _cfg_gap = max(0.0, float(value(gap_window)) / 1000.0)
    _cfg_hip_fire = bool(value(hip_fire))
    _cfg_begin_fire_sprint = bool(value(begin_fire_sprint))
    _cfg_crosshair = bool(value(crosshair))
    _cfg_baseline = bool(value(baseline))
    _cfg_precision = bool(value(precision))
    _cfg_precision_strength = max(0.0, min(1.0, float(value(precision_strength)) / 100.0))
    _cfg_native_precision = bool(value(native_precision))
    _cfg_coyote = bool(value(coyote_time))
    _cfg_coyote_window = max(0.0, min(1.000, float(value(coyote_window)) / 1000.0))




def _same(a, b):
    return a is not None and b is not None and a._get_address() == b._get_address()


def _cache_local(pc):
    global _pc_ref, _pi_ref, _pc_address, _pi_address, _local_pawn_address
    if pc is None:
        _coyote.invalidate()
        _pc_ref = _pi_ref = None
        _pc_address = _pi_address = _local_pawn_address = 0
        return None

    pi = pc.PlayerInput
    pawn = pc.Pawn
    pc_address = pc._get_address()
    pi_address = 0 if pi is None else pi._get_address()
    pawn_address = 0 if pawn is None else pawn._get_address()
    if (pc_address, pi_address, pawn_address) != (
            _pc_address, _pi_address, _local_pawn_address):
        _coyote.invalidate()
    _pc_ref = unrealsdk.unreal.WeakPointer(pc)
    _pi_ref = None if pi is None else unrealsdk.unreal.WeakPointer(pi)
    _pc_address, _pi_address, _local_pawn_address = pc_address, pi_address, pawn_address
    return pc


def _pc(*, refresh=False):
    pc = None if _pc_ref is None else _pc_ref()
    if not refresh and pc is not None:
        return pc
    try:
        return _cache_local(get_pc(possibly_loading=True))
    except Exception:
        return pc


def _local_pc(obj):
    if obj is None:
        return None
    if _pc_address and obj._get_address() == _pc_address:
        return _pc()
    pc = _pc(refresh=not _pc_address)
    return pc if pc is not None and _same(obj, pc) else None


def _local_input(obj):
    if obj is None:
        return None
    if _pi_address and obj._get_address() == _pi_address:
        return None if _pi_ref is None else _pi_ref()
    pc = _pc(refresh=not _pc_address)
    if pc is not None and pc.PlayerInput is not None and _same(obj, pc.PlayerInput):
        _cache_local(pc)
        return pc.PlayerInput
    # The last level's PlayerInput may remain alive after travel even when the
    # destination is running a new one. Reacquire ONLY during bounded travel.
    if _travel_pending:
        fresh = _pc(refresh=True)
        if fresh is not None and fresh.PlayerInput is not None and _same(obj, fresh.PlayerInput):
            _cache_local(fresh)
            return fresh.PlayerInput
    return None


def _is_local_pawn(obj):
    return (
        obj is not None
        and bool(_local_pawn_address)
        and obj._get_address() == _local_pawn_address
    )


def _hook_id(tag):
    global _hook_sequence
    _hook_sequence += 1
    return "OmniMovement/" + tag + "/" + _hook_instance_nonce + "/" + str(_hook_sequence)


def _function_exists(path):
    # UFunction objects are stable once resolved. Cache only positive results so
    # repeated sprint/presentation hook synchronization does not keep crossing
    # into GObjects for functions we already proved exist. Never cache failures:
    # a class may become available later during startup/travel.
    if path in _known_functions:
        return True
    try:
        exists = unrealsdk.find_object("Function", path) is not None
    except Exception:
        return False
    if exists:
        _known_functions.add(path)
    return exists


def _set_hook(tag, enabled, path, kind, callback):
    current = _hooks.get(tag)
    if not enabled:
        if current is not None:
            try:
                unrealsdk.hooks.remove_hook(current[0], current[1], current[2])
                if unrealsdk.hooks.has_hook(*current):
                    raise RuntimeError('hook remains registered')
            except Exception as exc:
                logging.warning('omnimovement: hook removal deferred ' + tag + ': ' + repr(exc))
                return False
            _hooks.pop(tag, None)
        return False

    if current is not None:
        try:
            if unrealsdk.hooks.has_hook(*current):
                return True
        except Exception as exc:
            logging.warning('omnimovement: hook ownership check failed ' + tag + ': ' + repr(exc))
            return False
        # The SDK no longer has our recorded callback. Retry registration
        # rather than reporting a stale ledger entry as an active feature.
        _hooks.pop(tag, None)

    ident = _hook_id(tag)
    try:
        occupied = unrealsdk.hooks.has_hook(path, kind, ident)
    except Exception as exc:
        # A failed preflight is not a post-install exception. Never claim it.
        return False
    if occupied:
        logging.warning('omnimovement: hook identifier already in use: ' + ident)
        return False
    # True is unambiguous; False is unreliable on an already-populated
    # function/stage list in the SDK revision shipped with UnrealSDK 3.2.0.
    # Keep positive-return provenance if the later presence check throws.
    added = None
    try:
        added = unrealsdk.hooks.add_hook(path, kind, ident, callback)
    except Exception as exc:
        # Preserve the established post-install-exception cleanup contract.
        # This is not evidence that a False add return owns an existing ID.
        try:
            if not unrealsdk.hooks.has_hook(path, kind, ident):
                return False
        except Exception:
            return False
    # UnrealSDK 3.2.0 can return False *after installing* a new hook when a
    # different identifier already occupies the same function/stage list.
    # The preflight found our ID absent; use postflight presence, not the
    # unreliable add_hook return, to decide whether registration succeeded.
    try:
        confirmed = unrealsdk.hooks.has_hook(path, kind, ident)
    except Exception as exc:
        if added is True:
            # add_hook(True) is the SDK's positive ownership evidence.
            # Preserve the ledger for eventual cleanup instead of orphaning
            # an installed callback when the verification query fails.
            _hooks[tag] = (path, kind, ident)
            return True
        return False
    if confirmed:
        _hooks[tag] = (path, kind, ident)
        return True
    logging.warning('omnimovement: hook registration unconfirmed ' + ident)
    return False


def _set_optional_hook(tag, enabled, path, kind, callback):
    if not enabled:
        return _set_hook(tag, False, path, kind, callback)
    if not _function_exists(path):
        return False
    return _set_hook(tag, True, path, kind, callback)


def _remove_all_hooks():
    for tag, (path, kind, ident) in tuple(_hooks.items()):
        _set_hook(tag, False, path, kind, None)


def _basic_context(pc, pi=None, pawn=None, *, fire_pose_only=False):
    if pc is None:
        return False
    pi = pc.PlayerInput if pi is None else pi
    pawn = pc.Pawn if pawn is None else pawn
    if pi is None or pawn is None:
        return False
    # Presentation-only callers (sprint-fire pose and sprint crosshair) may use
    # stock character skill contexts. Movement, sprint-gate, and recovery
    # predicates retain the narrow on_foot() requirement.
    if not (firing_pose_context(pc, pawn, pi) if fire_pose_only else on_foot(pawn, pi)):
        return False
    return not (
        pawn.bIsInjured
        or pawn.bIsDead
        or pc.bCinematicMode
        or pc.bIgnoreMoveInput
    )


def _coyote_enabled():
    return _active and _cfg_coyote and _cfg_coyote_window > 0.0


def _coyote_owner(pc):
    return (pc._get_address(), pc.PlayerInput._get_address(),
            pc.Pawn._get_address(), pc.WorldInfo._get_address())


def _coyote_context(pc):
    """Fail closed outside the game's ordinary local on-foot jump path."""
    if not (_coyote_enabled() and _coyote_ready and pc is not None):
        return False
    try:
        pawn, pi = pc.Pawn, pc.PlayerInput
        return bool(
            pawn is not None and pi is not None
            and _same(pawn.Controller, pc) and pc.IsLocalPlayerController()
            and int(pawn.Role) == 3  # Local authority; do not predict an unapproved client jump.
            # BL2's live local controller can report no UnrealScript state
            # while using normal native on-foot movement. Named special states
            # remain excluded; input context/physics and native vetoes still apply.
            and str(pc.GetStateName()) in ('None', 'PlayerWalking') and not pc.bUpdating
            and _basic_context(pc, pi=pi, pawn=pawn)
            and str(pi.CurrentInputContextName) == 'DefaultOnFoot'
            and not _travel_pending and not _coyote_traveling and not _crosshair_modal_open
            and not _deathtrap_skill_active
            and not pawn.bDeleteMe and not pawn.bIsLeaping and pawn.DrivenVehicle is None
            # SDK returns (bool, RemainingTime) for the optional out parameter.
            and not pc.IsInTransitionalVehicleState()[0]
            and not pc.bStopgapBlockForDeferredMovies
            and pc.PlayerReplicationInfo is not None
            and not pc.PlayerReplicationInfo.bGFxMenuOpen
            and pc.WorldInfo.Pauser is None
            and not pc.WorldInfo.IsPreparingMapChange()
            and not pc.WorldInfo.Game.bLevelChange
            and int(pawn.Physics) in (1, 2)
            and pawn.bJumpCapable and pawn.bCanJump
            and not pawn.bIsCrouched and not pawn.bWantsToCrouch
        )
    except Exception:
        return False


def _coyote_seed(pc):
    _coyote.invalidate()
    if _coyote_context(pc) and int(pc.Pawn.Physics) == 1:
        _coyote.ground(_coyote_owner(pc))


def _coyote_local_pawn(obj):
    if not _is_local_pawn(obj):
        return None
    pc = _pc()
    return pc if pc is not None and _same(obj, pc.Pawn) else None


def _coyote_falling(obj, args, ret, func):
    pc = _coyote_local_pawn(obj)
    if pc is None:
        return
    if not _coyote_context(pc):
        _coyote.invalidate()
        return
    # Rising velocity is an intentional jump or launch, never a ledge grace.
    # AddVelocity/scripted SetPhysics also invalidate before their native body.
    vz = float(obj.Velocity.Z)
    if not math.isfinite(vz) or vz > 0.0:
        _coyote.invalidate()
        return
    _coyote.leave(_coyote_owner(pc), time.monotonic())


def _coyote_landed(obj, args, ret, func):
    pc = _coyote_local_pawn(obj)
    if pc is None:
        return
    _coyote.invalidate()
    if (_coyote_context(pc) and args.FloorActor is not None
            and float(args.HitNormal.Z) >= float(obj.WalkableFloorZ)):
        # The native collision's Landed event is ground evidence even when the
        # physics integrator changes PHYS_Falling to PHYS_Walking afterward.
        _coyote.ground(_coyote_owner(pc))


def _coyote_do_jump_pre(obj, args, ret, func):
    pc = _coyote_local_pawn(obj)
    if pc is not None:
        _coyote.begin_jump(_coyote_owner(pc))


def _coyote_do_jump_post(obj, args, ret, func):
    pc = _coyote_local_pawn(obj)
    if pc is not None:
        _coyote.end_jump(_coyote_owner(pc), bool(ret),
                         _coyote_context(pc) and int(obj.Physics) == 1)


def _coyote_can_jump(obj, args, ret, func):
    if _coyote_attempt is None:
        return
    pc = _coyote_local_pawn(obj)
    if not _coyote_context(pc):
        return
    owner, deadline = _coyote_attempt
    # Respect a previous hook's explicit veto. Only the Engine base physics
    # test is relaxed; WillowPlayerPawn.CanJump still runs its native body.
    if (ret is not Unset and not isinstance(ret, Unset)):
        return
    if (_coyote_owner(pc) == owner and time.monotonic() < deadline
            and int(obj.Physics) == 2 and pc.bPressedJump):
        return Block, True


def _coyote_jump_input(obj, args, ret, func):
    """Execute at the existing Jump action, never save an input for landing."""
    global _coyote_attempt, _coyote_failure_logged
    if not (_coyote_enabled() and _coyote_ready and _local_input(obj) is not None):
        return
    pc = _pc()
    if not _coyote_context(pc):
        _coyote.invalidate()
        return
    pawn = pc.Pawn
    if int(pawn.Physics) != 2 or not pc.bPressedJump or _coyote_attempt is not None:
        return
    owner = _coyote_owner(pc)
    now = time.monotonic()
    if not _coyote.eligible(owner, now, _cfg_coyote_window):
        return
    try:
        # The regular PlayerMove path consults CannotJumpNow before DoJump.
        # Do not bypass custom game/mod restrictions on this immediate attempt.
        if pawn.CannotJumpNow():
            _coyote.invalidate()
            return
        deadline = _coyote.take(owner, time.monotonic(), _cfg_coyote_window)
        if deadline is None:
            return
        if not _set_hook('coyote-can-jump', True, BASE_CAN_JUMP, Type.PRE, _coyote_can_jump):
            return
        _coyote_attempt = (owner, deadline)
        try:
            succeeded = bool(pawn.DoJump(bool(pc.bUpdating)))
            # A return value is required; merely invoking DoJump is not success.
            if succeeded and int(pawn.Physics) == 2:
                pass
        finally:
            _coyote_attempt = None
            _set_hook('coyote-can-jump', False, BASE_CAN_JUMP, Type.PRE, _coyote_can_jump)
            _coyote.invalidate()
    except Exception:
        _coyote.invalidate()
        if not _coyote_failure_logged:
            logging.warning('omnimovement: coyote attempt failed; vanilla jumping retained')
            _coyote_failure_logged = True


def _coyote_forced_physics(obj, args, ret, func):
    if _coyote_local_pawn(obj) is not None and int(args.newPhysics) != 1:
        _coyote.invalidate()


def _coyote_impulse(obj, args, ret, func):
    if _coyote_local_pawn(obj) is not None:
        velocity = args.NewVelocity
        if not obj.bIgnoreForces and any(float(v) != 0.0 for v in (velocity.X, velocity.Y, velocity.Z)):
            _coyote.invalidate()


def _coyote_invalid_pawn(obj, args, ret, func):
    if _coyote_local_pawn(obj) is not None:
        _coyote.invalidate()


def _coyote_invalid_controller(obj, args, ret, func):
    if _local_pc(obj) is not None:
        _coyote.invalidate()


def _coyote_possessed(obj, args, ret, func):
    pc = _local_pc(obj)
    if pc is not None:
        _cache_local(pc)
        _coyote_seed(pc)


def _coyote_skill_ending(obj, args, ret, func):
    # OnActionSkillEnded clears MyController in its body; use the real PRE
    # owner, rather than guessing an argument which this function does not have.
    pc = _local_pc(obj.MyController)
    if pc is not None:
        _coyote_seed(pc)


def _sync_coyote_hooks():
    global _coyote_ready, _coyote_failure_logged
    enabled = _coyote_enabled()
    specs = (
        ('coyote-input', JUMP_INPUT, Type.POST, _coyote_jump_input),
        ('coyote-jump-pre', DO_JUMP, Type.PRE, _coyote_do_jump_pre),
        ('coyote-jump-post', DO_JUMP, Type.POST_UNCONDITIONAL, _coyote_do_jump_post),
        ('coyote-falling', CROSSHAIR_FALLING, Type.PRE, _coyote_falling),
        ('coyote-landed', CROSSHAIR_LANDED, Type.POST, _coyote_landed),
        ('coyote-physics', SET_PHYSICS, Type.PRE, _coyote_forced_physics),
        ('coyote-impulse', ADD_VELOCITY, Type.PRE, _coyote_impulse),
        ('coyote-died', 'WillowGame.WillowPlayerPawn:Died', Type.PRE, _coyote_invalid_pawn),
        ('coyote-injured', 'WillowGame.WillowPlayerPawn:injured.BeginState', Type.PRE, _coyote_invalid_pawn),
        ('coyote-unpossess', 'WillowGame.WillowPlayerController:UnPossess', Type.PRE, _coyote_invalid_controller),
        ('coyote-possess', 'WillowGame.WillowPlayerController:Possess', Type.POST, _coyote_possessed),
        ('coyote-modal', CROSSHAIR_MODAL_OPENING, Type.PRE, _coyote_invalid_controller),
        ('coyote-pause', CROSSHAIR_PAUSE_STATE, Type.PRE, _coyote_invalid_controller),
        ('coyote-skill-ended', ACTION_SKILL_ENDED, Type.PRE, _coyote_skill_ending),
    )
    _coyote_ready = enabled and all(_function_exists(p) for p in (
        BASE_CAN_JUMP, SET_INPUT_CONTEXT, PRE_CLIENT_TRAVEL, NOTIFY_LOADED_WORLD,
        FAST_TRAVEL_REQUEST, FAST_TRAVEL_LOADED, *(row[1] for row in specs)))
    for tag, path, kind, callback in specs:
        installed = _set_hook(tag, _coyote_ready, path, kind, callback)
        if _coyote_ready and not installed:
            _coyote_ready = False
    if not _coyote_ready:
        _coyote.invalidate()
        for tag, path, kind, callback in specs:
            _set_hook(tag, False, path, kind, callback)
        if enabled and not _coyote_failure_logged:
            logging.warning('omnimovement: required coyote hooks unavailable; vanilla jumping retained')
            _coyote_failure_logged = True


def _fast_zoomed(pc, weapon, *, pending_zoom=None, weapon_zoomed=None):
    # A valid native snapshot has already reflected bPendingZoom.
    if (pc.bPendingZoom if pending_zoom is None else pending_zoom):
        return True
    if weapon is not None:
        if weapon_zoomed is not None:
            return bool(weapon_zoomed)
        try:
            return int(weapon.ZoomState) != 0
        except Exception:
            pass
    try:
        return bool(pc.IsZoomed())
    except Exception:
        return False


def _interrupted(pc):
    pi = None if pc is None else pc.PlayerInput
    observation = _precision.combat_observe(
        pc, pi, prefer_native=_cfg_native_precision)
    if observation is not None:
        snapshot = observation[1]
        interrupted = bool(snapshot[1] or snapshot[4] or _has_weapon_transition(pc))
        if interrupted or _weapon_state_events_complete:
            return interrupted
        weapon = pc.Pawn.Weapon
        if weapon is not None:
            try:
                return bool(weapon.IsEquipping())
            except Exception:
                pass
        return interrupted
    return action_interrupted(
        pc,
        weapon_transitioning=_has_weapon_transition(pc),
        check_equipping_fallback=not _weapon_state_events_complete,
    )


def _reset_reversal_state():
    """Reset both reversal observers on direction, travel, and input changes."""
    global _native_reversal_was_active
    _reversal.reset()
    _native_reversal_was_active = False
    _precision.reset_reversal()


def _clear_direction():
    global _reversal_last_at
    _reset_reversal_state()
    _reversal_last_at = None


def _crouch_sprint_paused():
    deadline = _crouch_sprint_pause_until
    return deadline > 0.0 and deadline > time.perf_counter()


def _zero_execute_waiting():
    return (_zero_execute_waiting_cleanup is not None
            and not _zero_execute_early_ready
            and time.perf_counter() - _zero_execute_waiting_cleanup[3] < 3.0)


def _assist_crouching(pc, pawn=None):
    """Requested or actual crouch owns movement until the pawn is standing."""
    if pc is None:
        return True
    pawn = pc.Pawn if pawn is None else pawn
    if pawn is None:
        return True
    try:
        return bool(pc.bDuck or pawn.bWantsToCrouch or pawn.bIsCrouched)
    except Exception:
        return True


def _set_try_sprint(pi, value, *, allow_deathtrap=False):
    global _try_sprint_written
    # While the Digi-Jack cast is interrupting sprint, ordinary event-driven
    # retries stay suppressed. A tiny temporary recovery watcher is the only
    # path allowed to re-arm Sprint Assist during DeathTrap, and only after
    # BL2's own CanContinueSprinting has remained valid.
    value = bool(value)
    if value and _deathtrap_skill_active and not allow_deathtrap:
        value = False
    if value and _cfg_assist:
        # PlayerInput consumes this request by clearing bDuck/bHoldDuck BEFORE
        # CanSprint runs. Permission checks alone cannot protect crouch.
        pc = _pc()
        if pc is None or not _same(pi, pc.PlayerInput):
            return
        if (_assist_crouching(pc) or _crouch_sprint_paused()
                or _zero_execute_waiting()):
            value = False
    try:
        if bool(pi.bTryToSprint) != value:
            pi.bTryToSprint = value
    finally:
        _try_sprint_written = value


def _stop_assist_recovery():
    global _assist_recovery_ready_frames
    _assist_recovery_ready_frames = 0
    _precision.reset_assist('retry')
    _set_hook(
        "assist-recovery",
        False,
        PLAYER_TICK,
        Type.POST_UNCONDITIONAL,
        _assist_recovery_tick,
    )


def _clear_assist():
    global _try_sprint_written, _assist_airborne_hold, _assist_press_held
    _assist_airborne_hold = False
    _assist_press_held = False
    _precision.intent_reset(_armed)
    _set_hook(
        "assist-airborne-gate",
        False,
        INPUT_ALLOWS_SPRINT,
        Type.PRE,
        _assist_airborne_gate,
    )
    _stop_assist_recovery()
    pi = None if _pi_ref is None else _pi_ref()
    if pi is not None and _try_sprint_written:
        try:
            if bool(pi.bTryToSprint):
                pi.bTryToSprint = False
        except Exception:
            pass
    _try_sprint_written = None


def _hold_assist_through_airborne(pc):
    """Keep Sprint Assist intent owned by Omni until the pawn is grounded.

    BL2's bTryToSprint is a one-shot. If it is consumed while airborne, DoSprint
    can move that intent into bWantsToSprint, and PlayerWalking.ProcessMove can
    then discard bWantsToSprint again when movement crosses zero before landing.
    Keep bTryToSprint pending instead; the existing InputAllowsSprint callback
    releases this hold as soon as Physics returns to walking.
    """
    global _assist_airborne_hold
    if not (
        _active
        and _cfg_assist
        and _armed
        and not _deathtrap_skill_active
        and pc is not None
        and pc.PlayerInput is not None
        and not pc.bInSprintState
        and pc.Pawn is not None
        and _assist_context(pc)
        and not _assist_crouching(pc)
        and not _crouch_sprint_paused()
        and not _zero_execute_waiting()
    ):
        return False
    try:
        if int(pc.Pawn.Physics) == 1:
            return False
    except Exception:
        return False

    decision = _precision.intent_observe(
        pc, pc.PlayerInput, _travel_epoch, _armed,
        context_ready=True, sprinting=False, moving=_assist_raw_moving(pc),
        grounded=False, permitted=False, prefer_native=_cfg_native_precision)
    if decision is not None and not decision[4]:
        return False
    _assist_airborne_hold = True
    # Omnidirectional Sprint already owns InputAllowsSprint on the production
    # Python-safe path. If Sprint Assist is used by itself, install the same
    # airborne hold as a temporary hook and remove it on landing/reset.
    _set_hook(
        "assist-airborne-gate",
        not _cfg_omni,
        INPUT_ALLOWS_SPRINT,
        Type.PRE,
        _assist_airborne_gate,
    )
    _stop_assist_recovery()
    # Sprint Assist owns the remembered request. Do not leave BL2's separate
    # airborne bWantsToSprint one-shot armed at the same time.
    try:
        pc.bWantsToSprint = False
    except Exception:
        pass
    _set_try_sprint(pc.PlayerInput, True)
    return True


def _assist_airborne_gate(obj, args, ret, func):
    """Temporary vanilla-gate companion when Sprint Assist is used alone."""
    global _assist_airborne_hold
    if not (_active and _cfg_assist and _armed and _assist_airborne_hold):
        _assist_airborne_hold = False
        _set_hook(
            "assist-airborne-gate",
            False,
            INPUT_ALLOWS_SPRINT,
            Type.PRE,
            _assist_airborne_gate,
        )
        return

    pc = _pc()
    grounded = _assist_grounded(pc)
    if grounded:
        _assist_airborne_hold = False
        _set_hook(
            "assist-airborne-gate",
            False,
            INPUT_ALLOWS_SPRINT,
            Type.PRE,
            _assist_airborne_gate,
        )
        return
    return Block, False


def _assist_grounded(pc):
    """Observe landing only at the existing temporary input-gate boundary."""
    pawn = None if pc is None else pc.Pawn
    try:
        grounded = pawn is not None and int(pawn.Physics) == 1
    except Exception:
        return False
    # Python mode never consumes an intent observation here. Avoid reflecting
    # context/axes just to call an adapter which immediately returns None.
    if not _cfg_native_precision:
        return grounded
    pi = None if pc is None else pc.PlayerInput
    if (pi is not None and _assist_context(pc, pi=pi, pawn=pawn)
            and not _deathtrap_skill_active):
        decision = _precision.intent_observe(
            pc, pi, _travel_epoch, _armed,
            context_ready=True, sprinting=bool(pc.bInSprintState),
            # Falling intent selects AirbornePending (or actual Sprinting)
            # before consulting axes. Keep observing the state/generation, but
            # do not reflect axes which cannot affect that decision.
            moving=grounded and _assist_raw_moving(pc, pi=pi), grounded=grounded,
            permitted=False, prefer_native=True,
            owner_input=pi, pawn=pawn)
        if decision is not None:
            # Actual sprint also releases the hold; pending flags never do.
            return decision[0] == 2 or not decision[4]
    return grounded


def _assist_raw_moving(pc, *, pi=None):
    if pi is None:
        pi = None if pc is None else pc.PlayerInput
    if pi is None:
        return False
    try:
        fwd = float(pi.aBaseY)
        side = float(pi.aStrafe)
    except Exception:
        return False
    return fwd * fwd + side * side > 0.0001


def _assist_can_retry(pc, *, native_snapshot=None, pi=None, pawn=None,
                      context_validated=False, allow_weapon_swap=False):
    """Authoritative CanSprint check with optional native rejection snapshot.

    The native snapshot NEVER permits sprint or replaces context/weapon checks.
    """
    if not (
        _active
        and _cfg_assist
        and _armed
        and pc is not None
        and not (native_snapshot[0] if native_snapshot is not None
                 else pc.bInSprintState)
        and not _deathtrap_skill_active
    ):
        return False
    pawn = pc.Pawn if pawn is None else pawn
    pi = pc.PlayerInput if pi is None else pi
    if pawn is None or pi is None:
        return False
    # This hint is callback-local, supplied only before any engine function.
    # The permission call below can dispatch hooks, so a successful result is
    # revalidated at that boundary before it can produce a pending request.
    if not context_validated and (
            not _assist_context(pc, pi=pi, pawn=pawn)
             or _assist_crouching(pc, pawn)
             or _crouch_sprint_paused() or _zero_execute_waiting()):
        return False

    # The engine owns character, firing (including off-hand) and animation
    # restrictions. Native combat observations never substitute for CanSprint.
    if native_snapshot is not None:
        if native_snapshot[1] or native_snapshot[2]:
            return False
        weapon = pawn.Weapon
        if not allow_weapon_swap:
            put_down = (native_snapshot[4] if len(native_snapshot) >= 6
                        else weapon is not None and weapon.bWeaponPutDown)
            if _has_weapon_transition(pc) or put_down:
                return False
            if not _weapon_state_events_complete and weapon is not None:
                try:
                    if bool(weapon.IsEquipping()):
                        return False
                except Exception:
                    pass
        weapon_zoomed = native_snapshot[5] if len(native_snapshot) >= 6 else None
        if _fast_zoomed(pc, weapon, pending_zoom=False, weapon_zoomed=weapon_zoomed):
            return False
    else:
        interrupted = (action_interrupted(pc, check_weapon_transition=False)
                       if allow_weapon_swap else _interrupted(pc))
        if interrupted or _fast_zoomed(pc, pawn.Weapon):
            return False
    try:
        allowed = bool(pawn.CanSprint())
        if allowed and context_validated:
            return bool(
                _same(pi, pc.PlayerInput) and _same(pawn, pc.Pawn)
                and not _deathtrap_skill_active
                and _assist_context(pc, pi=pi, pawn=pawn)
                and not _assist_crouching(pc, pawn))
        return allowed
    except Exception:
        return False


def _schedule_assist_recovery(pc=None):
    """Temporarily preserve remembered Sprint through any native blocker.

    BL2 consumes bTryToSprint before DoSprint checks CanSprint(). If CanSprint
    later rejects ADS, melee, firing or another action, the one-shot retry is
    otherwise lost. This watcher exists only while such a restriction is active.
    """
    global _assist_recovery_ready_frames
    if pc is None:
        pc = _pc()
    if not (
        _active
        and _cfg_assist
        and _armed
        and pc is not None
        and pc.PlayerInput is not None
        and not pc.bInSprintState
    ):
        _stop_assist_recovery()
        return
    if _assist_airborne_hold:
        _stop_assist_recovery()
        _set_try_sprint(pc.PlayerInput, True)
        return
    if _deathtrap_skill_active:
        _stop_assist_recovery()
        _set_try_sprint(pc.PlayerInput, False)
        _schedule_deathtrap_recovery(pc)
        return
    if "assist-recovery" in _hooks:
        # Already watching this interruption; do not restart its retry window.
        return
    if _set_hook(
        "assist-recovery",
        True,
        PLAYER_TICK,
        Type.POST_UNCONDITIONAL,
        _assist_recovery_tick,
    ):
        # An unowned/rejected registration cannot service a retry window.
        # Do not repeatedly discard recovery state or clear sprint intent.
        _assist_recovery_ready_frames = 0
        _precision.reset_assist('retry')
        _set_try_sprint(pc.PlayerInput, False)


def _assist_recover_or_schedule(pc):
    """Re-arm immediately when safe; otherwise wait for the blocker to clear."""
    if not (
        _active
        and _cfg_assist
        and _armed
        and pc is not None
        and pc.PlayerInput is not None
        and not pc.bInSprintState
    ):
        _stop_assist_recovery()
        return False
    if (_assist_crouching(pc) or _crouch_sprint_paused()
            or _zero_execute_waiting()):
        _set_try_sprint(pc.PlayerInput, False)
        _schedule_assist_recovery(pc)
        return False
    if _assist_airborne_hold:
        _stop_assist_recovery()
        _set_try_sprint(pc.PlayerInput, True)
        return True
    if _deathtrap_skill_active:
        _schedule_assist_recovery(pc)
        return False
    if not _assist_raw_moving(pc):
        # Stationary Sprint mode is just a pending intent. Keep bTryToSprint set
        # and let InputAllowsSprint consume it only when real movement begins.
        _stop_assist_recovery()
        _set_try_sprint(pc.PlayerInput, True)
        return True
    # Even if native CanSprint is currently true, the EndSprint/animation
    # boundary may still consume a one-shot request before BeginSprint runs.
    # Let the existing two-frame recovery watcher rearm it and remain alive
    # until native sprint has been confirmed.
    _schedule_assist_recovery(pc)
    return False


def _zero_execute_try_early_handoff(pc, pi, pawn):
    """Release Execute's *mod-owned* sprint hold at native eligibility.

    RestorePlayerMovementPostExecute normally precedes Execute cleanup, but
    the old guard withheld Sprint Assist until cleanup even if the native
    controller, weapon and pawn allowed sprint earlier. The actual game
    permissions, not a new animation timer, decide the transition. Cleanup's
    PRE/POST velocity preservation still owns the later native zero-reset.
    Called only by the already temporary assist-recovery tick.
    """
    global _zero_execute_early_ready
    stamp = _zero_execute_waiting_cleanup
    if stamp is None or _zero_execute_early_ready:
        return False
    try:
        if (time.perf_counter() - stamp[3] > 3.0
                or pawn is None or pi is None or pc is None
                or pc._get_address() != stamp[1]
                or pawn._get_address() != stamp[2]
                or not _same(pc.Pawn, pawn)
                or not _same(pc.PlayerInput, pi)
                or int(pawn.Physics) != 1
                or pc.bIgnoreMoveInput or pc.bCinematicMode
                or pc.bPerformingMeleeAttack
                or pc.bPerformingSharedWeaponAction
                or _assist_crouching(pc, pawn)
                or _crouch_sprint_paused()
                or not _assist_raw_moving(pc, pi=pi)
                or not _assist_context(pc, pi=pi, pawn=pawn)):
            return False
        # This skips only the mod's pending Execute *wait flag*, not the
        # game's weapon/action checks or CanSprint. Never force DoSprint.
        if not _assist_can_retry(pc, pi=pi, pawn=pawn,
                                 context_validated=True):
            return False
        _zero_execute_early_ready = True
        # Native Execute zeroes the dash at its end. If movement is still
        # nearly stationary at this now-authorized boundary, let the same
        # conservative, bounded walking reseed prevent a second dead stop.
        if _zero_execute_restore_momentum(pc, pi, pawn):
            pass
        return True
    except Exception:
        return False


def _assist_recovery_tick(obj, args, ret, func):
    global _assist_recovery_ready_frames
    pc = _local_pc(obj)
    if not (
        pc is not None
        and _active
        and _cfg_assist
        and _armed
    ):
        _stop_assist_recovery()
        return
    pi = pc.PlayerInput
    if pi is None:
        _stop_assist_recovery()
        return
    pawn = pc.Pawn
    global _zero_execute_seed_pending
    pending = _zero_execute_seed_pending
    if pending is not None:
        try:
            now = time.perf_counter()
            if (now <= pending[2] and pawn is not None
                    and pc._get_address() == pending[0]
                    and pawn._get_address() == pending[1]):
                if _zero_execute_restore_momentum(pc, pi, pawn):
                    _zero_execute_seed_pending = None
            else:
                _zero_execute_seed_pending = None
        except Exception:
            _zero_execute_seed_pending = None
    if _zero_execute_waiting_cleanup is not None and not _zero_execute_early_ready:
        _zero_execute_try_early_handoff(pc, pi, pawn)
        pi = pc.PlayerInput
        if pi is None:
            _stop_assist_recovery()
            return
        pawn = pc.Pawn
    if _zero_execute_waiting() or _crouch_sprint_paused():
        _assist_recovery_ready_frames = 0
        _set_try_sprint(pi, False)
        return
    # A remembered mode survives menus/vehicles/death, but its pending request
    # cannot. Apply this guard before stationary/native observations as well as
    # the moving retry predicate; both backends then resume from two ready frames.
    if (pawn is None or not _assist_context(pc, pi=pi, pawn=pawn)
            or _assist_crouching(pc, pawn)):
        _assist_recovery_ready_frames = 0
        _precision.reset_assist('retry')
        _precision.intent_observe(
            pc, pi, _travel_epoch, _armed, context_ready=False,
            sprinting=False, moving=False, grounded=False, permitted=False,
            prefer_native=_cfg_native_precision)
        _set_try_sprint(pi, False)
        return
    if _deathtrap_skill_active:
        _stop_assist_recovery()
        _set_try_sprint(pi, False)
        _schedule_deathtrap_recovery(pc)
        return
    if _native_assist_recovery(pc, pi, pawn=pawn, context_validated=True):
        return
    # The older native retry combines put-down with real action blockers.
    # Read its raw snapshot in Omni mode so the retry can distinguish them.
    allow_weapon_swap = _cfg_omni
    combat = _precision.combat_observe(
        pc, pi, retry=not allow_weapon_swap, recovery_only=allow_weapon_swap,
        weapon_transitioning=not allow_weapon_swap and _has_weapon_transition(pc),
        prefer_native=_cfg_native_precision)
    native_observation = None if combat is None else combat[0]
    if native_observation is not None:
        if native_observation == 0:  # confirmed native sprint
            _stop_assist_recovery()
            return
        if native_observation == 1:  # stationary remembered-sprint intent
            _stop_assist_recovery()
            _set_try_sprint(pi, True)
            return
        if native_observation in (2, 3):  # known blocker / debounce
            _set_try_sprint(pi, False)
            return
        # Status 4 is only a CANDIDATE. Python still owns full eligibility.
        # Re-check the skill before writes, since status may change within
        # a callback and native never grants sprint by itself.
        if _deathtrap_skill_active:
            _stop_assist_recovery()
            _set_try_sprint(pi, False)
            _schedule_deathtrap_recovery(pc)
            return
        if not _assist_can_retry(pc, native_snapshot=combat[1]):
            _precision.reset_assist('retry')
            _set_try_sprint(pi, False)
            return
        _set_try_sprint(pi, True)
        return
    # Entire legacy path stays available when Python is selected or
    # if native reflection/ABI is unavailable.
    if pc.bInSprintState:
        _stop_assist_recovery()
        return
    if _deathtrap_skill_active:
        _stop_assist_recovery()
        _set_try_sprint(pi, False)
        _schedule_deathtrap_recovery(pc)
        return
    if not _assist_raw_moving(pc, pi=pi):
        _stop_assist_recovery()
        _set_try_sprint(pi, True)
        return
    # Only definite blockers can short circuit here: action flags and zoom.
    # The unblocked result NEVER grants sprint. All other engine, weapon and
    # character-specific checks run through the existing Python predicate.
    snapshot = None if combat is None else combat[1]
    blocked = (
        _precision.retry_preflight_blocked(pc, prefer_native=_cfg_native_precision)
        if snapshot is None and _assist_recovery_ready_frames == 0 else None
    )
    if blocked is True or not _assist_can_retry(
            pc, native_snapshot=snapshot, allow_weapon_swap=allow_weapon_swap):
        _assist_recovery_ready_frames = 0
        _precision.reset_assist('retry')
        _set_try_sprint(pc.PlayerInput, False)
        return

    # Keep the original two-frame Python fallback aligned with native state.
    # Python has already checked the action, weapon, movement and local pawn.
    native_ready = _precision.assist_ready(
        'retry', True, prefer_native=_cfg_native_precision)
    _assist_recovery_ready_frames += 1
    if not (native_ready if native_ready is not None
            else _assist_recovery_ready_frames >= 2):
        return
    _set_try_sprint(pc.PlayerInput, True)
    # Do not stop here: a consumed bTryToSprint does NOT prove BeginSprint
    # succeeded, especially at the end of melee/action-skill animations.
    # The watcher exits only after native sprint is observed or explicitly
    # cancelled. If the one-shot was consumed too soon, we retry next frame.


def _assist_crouch_changed(pc, pi):
    """Let native crouch win without changing remembered Sprint mode."""
    global _assist_airborne_hold, _crouch_sprint_pause_until
    if not (_active and _cfg_assist and _armed and pc is not None
            and pi is not None and _same(pi, pc.PlayerInput)):
        return
    # Explicit duck input must exit active sprint, not merely veto the
    # next pending sprint. Hold off re-entry while duck events are rapid.
    _crouch_sprint_pause_until = time.perf_counter() + 0.22
    # A duck event suspends any earlier airborne request. Keep recovery watching
    # until the same crouch pause that blocks the request has elapsed.
    _assist_airborne_hold = False
    _set_hook("assist-airborne-gate", False, INPUT_ALLOWS_SPRINT,
              Type.PRE, _assist_airborne_gate)
    crouching = _assist_crouching(pc)
    if pc.bInSprintState:
        # EndSprint creates a native skid camera lurch. On a local crouch
        # transition it can look like a movement stop, especially sideways or
        # backward. Capture eligibility before EndSprint clears sprint state;
        # never alter velocity, crouch permission, or an airborne/root-motion edge.
        cancel_crouch_skid = bool(crouching and _cfg_omni
                                  and _moving_nonforward_on_foot(pc))
        _force_end_sprint(pc)
        if cancel_crouch_skid and not pc.bInSprintState:
            try:
                _action_skid.cancel(pc)
                _schedule_action_skid_tick()
            except Exception:
                pass  # Crouch and sprint intent must still be processed.
    if crouching:
        # Native input can otherwise accept an old queued Sprint request and
        # clear bDuck during rapid crouch toggles. Discard the old movement
        # gate and reversal state at the actual crouch input boundary.
        _reset_gate_grace()
        _clear_direction()
        _clear_center_transition()
        _set_try_sprint(pi, False)
        # A queued airborne or already-processed request can bypass the next
        # bTryToSprint check. The user's explicit crouch cancels those intents.
        pc.bWantsToSprint = False
        pc.bPressedSprint = False
        _schedule_assist_recovery(pc)
    elif not _assist_context(pc, pi=pi):
        _set_try_sprint(pi, False)
        _schedule_assist_recovery(pc)
    else:
        _assist_recover_or_schedule(pc)


def _assist_duck_input(obj, args, ret, func):
    if not (_active and _cfg_assist and _armed):
        return
    pi = _local_input(obj)
    if pi is not None:
        _assist_crouch_changed(_pc(), pi)


def _assist_uncrouched(obj, args, ret, func):
    if not (_active and _cfg_assist and _armed):
        return
    pc = _local_pc(obj)
    if pc is not None:
        _assist_crouch_changed(pc, pc.PlayerInput)


def _native_assist_recovery(pc, pi, *, pawn=None, context_validated=False):
    """Native intent decisions with current engine permission and local writes.

    False selects the complete independent Python recovery path. No routine
    DoSprint call is introduced; a pending request never proves BeginSprint.
    """
    if not _cfg_native_precision:
        return False
    pawn = pc.Pawn if pawn is None else pawn
    if pawn is None:
        _precision.intent_reset(_armed)
        return False
    # A failed/unavailable intent backend selects Python recovery immediately.
    # Do not read combat and run CanSprint only to discover that same failure
    # again at the final native intent call on every recovery frame.
    if not _precision.intent_available(prefer_native=True):
        return False
    combat = _precision.combat_observe(pc, pi, prefer_native=True, pawn=pawn,
                                       recovery_only=True)
    snapshot = None if combat is None else combat[1]
    sprinting = bool(pc.bInSprintState) if snapshot is None else snapshot[0]
    moving = _assist_raw_moving(pc, pi=pi) if snapshot is None else snapshot[3]
    context_ready = bool(
        (context_validated or _assist_context(pc, pi=pi, pawn=pawn))
        and not _deathtrap_skill_active)
    try:
        grounded = int(pawn.Physics) == 1
    except Exception:
        context_ready, grounded = False, False
    # A definite blocker still needs the intent observation to reset debounce.
    # Its permission result is already known from the validated snapshot.
    check_permission = (context_ready and grounded and moving and not sprinting
                        and not (snapshot is not None and (snapshot[1] or snapshot[2])))
    permitted = check_permission and _assist_can_retry(
        pc, native_snapshot=snapshot, pi=pi, pawn=pawn,
        context_validated=context_validated, allow_weapon_swap=_cfg_omni)
    decision = _precision.intent_observe(
        pc, pi, _travel_epoch, _armed, context_ready=context_ready,
        sprinting=sprinting, moving=moving, grounded=grounded,
        permitted=permitted, prefer_native=True)
    if decision is None:
        return False
    phase, remembered, request, write_required, airborne = decision
    if _deathtrap_skill_active:
        _stop_assist_recovery()
        _set_try_sprint(pi, False)
        _schedule_deathtrap_recovery(pc)
    elif airborne:
        _hold_assist_through_airborne(pc)
    elif phase in (0, 2, 3):
        _stop_assist_recovery()
        _set_try_sprint(pi, request)
    elif write_required:
        # The native state machine suppresses duplicate property requests.
        # Engine CanSprint was checked on THIS frame before RetryPending.
        _set_try_sprint(pi, request)
    return True


def _do_sprint_attempt(obj, args, ret, func):
    """Observe actual sprint after native DoSprint consumes its one-shot."""
    if not (_active and _cfg_assist and _armed and _is_local_pawn(obj)):
        return
    pc = _pc()
    if pc is None or pc.bInSprintState:
        return
    # CanSprint and DoSprint's return value can both be true while falling:
    # DoSprint only sets bWantsToSprint in that case. ProcessMove discards that
    # queue when movement reaches zero. Observe AFTER the engine call and move
    # the remembered request back into our airborne hold before that can happen.
    if not _hold_assist_through_airborne(pc):
        _schedule_assist_recovery(pc)


def _assist_landed(obj, args, ret, func):
    """Re-arm remembered Sprint after native landing/state cleanup."""
    if not (_active and _cfg_assist and _armed and _is_local_pawn(obj)):
        return
    pc = _pc()
    if (pc is None or pc.PlayerInput is None or pc.bInSprintState
            or not _assist_context(pc)):
        return
    # This rare event also covers a landing that clears the native pending
    # flags without another EndSprint/DoSprint call. A zero input remains a
    # pending request; normal movement and CanSprint still own actual sprint.
    if not _hold_assist_through_airborne(pc):
        _assist_recover_or_schedule(pc)


def _reset_gate_grace():
    global _gate_last_moving_at
    _gate_last_moving_at = None
    _precision.reset_gate()


def _clear_center_transition(*, remove_hook=True):
    global _center_origin_velocity
    global _center_input_previous, _center_input_started
    _center_input_previous = None
    _center_input_started = None
    global _center_transition_origin, _center_transition_target
    global _center_transition_speed, _center_transition_started
    _center_origin_velocity = None
    _center_transition_origin = None
    _center_transition_target = None
    _center_transition_speed = 0.0
    _center_transition_started = 0.0
    if remove_hook:
        _set_hook(
            "center-transition",
            False,
            PLAYER_TICK,
            Type.POST_UNCONDITIONAL,
            _center_transition_tick,
        )




def _set_native_hipfire(enabled):
    """Patch sprint continuation and optionally validated sprint initiation.

    Sprint-entry selection is captured when the mod is enabled. Changing that
    setting requires disabling and re-enabling OmniMovement. Any patch mode
    transition restores the owned patch first; a failed restore prevents it.
    """
    global _native_hipfire_active, _active_fire_entry
    enabled = bool(enabled and _active and _cfg_hip_fire)
    if not enabled:
        _stop_sprint_fire_entry_probe()
        if not _native_hipfire.restore():
            logging.warning(
                "omnimovement: native hip-fire restore FAILED, restart BL2; "
                + str(_native_hipfire.last_error)
            )
        _native_hipfire_active = False
        _active_fire_entry = False
        return False

    entry_requested = bool(_requested_fire_entry)
    if _native_hipfire_active and _native_hipfire.patched:
        if _native_hipfire.start_while_firing == entry_requested:
            return True
        if not _native_hipfire.restore():
            logging.warning("omnimovement: unsafe sprint-entry mode switch refused: "
                            + str(_native_hipfire.last_error))
            _native_hipfire_active = False
            _active_fire_entry = False
            return False

    _native_hipfire_active = bool(
        _native_hipfire.apply(start_while_firing=entry_requested)
    )
    _active_fire_entry = bool(
        _native_hipfire_active and _native_hipfire.start_while_firing
    )
    if not _native_hipfire_active:
        logging.warning(
            "omnimovement: validated native sprint-fire unavailable; "
            "using Python continuation fallback; experimental sprint entry DISABLED: "
            + str(_native_hipfire.last_error)
        )
    return _native_hipfire_active


def _is_doppelganger_pc(pc):
    """Event-only exact check mirrored from the supplied Doppelganger mod."""
    if pc is None:
        return False
    try:
        pri = pc.PlayerReplicationInfo
        name_def = None if pri is None else pri.CharacterNameIdDef
        return name_def is not None and str(name_def.CharacterName) in {"Jack", "Jack30"}
    except Exception:
        return False


def _stop_deathtrap_recovery():
    global _deathtrap_recovery_ready_at
    _deathtrap_recovery_ready_at = 0.0
    _precision.reset_assist('summon')
    _set_hook(
        "deathtrap-recovery",
        False,
        PLAYER_TICK,
        Type.POST_UNCONDITIONAL,
        _deathtrap_recovery_tick,
    )


def _schedule_deathtrap_recovery(pc=None):
    """Wake only around a Digi-Jack sprint interruption, never for steady movement."""
    global _deathtrap_recovery_ready_at
    _deathtrap_recovery_ready_at = 0.0
    _precision.reset_assist('summon')
    if pc is None:
        pc = _pc()
    if not (
        _active
        and _deathtrap_skill_active
        and _cfg_assist
        and _armed
        and pc is not None
        and pc.PlayerInput is not None
    ):
        _stop_deathtrap_recovery()
        return
    _set_hook(
        "deathtrap-recovery",
        True,
        PLAYER_TICK,
        Type.POST_UNCONDITIONAL,
        _deathtrap_recovery_tick,
    )


def _deathtrap_recovery_tick(obj, args, ret, func):
    """Re-arm remembered Sprint once the summon interruption is genuinely over."""
    global _deathtrap_recovery_ready_at
    pc = _local_pc(obj)
    if not (
        pc is not None
        and _active
        and _deathtrap_skill_active
        and _cfg_assist
        and _armed
    ):
        _stop_deathtrap_recovery()
        return

    # If native sprint survived/re-entered already, there is nothing to recover.
    if pc.bInSprintState:
        _stop_deathtrap_recovery()
        return

    pawn = pc.Pawn
    pi = pc.PlayerInput
    if pawn is None or pi is None or not _basic_context(pc, pi=pi, pawn=pawn):
        _deathtrap_recovery_ready_at = 0.0
        _precision.reset_assist('summon')
        return

    # Mirror the old, verified Sprint Assist rule, but only during this short
    # recovery window. Never fight crouch, firing, weapon actions, or another
    # native sprint restriction.
    if (
        int(pc.bDuck)
        or pawn.bWantsToCrouch
        or pawn.bIsCrouched
        or _fire_requested
        or _interrupted(pc)
    ):
        _deathtrap_recovery_ready_at = 0.0
        _precision.reset_assist('summon')
        return
    try:
        can_continue = bool(pawn.CanContinueSprinting())
    except Exception:
        can_continue = False
    if not can_continue:
        _deathtrap_recovery_ready_at = 0.0
        _precision.reset_assist('summon')
        return

    # Stage18: merge summon controller blockers and the 50ms readiness timer
    # in one native call, but only AFTER Python validates the context,
    # animation/weapon state and pawn.CanContinueSprinting().
    now = time.perf_counter()
    native_observation = _precision.assist_summon_observe(
        pc, now, prefer_native=_cfg_native_precision)
    if native_observation is not None:
        if native_observation == 0:
            _stop_deathtrap_recovery()
            return
        if native_observation == 2:
            _deathtrap_recovery_ready_at = 0.0
            _precision.reset_assist('summon')
            return
        if native_observation == 3:
            return
        _set_try_sprint(pi, True, allow_deathtrap=True)
        _stop_deathtrap_recovery()
        return
    # Original Python debounce and fallback are unchanged.
    if _deathtrap_recovery_ready_at <= 0.0:
        _deathtrap_recovery_ready_at = now + 0.050
        return
    if now < _deathtrap_recovery_ready_at:
        return

    _set_try_sprint(pi, True, allow_deathtrap=True)
    _stop_deathtrap_recovery()


def _finish_deathtrap_guard(pc=None):
    """Clear the temporary Digi-Jack retry guard and restore remembered intent."""
    global _deathtrap_skill_active, _deathtrap_skill_ref, _deathtrap_context_seen
    _stop_deathtrap_recovery()
    _deathtrap_skill_active = False
    _deathtrap_skill_ref = None
    _deathtrap_context_seen = False
    _clear_center_transition()
    if pc is None:
        pc = _pc()
    if _coyote_enabled():
        _coyote_seed(pc)
    if (
        _active
        and _cfg_assist
        and _armed
        and pc is not None
        and pc.PlayerInput is not None
        and not pc.bInSprintState
    ):
        _set_try_sprint(pc.PlayerInput, True)


def _action_skill_started(obj, args, ret, func):
    """Track Zer0's native cast transition; keep Doppelganger policy unchanged."""
    global _deathtrap_skill_active, _deathtrap_skill_ref, _deathtrap_context_seen
    global _zero_deception_cast_edge
    if _active and _cfg_crosshair:
        try:
            if str(obj.Class.Name) == "DualWieldActionSkill":
                _schedule_offhand_crosshair(_local_pc(args.TheController), pending_draw=True)
        except Exception:
            pass
    if _active and _cfg_omni:
        try:
            if str(obj.Class.Name) == "ExecuteActionSkill":
                pc = _local_pc(args.TheController)
                if (pc is not None and pc.Pawn is not None
                        and _same(obj.MyWillowPawn, pc.Pawn)):
                    _zero_deception_cast_edge = (
                        pc._get_address(), time.perf_counter() + 0.75)
        except Exception:
            pass
    if _coyote_enabled():
        try:
            if _local_pc(args.TheController) is not None:
                _coyote.invalidate()
        except AttributeError:
            pass
    if not (_active and str(obj.Class.Name) == "DeathtrapActionSkill"):
        return
    try:
        pc = _local_pc(args.TheController)
    except Exception:
        pc = None
    if pc is None or not _is_doppelganger_pc(pc):
        return

    _deathtrap_skill_active = True
    _deathtrap_skill_ref = unrealsdk.unreal.WeakPointer(obj)
    try:
        _deathtrap_context_seen = str(pc.PlayerInput.CurrentInputContextName) == "DeathTrap"
    except Exception:
        _deathtrap_context_seen = False
    _clear_center_transition()
    _clear_direction()
    if pc.PlayerInput is not None:
        _set_try_sprint(pc.PlayerInput, False)
    _schedule_deathtrap_recovery(pc)

    # Do not force EndSprint and do not block BeginSprint. BL2 remains
    # authoritative: if the cast cannot continue sprint, native ProcessMove
    # will end it once. The EndSprint hook cancels only that resulting skid.


def _action_skill_ended(obj, args, ret, func):
    if not _deathtrap_skill_active:
        return
    owned = None if _deathtrap_skill_ref is None else _deathtrap_skill_ref()
    if owned is not None and not _same(owned, obj):
        return

    pc = _pc()
    # The input context is a more reliable lifetime boundary than the action
    # skill UObject itself. If DeathTrap is still active here, wait for
    # SetInputContext to leave it; otherwise clear immediately.
    try:
        context = str(pc.PlayerInput.CurrentInputContextName) if pc is not None and pc.PlayerInput is not None else ""
    except Exception:
        context = ""
    if context != "DeathTrap":
        _finish_deathtrap_guard(pc)


def _input_context_changed(obj, args, ret, func):
    """Refresh presentation after skill-context changes and guard Digi-Jack."""
    global _deathtrap_context_seen
    if not _active:
        return
    pi = _local_input(obj)
    if pi is None:
        return
    pc = _pc()
    if _equip_sprint_handoffs:
        _equip_sprint_context_post(pc, pi)
    if _coyote_enabled():
        _coyote_seed(pc)
    # Skill activation can hide/rebuild the native GFx reticle without ending
    # sprint. Reacquire only on this context boundary, with a short bounded
    # follow-up to survive native HUD updates later in the transition.
    # Never change sprint eligibility or weapon/action-skill input contexts.
    if _cfg_crosshair and pc is not None and pc.bInSprintState:
        if _crosshair_eligible(pc):
            _schedule_crosshair_resync(
                pc, ticks=3, force_tick=True, max_attempts=60,
            )
    try:
        if str(pi.CurrentInputContextName) == "Gunzerking":
            _schedule_offhand_crosshair(pc, pending_draw=True)
        elif _offhand_crosshair is not None or _offhand_crosshair_retry is not None:
            _release_offhand_crosshair()
            _stop_offhand_crosshair_retry()
    except Exception:
        pass
    if not _deathtrap_skill_active:
        return
    try:
        context = str(pi.CurrentInputContextName)
    except Exception:
        return
    if context == "DeathTrap":
        _deathtrap_context_seen = True
        return
    if _deathtrap_context_seen:
        _finish_deathtrap_guard(pc)

def _reset_runtime_motion():
    _stop_activation_input_frame()
    _clear_equip_sprint_handoffs()
    _stop_offhand_crosshair_retry()
    _release_offhand_crosshair(refresh_native=False)
    _coyote.invalidate()
    global _zero_final_physics_snapshot, _zero_deception_cast_edge
    global _zero_execute_waiting_cleanup, _zero_execute_seed_pending
    global _zero_execute_early_ready, _pause_sprint_grace_until, _ads_sprint_edge
    _ads_sprint_edge = None
    global _combat_action_active, _combat_fire_mask
    _combat_action_active = False
    _combat_fire_mask = 0
    _stop_combat_fire_tail()
    _pose_reload_addresses.clear()
    _stop_pose_reload_retry()
    _sync_combat_sprint_hooks()
    _pause_sprint_grace_until = 0.0
    _zero_final_physics_snapshot = None
    _zero_deception_cast_edge = None
    _zero_execute_waiting_cleanup = None
    _zero_execute_seed_pending = None
    _zero_execute_early_ready = False
    global _melee_nonforward_event_at, _melee_nonforward_controller
    _melee_nonforward_event_at = 0.0
    _melee_nonforward_controller = 0
    global _weapon_address, _fire_requested, _pose_offhand_requested, _fire_until, _pose_release_at
    global _hip_hooks_until, _knockback_until, _continuation_depth, _gate_sprinting
    _action_skid.restore()
    _precision.reset()
    _stop_deathtrap_recovery()
    _stop_fire_state_recovery()
    _stop_assist_recovery()
    _clear_direction()
    _clear_center_transition()
    _reset_gate_grace()
    _weapon_address = 0
    _fire_requested = False
    _pose_offhand_requested = False
    _fire_until = 0.0
    _pose_release_at = 0.0
    _hip_hooks_until = 0.0
    _knockback_until = 0.0
    _continuation_depth = 0
    _gate_sprinting = False
    _weapon_transition_addresses.clear()
    _weapon_transition_refs.clear()
    _sync_fire_hooks(False)


def _has_weapon_transition(pc):
    """Discard only proven orphaned entries; unknown state remains a blocker."""
    if not _weapon_transition_addresses or pc is None:
        return bool(_weapon_transition_addresses)
    pawn = pc.Pawn
    if pawn is None:
        return True
    for address in tuple(_weapon_transition_addresses):
        reference = _weapon_transition_refs.get(address)
        if reference is None:
            continue
        try:
            weapon = reference()
            orphaned = weapon is None
            if weapon is not None:
                orphaned = bool(weapon.bDeleteMe)
                if not orphaned:
                    held = _same(weapon, pawn.Weapon) or _same(weapon, pawn.OffHandWeapon)
                    orphaned = not held and not _same(weapon.Instigator, pawn)
                if not orphaned and not weapon.bWeaponPutDown and not weapon.IsEquipping():
                    # A missing EndState is recoverable when both the native
                    # flags and a known resting/firing state confirm completion.
                    state = str(weapon.GetStateName())
                    orphaned = state in ('Active', 'Inactive', 'WeaponFiring', 'WeaponReloading')
        except Exception:
            continue  # Unavailable reflection must not grant sprint permission.
        if orphaned:
            _weapon_transition_addresses.discard(address)
            _weapon_transition_refs.pop(address, None)
    return bool(_weapon_transition_addresses)


def _weapon_belongs_to_local(obj):
    if obj is None:
        return False
    try:
        if _weapon_address and obj._get_address() == _weapon_address:
            return True
    except Exception:
        return False
    try:
        instigator = obj.Instigator
        return (
            instigator is not None
            and _local_pawn_address
            and instigator._get_address() == _local_pawn_address
        )
    except Exception:
        return False


def _moving_nonforward_on_foot(pc):
    """Only a local lateral or backward input edge; never infer from camera angle.

    The engine owns melee knockback, lunges and movement root-motion. We only
    expire Omni's own action-exit lurch during sideways or backward movement.
    """
    if pc is None or pc.Pawn is None or pc.PlayerInput is None:
        return False
    # A real recent impulse/knockback takes priority over cosmetic lurch
    # cleanup even if the player presses melee during the knockback window.
    if _knockback_until > time.perf_counter():
        return False
    try:
        return (int(pc.Pawn.Physics) == 1
                # Melee skill input contexts differ by character. Unlike the
                # sprint gate, this cosmetic-only rule needs no context allowlist.
                and not pc.Pawn.bIsInjured and not pc.Pawn.bIsDead
                and not pc.bCinematicMode and not pc.bIgnoreMoveInput
                and (float(pc.PlayerInput.aBaseY) < -0.08
                     or (float(pc.PlayerInput.aBaseY) <= 0.08
                         and abs(float(pc.PlayerInput.aStrafe)) > 0.08))
                and not pc.Pawn.bForceRMVelocity
                and not pc.Pawn.bForceRegularVelocity
                and not pc.Pawn.bRMForceStop)
    except Exception:
        return False


def _melee_dispatch_exit(obj, args, ret, func):
    """Expire a short local melee-exit lurch without touching native melee.

    Shared across ordinary melee and class overrides via Behavior_Melee.
    No velocity/animation/cooldown or character-specific skill is altered.
    """
    pc = _local_pc(obj)
    if not (_active and _cfg_omni and _moving_nonforward_on_foot(pc)):
        return
    try:
        now = time.perf_counter()
        if (_melee_nonforward_controller == pc._get_address()
                and now - _melee_nonforward_event_at <= 0.35):
            _action_skid.cancel(pc)
            _schedule_action_skid_tick()
    except Exception:
        return


def _melee_dispatch_enter(obj, args, ret, func):
    """Yield owned sprint/fire posture before any normal or overridden melee.

    Behavior_Melee routes ordinary melee, tracked override skills and running
    action-skill interception. A weapon-action completion hook does not cover
    every branch. This event changes presentation only: no skill is blocked,
    no retry is forced mid-attack and no permanent tick is installed.
    """
    global _melee_nonforward_event_at, _melee_nonforward_controller
    pc = _local_pc(obj)
    if _active and _cfg_hip_fire and pc is not None:
        _fire_pose.restore()
    # Capture the incoming sprint/sideways or backward transition BEFORE the native melee
    # route can clear bInSprintState or its action flags. This is a short-lived
    # event marker, never a new movement ticker or forced sprint permission.
    if (_active and _cfg_omni and pc is not None
            and (pc.bInSprintState or _gate_sprinting)
            and _moving_nonforward_on_foot(pc)):
        _melee_nonforward_controller = pc._get_address()
        _melee_nonforward_event_at = time.perf_counter()
    else:
        _melee_nonforward_controller = 0
        _melee_nonforward_event_at = 0.0


def _main_reload_allows_offhand_sprint(pc, pawn, *, release=False):
    """Recognize a main-hand reload while the live offhand still owns fire."""
    try:
        main, offhand = pawn.Weapon, pawn.OffHandWeapon
        if main is None or offhand is None or str(pc.PlayerInput.CurrentInputContextName) != "Gunzerking":
            return False
        offhand_bit = 1 if pc.bGunzerkingAutoSwitch else 2
        if not (_combat_fire_mask & offhand_bit or (release and _combat_tail_matches(pc, pawn))):
            return False
        def reloading(weapon):
            return (weapon._get_address() in _pose_reload_addresses
                    or str(weapon.GetStateName()).lower() == "weaponreloading")
        return reloading(main) and not reloading(offhand)
    except Exception:
        return False


def _opposite_hand_fire_during_reload(pc, pawn):
    """Allow the firing stance only when one known reload leaves a firing hand."""
    try:
        main, offhand = pawn.Weapon, pawn.OffHandWeapon
        if main is None or offhand is None or str(pc.PlayerInput.CurrentInputContextName) != "Gunzerking":
            return False
        if not _combat_fire_mask:
            return False
        def reloading(weapon):
            return (weapon._get_address() in _pose_reload_addresses
                    or str(weapon.GetStateName()).lower() == "weaponreloading")
        main_reloading, offhand_reloading = reloading(main), reloading(offhand)
        if main_reloading == offhand_reloading:
            return False
        offhand_bit = 1 if pc.bGunzerkingAutoSwitch else 2
        return bool(_combat_fire_mask & (offhand_bit if main_reloading else 3 ^ offhand_bit))
    except Exception:
        return False


def _combat_sprint_safe(pc, pawn, *, allow_offhand_reload=False, release=False,
                        other_weapon_reloading=False, airborne_offhand=False):
    """Fail closed when native locomotion or another action owns movement."""
    try:
        return bool(pc is not None and pawn is not None and _same(pc.Pawn, pawn)
            and _basic_context(pc, pawn=pawn, fire_pose_only=True)
            and (int(pawn.Physics) == 1 or airborne_offhand) and not pawn.bDeleteMe
            and not pc.bDuck and (not pc.bPressedJump or airborne_offhand)
            and not pawn.bIsCrouched and not pawn.bWantsToCrouch
            and (not pawn.bReloading or other_weapon_reloading or (allow_offhand_reload
                 and _main_reload_allows_offhand_sprint(pc, pawn, release=release)))
            and not pawn.bForceRMVelocity
            and not pawn.bForceRegularVelocity and not pawn.bRMForceStop
            and not _fast_zoomed(pc, pawn.Weapon))
    except Exception:
        return False


def _combat_release_safe(pc, pawn):
    """Only continue an already held, ordinary sprint through fire release."""
    pri = pc.PlayerReplicationInfo
    return bool(
        not _crosshair_modal_open and not _travel_modal_open
        and pc.WorldInfo.Pauser is None and pri is not None and not pri.bGFxMenuOpen
        and _assist_raw_moving(pc) and time.perf_counter() >= _knockback_until
        and not _crouch_sprint_paused() and not _zero_execute_waiting()
        and not _interrupted(pc) and (not _pose_reload_active(pawn)
            or _main_reload_allows_offhand_sprint(pc, pawn, release=True)))


def _airborne_offhand_continuation(pc, pawn):
    try:
        return bool(
            _cfg_hip_fire and int(pawn.Physics) == 2 and pawn.OffHandWeapon is not None
            and str(pc.PlayerInput.CurrentInputContextName) == "Gunzerking"
            and not pc.bPerformingSharedWeaponAction and not pc.bPerformingMeleeAttack
            and not pc.bThrowingGrenade
            and (_combat_fire_mask & (1 if pc.bGunzerkingAutoSwitch else 2)
                 or _combat_tail_matches(pc, pawn)))
    except Exception:
        return False


def _combat_continue_override(obj, args, ret, func):
    """Replace native continuation only for guarded local combat cases.

    A successful PRE override skips the full native eligibility result, not
    just its shared-action or offhand-firing condition.
    """
    if not (_active and _cfg_omni and _is_local_pawn(obj)):
        return
    pc = _pc()
    if pc is None or not pc.bInSprintState or not _combat_sprint_safe(
            pc, obj, allow_offhand_reload=_cfg_hip_fire, release=True,
            airborne_offhand=_airborne_offhand_continuation(pc, obj)):
        return
    try:
        action = bool(_combat_action_active and _cfg_assist and _armed
                      and pc.bPerformingSharedWeaponAction)
        offhand = False
        if _cfg_hip_fire and obj.OffHandWeapon is not None:
            offhand = bool(_combat_fire_mask)
            if not offhand and _combat_tail_matches(pc, obj):
                offhand = (not (_cfg_assist and _armed)
                           or time.perf_counter() < _combat_fire_tail[3]
                           or _combat_release_safe(pc, obj))
        if not (action or offhand) or (pc.bPerformingSharedWeaponAction and not action):
            return
        weapon = obj.Weapon
        if weapon is not None and weapon.IsFiring() and not _cfg_hip_fire:
            return
        if obj.OffHandWeapon is not None and obj.OffHandWeapon.IsFiring() and not offhand:
            return
        return Block, True
    except Exception:
        return


def _combat_entry_override(obj, args, ret, func):
    """Override native sprint entry only for guarded local offhand firing.

    A successful PRE override replaces the full native CanSprint result.
    """
    if not (_active and _cfg_omni and _cfg_hip_fire and _active_fire_entry
            and (_combat_fire_mask or _combat_fire_tail) and _is_local_pawn(obj)):
        return
    pc = _pc()
    if pc is None or pc.bInSprintState or not _combat_sprint_safe(pc, obj, allow_offhand_reload=True):
        return
    try:
        if (obj.OffHandWeapon is None or pc.PlayerClass is None
                or pc.PlayerClass.SprintSettings is None
                or pc.bPerformingMeleeAttack or pc.bPerformingSharedWeaponAction):
            return
        if not _combat_fire_mask and not (
                _combat_tail_matches(pc, obj) and obj.OffHandWeapon.IsFiring()):
            return
        return Block, True
    except Exception:
        return


def _sync_combat_sprint_hooks():
    firing = bool(_combat_fire_mask or _combat_fire_tail)
    enabled = bool(_active and _cfg_omni and (_combat_action_active or firing))
    _set_hook("combat-continue", enabled, CONTINUE, Type.PRE, _combat_continue_override)
    _set_hook("combat-entry", bool(enabled and firing and _active_fire_entry),
              CAN_SPRINT, Type.PRE, _combat_entry_override)


def _combat_tail_matches(pc, pawn):
    """A released trigger may leave its exact offhand weapon firing briefly."""
    try:
        tail = _combat_fire_tail
        return bool(tail and pc is not None and pawn is not None
                    and pawn.OffHandWeapon is not None
                    and (pc._get_address(), pawn._get_address(),
                         pawn.OffHandWeapon._get_address()) == tail[:3]
                    and time.perf_counter() < tail[4])
    except Exception:
        return False


def _stop_combat_fire_tail():
    global _combat_fire_tail
    _combat_fire_tail = None
    _set_hook("combat-fire-tail", False, PLAYER_TICK, Type.POST_UNCONDITIONAL,
              _combat_fire_tail_tick)


def _combat_fire_tail_tick(obj, args, ret, func):
    """Retire the release override once native sprint continuation accepts."""
    pc = _local_pc(obj)
    if pc is None:
        return  # A foreign controller tick cannot retire the local release.
    pawn = pc.Pawn
    try:
        pending = bool(_active and _cfg_omni and _cfg_hip_fire
                       and _combat_tail_matches(pc, pawn))
        if pending and time.perf_counter() >= _combat_fire_tail[3]:
            if pc.bInSprintState and _cfg_assist and _armed:
                pending = (_combat_sprint_safe(pc, pawn, allow_offhand_reload=True, release=True,
                            airborne_offhand=_airborne_offhand_continuation(pc, pawn))
                           and _combat_release_safe(pc, pawn))
                if pending:
                    # Reflected IsFiring can report false before the native
                    # gate clears. Probe the gate without our override.
                    with unrealsdk.hooks.prevent_hooking_direct_calls():
                        pending = not pawn.CanContinueSprinting()
            else:
                pending = bool(pawn.OffHandWeapon.IsFiring())
    except Exception:
        pending = False
    if not pending:
        _stop_combat_fire_tail()
        _sync_combat_sprint_hooks()


def _retain_combat_fire_tail(pc):
    global _combat_fire_tail
    try:
        pawn = pc.Pawn
        offhand = None if pawn is None else pawn.OffHandWeapon
        if not (_active and _cfg_omni and _cfg_hip_fire and offhand is not None
                and (pc.bInSprintState or offhand.IsFiring())):
            return
        # Live dual wield can reject the first continuation check after
        # StopFire even when the exposed IsFiring() already reports false.
        now = time.perf_counter()
        _combat_fire_tail = (pc._get_address(), pawn._get_address(),
                             offhand._get_address(), now + 0.10, now + 5.0)
        if not _set_hook("combat-fire-tail", True, PLAYER_TICK,
                         Type.POST_UNCONDITIONAL, _combat_fire_tail_tick):
            _combat_fire_tail = None
    except Exception:
        _combat_fire_tail = None


def _combat_action_begin(obj, args, ret, func):
    global _combat_action_active
    pc = _local_pc(obj)
    if _active and _cfg_omni and _cfg_assist and _armed and pc is not None and pc.bInSprintState:
        _combat_action_active = True
        _sync_combat_sprint_hooks()


def _combat_fire_edge(pc, mode, pressed):
    global _combat_fire_mask
    if mode not in (0, 1):
        return
    bit = 1 << mode
    if pressed:
        if not (_active and _cfg_omni and _cfg_hip_fire and pc.Pawn is not None
                and pc.Pawn.OffHandWeapon is not None):
            return
        _stop_combat_fire_tail()
        _combat_fire_mask |= bit
    else:
        was_held = bool(_combat_fire_mask & bit)
        _combat_fire_mask &= ~bit
        if was_held and not _combat_fire_mask:
            _retain_combat_fire_tail(pc)
    _sync_combat_sprint_hooks()
    if pressed and _active_fire_entry and _cfg_assist and _armed and not pc.bInSprintState:
        _assist_recover_or_schedule(pc)


def _weapon_action_completed(obj, args, ret, func):
    """Recover remembered Sprint after any completed shared weapon action.

    Native MeleeAttackComplete and GrenadeThrowComplete both call this event
    after clearing their respective blockers. Using the shared boundary also
    supports non-character-specific actions without another per-frame hook.
    """
    global _combat_action_active
    pc = _local_pc(obj)
    if pc is None:
        return
    _combat_action_active = False
    _sync_combat_sprint_hooks()
    if _active and _cfg_assist and _armed and not pc.bInSprintState:
        _assist_recover_or_schedule(pc)


def _weapon_action_pose_completed(obj, args, ret, func):
    """Reacquire the firing pose after native melee/shared action cleanup."""
    pc = _local_pc(obj)
    if _active and _cfg_hip_fire and pc is not None and pc.bInSprintState:
        _sprint_fire_entry(pc, after_equip=True)


def _zero_execute_restore_momentum(pc, pi, pawn):
    """Conservatively reseed local, grounded movement after Execute zeroes it.

    Source evidence: ExecuteActionSkill.RestorePhysicsPostExecute resets every
    component of Velocity to zero on dash end AND during CleanUpExecute.
    The player's movement is already unlocked by the game's own method. After
    native melee/shared-action locks clear, replace only a *near-zero* horizontal
    velocity with a small input-aligned walking velocity. Native movement then
    owns acceleration and collision from the next physics frame onward.
    No dash physics, falling velocity, sprint permission or input flags change.
    """
    try:
        if (int(pawn.Role) != 3  # local authority only; no client prediction writes
                or int(pawn.Physics) != 1
                or pawn.DrivenVehicle is not None
                or pc.bIgnoreMoveInput or pc.bCinematicMode
                or pc.bPerformingMeleeAttack or pc.bPerformingSharedWeaponAction
                or pawn.bForceRMVelocity or pawn.bForceRegularVelocity
                or pawn.bRMForceStop or pawn.bIsInjured or pawn.bIsDead
                or _assist_crouching(pc, pawn)
                or not _basic_context(pc, pi=pi, pawn=pawn, fire_pose_only=True)):
            return False
        forward, side = float(pi.aBaseY), float(pi.aStrafe)
        magnitude = math.hypot(forward, side)
        if not (math.isfinite(magnitude) and magnitude >= 0.25):
            return False
        velocity = pawn.Velocity
        vx, vy = float(velocity.X), float(velocity.Y)
        ground = float(pawn.GroundSpeed)
        desired = float(pawn.MaxDesiredSpeed)
        speed_cap = ground * min(1.0, max(0.0, desired))
        if not (math.isfinite(speed_cap) and speed_cap > 0.0
                and math.isfinite(vx) and math.isfinite(vy)
                and math.hypot(vx, vy) < speed_cap * 0.06):
            return False
        direction = _movement_world_direction(pc, forward, side, magnitude)
        if direction is None:
            return False
        # A modest 30% of vanilla walking speed. Replaces an engine-imposed
        # stop, never carries forward Execute's dash speed or camera direction.
        target = speed_cap * 0.30 * min(1.0, magnitude)
        velocity.X = direction[0] * target
        velocity.Y = direction[1] * target
        return True
    except Exception:
        return False


def _zero_final_restore_pre(obj, args, ret, func):
    """Remember *walking* momentum just before Execute cleanup's last reset.

    In the exported ExecuteActionSkill.CleanUpExecute, WeaponActionComplete
    runs BEFORE RestorePhysicsPostExecute sets X/Y to zero. The first dash-end
    reset must stay native. Capture only the cleanup-time, local, grounded
    movement which the player actually regained after the dash.
    """
    global _zero_final_physics_snapshot
    _zero_final_physics_snapshot = None
    if not (_active and _cfg_assist and _armed):
        return
    try:
        if not obj.bCleaningUpExecute or not obj.bMovementHasBeenRestored:
            return
        pc = _pc()
        if (pc is None or pc.Pawn is None or pc.PlayerInput is None
                or not _same(obj.MyWillowPC, pc)
                or not _same(obj.MyWillowPawn, pc.Pawn)
                or pc.bIgnoreMoveInput or pc.bCinematicMode):
            return
        pawn, pi = pc.Pawn, pc.PlayerInput
        if (int(pawn.Role) != 3 or int(pawn.Physics) != 1
                or pawn.DrivenVehicle is not None
                or _assist_crouching(pc, pawn)
                or not _assist_raw_moving(pc, pi=pi)
                or not _basic_context(pc, pi=pi, pawn=pawn, fire_pose_only=True)):
            return
        velocity = pawn.Velocity
        vx, vy = float(velocity.X), float(velocity.Y)
        speed = math.hypot(vx, vy)
        ground = float(pawn.GroundSpeed)
        if (not math.isfinite(speed) or not math.isfinite(ground)
                or ground <= 0.0 or not 0.02 * ground <= speed <= 1.5 * ground):
            return
        _zero_final_physics_snapshot = (
            obj._get_address(), pc._get_address(), pawn._get_address(),
            vx, vy, time.perf_counter())
    except Exception:
        _zero_final_physics_snapshot = None


def _zero_final_restore_post(obj, args, ret, func):
    """Retain pre-existing on-foot movement after native cleanup zeroed X/Y."""
    global _zero_final_physics_snapshot
    saved = _zero_final_physics_snapshot
    _zero_final_physics_snapshot = None
    if saved is None:
        return
    try:
        pc = _pc()
        if (pc is None or pc.Pawn is None or pc.PlayerInput is None
                or obj._get_address() != saved[0]
                or pc._get_address() != saved[1]
                or pc.Pawn._get_address() != saved[2]
                or time.perf_counter() - saved[5] > 0.15
                or not obj.bCleaningUpExecute
                or pc.bIgnoreMoveInput or pc.bCinematicMode):
            return
        pawn, pi = pc.Pawn, pc.PlayerInput
        if (int(pawn.Role) != 3 or int(pawn.Physics) != 1
                or pawn.DrivenVehicle is not None
                or _assist_crouching(pc, pawn)
                or not _assist_raw_moving(pc, pi=pi)):
            return
        velocity = pawn.Velocity
        vx, vy = float(velocity.X), float(velocity.Y)
        if math.isfinite(vx) and math.isfinite(vy):
            # A nonzero native result must always win. The source-proven
            # cleanup reset writes X/Y=0, but physics is engine-owned.
            if math.hypot(vx, vy) < float(pawn.GroundSpeed) * 0.02:
                pawn.Velocity.X = saved[3]
                pawn.Velocity.Y = saved[4]
    except Exception as exc:
        pass


def _zero_execute_controls_restored(obj, args, ret, func):
    """Mark Execute's initial control return, NOT its last velocity reset."""
    global _zero_execute_waiting_cleanup, _zero_execute_early_ready
    if not (_active and _cfg_assist and _armed):
        return
    try:
        pc = _pc()
        if (pc is None or pc.Pawn is None or pc.PlayerInput is None
                or not _same(obj.MyWillowPC, pc)
                or not _same(obj.MyWillowPawn, pc.Pawn)
                or not obj.bMovementHasBeenRestored
                or not obj.bAttemptedExecute):
            return
        # Native CleanUpExecute calls this function once more, but the
        # game immediately returns because controls were already restored.
        # Keep the original marker until the final Cleanup POST callback.
        if (_zero_execute_waiting_cleanup is None
                or time.perf_counter() - _zero_execute_waiting_cleanup[3] > 3.0):
            _zero_execute_early_ready = False
            _zero_execute_waiting_cleanup = (
                obj._get_address(), pc._get_address(),
                pc.Pawn._get_address(), time.perf_counter())
        _set_try_sprint(pc.PlayerInput, False)
        _schedule_assist_recovery(pc)
    except Exception as exc:
        pass


def _zero_execute_cleanup_completed(obj, args, ret, func):
    """Restore movement only after final native RestorePhysicsPostExecute."""
    global _zero_execute_waiting_cleanup, _zero_execute_seed_pending
    global _zero_execute_early_ready
    stamp = _zero_execute_waiting_cleanup
    _zero_execute_waiting_cleanup = None
    _zero_execute_early_ready = False
    if not (_active and _cfg_assist and _armed and stamp is not None):
        return
    try:
        pc = _pc()
        if (pc is None or pc.Pawn is None or pc.PlayerInput is None
                or obj._get_address() != stamp[0]
                or pc._get_address() != stamp[1]
                or pc.Pawn._get_address() != stamp[2]
                or not _same(obj.MyWillowPC, pc)
                or not _same(obj.MyWillowPawn, pc.Pawn)
                or time.perf_counter() - stamp[3] > 3.0):
            return
        pawn, pi = pc.Pawn, pc.PlayerInput
        if _zero_execute_restore_momentum(pc, pi, pawn):
            pass
        else:
            # Reuse the existing temporary recovery hook if native melee
            # guards clear on the subsequent game frame.
            _zero_execute_seed_pending = (
                pc._get_address(), pawn._get_address(),
                time.perf_counter() + 0.24)
        _assist_recover_or_schedule(pc)
    except Exception as exc:
        pass


def _equip_sprint_owner(pc, weapon):
    """Match the incoming weapon to the current local pawn and exact hand."""
    if pc is None or weapon is None:
        return None
    pawn, pi = pc.Pawn, pc.PlayerInput
    if pawn is None or pi is None or not _same(pawn.Controller, pc):
        return None
    if not _same(weapon.Instigator, pawn):
        return None
    if _same(weapon, pawn.Weapon):
        hand = 0
    elif _same(weapon, pawn.OffHandWeapon):
        hand = 1
    else:
        return None
    return (hand, pc._get_address(), pawn._get_address(), pi._get_address(), weapon._get_address())


def _drop_sprint_pre(obj, args, ret, func):
    global _drop_sprint_scope
    if not (_active and _cfg_omni and _cfg_assist and _cfg_baseline and _armed):
        return
    try:
        pc = _pc()
        owner = _equip_sprint_owner(pc, obj)
        if owner is not None and pc.bInSprintState:
            now = time.perf_counter()
            if _drop_sprint_scope is None or now >= _drop_sprint_scope[1]:
                _drop_sprint_scope = (owner, now + 0.25)
    except Exception:
        pass


def _drop_sprint_post(obj, args, ret, func):
    global _drop_sprint_scope
    if _drop_sprint_scope is not None and obj._get_address() == _drop_sprint_scope[0][4]:
        _drop_sprint_scope = None


def _sync_drop_sprint_hooks(enabled):
    global _drop_sprint_scope
    if enabled and _set_optional_hook("drop-sprint-post", True, WEAPON_DROP,
                                     Type.POST_UNCONDITIONAL, _drop_sprint_post):
        if _set_optional_hook("drop-sprint-pre", True, WEAPON_DROP, Type.PRE, _drop_sprint_pre):
            return
    _drop_sprint_scope = None
    _set_hook("drop-sprint-pre", False, WEAPON_DROP, Type.PRE, _drop_sprint_pre)
    _set_hook("drop-sprint-post", False, WEAPON_DROP, Type.POST_UNCONDITIONAL, _drop_sprint_post)


def _equip_sprint_skid(pc):
    try:
        lurch = pc.CurrentLurch
        return (float(lurch.StartTime), float(lurch.Duration),
                float(lurch.LocMagnitude), float(lurch.RotMagnitude))
    except Exception:
        return None


def _expire_equip_sprint_skid(pc, pending):
    """Expire only a new, still-owned camera skid created by this equip."""
    skid = pending.get("equip_skid")
    if skid is None or skid != _equip_sprint_skid(pc):
        return
    try:
        now = float(pc.WorldInfo.TimeSeconds)
        if skid[1] > 0.0 and 0.0 <= now - skid[0] <= 0.10:
            _action_skid.cancel(pc)
            _schedule_action_skid_tick()
    except Exception:
        pass


def _equip_sprint_bringup_pre(obj, args, ret, func):
    if not (_active and _cfg_omni and _cfg_assist and "equip-sprint-post" in _hooks):
        return
    try:
        pc = _pc()
        owner = _equip_sprint_owner(pc, obj)
        if owner is None:
            return
        hand = owner[0]
        _equip_sprint_handoffs.pop(hand, None)
        if _armed and pc.bInSprintState:
            activation = (hand == 1
                          and str(pc.PlayerInput.CurrentInputContextName) != "Gunzerking")
            started = time.perf_counter()
            _equip_sprint_handoffs[hand] = dict(
                owner=owner, started=started, deadline=started + 0.25,
                phase="bringup", attempts=0, activation=activation, context_before=None,
                last_input=None, context_zero_until=0.0,
                travel_owner=_equip_activation_travel_owner(pc, owner) if activation else None,
                old_skid=_equip_sprint_skid(pc) if pc.Pawn.OffHandWeapon is not None else None)
    except Exception:
        return


def _equip_sprint_bringup_post(obj, args, ret, func):
    if not _equip_sprint_handoffs:
        return
    try:
        pc = _pc()
        owner = _equip_sprint_owner(pc, obj)
        if owner is None:
            return
        pending = _equip_sprint_handoffs.get(owner[0])
        if pending is None or pending["owner"] != owner or pending["phase"] != "bringup":
            return
        if pc.bInSprintState or time.perf_counter() >= pending["deadline"]:
            _equip_sprint_handoffs.pop(owner[0], None)
        else:
            pending["phase"] = "ready"
            old_skid = pending["old_skid"]
            if old_skid is not None:
                skid = _equip_sprint_skid(pc)
                if skid is not None and skid != old_skid:
                    pending["equip_skid"] = skid
    except Exception:
        return


def _equip_activation_travel_owner(pc, owner):
    """Treat already-sprinting, verified spawn settling separately from travel."""
    if not _travel_pending:
        return None
    try:
        generation = (owner[1], owner[3], owner[2])
        # These bookkeeping flags may still be settling even when BL2 has
        # already admitted a real local sprint in the destination. Verify the
        # actual state, without changing the rest of travel recovery.
        if (_travel_budget.active and _travel_generation == generation
                and pc.IsLocalPlayerController() and pc.bInSprintState
                and len(pc.SprintModifiers) > 0
                and _assist_context(pc) and _equip_activation_world_loaded(pc)):
            return (_travel_epoch, generation)
    except Exception:
        pass
    return None


def _equip_activation_world_loaded(pc):
    world = pc.WorldInfo
    return (world.Pauser is None and not world.IsPreparingMapChange()
            and world.Game is not None and not world.Game.bLevelChange)


def _equip_activation_world_ready(pc, *, settling=False):
    if (_travel_modal_open or _crosshair_modal_open
            or not _equip_activation_world_loaded(pc)):
        return False
    if not settling:
        return not _travel_close_pending
    # During the narrow destination exception, ask the live HUD. A stale
    # close-settling bit is not proof of a modal, and a real blocker still wins.
    pri, ui = pc.PlayerReplicationInfo, pc.GFxUIManager
    return (pri is not None and not pri.bGFxMenuOpen
            and ui is not None and not ui.IsBlockingMoviePlaying())


def _equip_sprint_veto(pc, pawn, pi, now, pending):
    """Return a specific blocker. Only unstable handoff state merits a retry."""
    if not (_active and _cfg_omni and _cfg_assist and _armed):
        return "disabled-or-walk"
    if _travel_pending:
        if (not _travel_budget.active or pending["travel_owner"] != (_travel_epoch, _travel_generation)
                or not _equip_activation_world_loaded(pc)):
            return "travel"
        if not _equip_activation_world_ready(pc, settling=True):
            return "destination-hud"
    if pc.WorldInfo.Pauser is not None:
        return "paused"
    if _crosshair_modal_open:
        return "modal-hud"
    if pc.PlayerReplicationInfo is None:
        return "missing-player-info"
    if pc.PlayerReplicationInfo.bGFxMenuOpen:
        return "gfx-menu"
    if pawn.DrivenVehicle is not None or pawn.bDeleteMe:
        return "not-on-foot"
    physics = int(pawn.Physics)
    if physics not in (1, 2):
        return "not-on-foot"
    if _assist_crouching(pc, pawn) or _crouch_sprint_paused():
        return "crouch"
    if (_deathtrap_skill_active or _zero_execute_waiting() or _knockback_until > now
            or pawn.bForceRMVelocity or pawn.bForceRegularVelocity or pawn.bRMForceStop
            or pc.bThrowingGrenade or pc.bPerformingMeleeAttack
            or pc.bPerformingSharedWeaponAction):
        return "action"
    if _fast_zoomed(pc, pawn.Weapon):
        return "ads"
    if pawn.bReloading or _pose_reload_active(pawn):
        return "reload"
    if not _assist_raw_moving(pc, pi=pi):
        return "no-movement"
    if not _assist_context(pc, pi=pi, pawn=pawn):
        return "input-context"
    if physics == 2:
        return "airborne"
    if pc.bPressedJump:
        return "jump-input"
    # The native end path must remove the prior sprint's modifiers before
    # DoSprint can safely apply a new set.
    if len(pc.SprintModifiers):
        return "sprint-modifiers"
    return None


def _stop_equip_sprint_frames():
    _set_hook("equip-sprint-input", False, POST_PLAYER_INPUT, Type.POST, _equip_sprint_input_post)
    _set_hook("equip-sprint-tick", False, PLAYER_TICK, Type.POST_UNCONDITIONAL, _equip_sprint_tick)


def _clear_equip_sprint_handoffs():
    global _drop_sprint_scope
    _drop_sprint_scope = None
    _equip_sprint_handoffs.clear()
    _stop_equip_sprint_frames()


def _watch_equip_sprint_frames():
    input_hook = _set_optional_hook("equip-sprint-input", True, POST_PLAYER_INPUT, Type.POST,
                                   _equip_sprint_input_post)
    # A reflected input hook can register without being dispatched by the live
    # game. PlayerTick also owns the deadline and provides a bounded fallback.
    tick_hook = _set_hook("equip-sprint-tick", True, PLAYER_TICK, Type.POST_UNCONDITIONAL,
                          _equip_sprint_tick)
    return bool(input_hook or tick_hook)


def _try_equip_sprint(pc, weapon, *, input_ready):
    owner = _equip_sprint_owner(pc, weapon)
    if owner is None:
        return "owner"
    hand = owner[0]
    pending = _equip_sprint_handoffs.get(hand)
    if pending is None or pending["owner"] != owner:
        return "owner"
    now = time.perf_counter()
    if now >= pending["deadline"] or pending["attempts"] >= 3:
        _equip_sprint_handoffs.pop(hand, None)
        return "expired"
    if pending["phase"] != "ready":
        return pending["phase"]
    if pc.bInSprintState:
        _equip_sprint_handoffs.pop(hand, None)
        return "already-sprinting"

    pawn, pi = pc.Pawn, pc.PlayerInput
    veto = _equip_sprint_veto(pc, pawn, pi, now, pending)
    if veto is not None:
        if veto == "destination-hud" and pending["activation"]:
            # A HUD veto at equip entry can clear after the native menu
            # handoff. Keep the exact destination retry and let the live
            # HUD continue to veto it until it is actually ready.
            pending["deadline"] = max(pending["deadline"], pending["started"] + 0.75)
            return veto
        if veto == "airborne" and pending["activation"]:
            # Keep this exact activation intent until landing, without
            # starting airborne sprint or replacing the velocity.
            pending["deadline"] = pending["started"] + 1.25
            return veto
        if (veto not in ("sprint-modifiers", "input-context", "modal-hud", "jump-input",
                         "missing-player-info", "gfx-menu")
                and not (veto == "no-movement" and not input_ready)):
            _equip_sprint_handoffs.pop(hand, None)
        return veto
    if _equip_sprint_owner(pc, weapon) != owner:
        _equip_sprint_handoffs.pop(hand, None)
        return "owner"
    pending["attempts"] += 1
    pending["phase"] = "calling"
    pawn.DoSprint(False)
    if _equip_sprint_handoffs.get(hand) is not pending:
        return "superseded"
    if pc.bInSprintState:
        _expire_equip_sprint_skid(pc, pending)
        if pending["activation"] and pending["attempts"] < 3:
            pending["phase"] = "resumed"
        else:
            _equip_sprint_handoffs.pop(hand, None)
        return "resumed"
    if pending["attempts"] >= 3:
        _equip_sprint_handoffs.pop(hand, None)
    else:
        pending["phase"] = "ready"
    return "native-denied"


def _resume_equip_sprint(weapon):
    if not _equip_sprint_handoffs:
        return False
    try:
        result = _try_equip_sprint(_pc(), weapon, input_ready=False)
        if _equip_sprint_handoffs and not _watch_equip_sprint_frames():
            _equip_sprint_handoffs.clear()
        return result == "resumed"
    except Exception:
        _clear_equip_sprint_handoffs()
        return False


def _equip_sprint_frame(pc, *, input_hook=True):
    if not (_active and _cfg_omni and _cfg_assist and _armed and pc is not None):
        _clear_equip_sprint_handoffs()
        return
    try:
        pawn, pi = pc.Pawn, pc.PlayerInput
        if pawn is None or pi is None:
            _clear_equip_sprint_handoffs()
            return
        now = time.perf_counter()
        if not _assist_raw_moving(pc, pi=pi):
            # Never retry on neutral input. A verified context change can
            # clear one native sample before the held input is read again.
            if input_hook:
                for hand, pending in tuple(_equip_sprint_handoffs.items()):
                    weapon = pawn.OffHandWeapon if hand == 1 else pawn.Weapon
                    if (now < min(pending["context_zero_until"], pending["deadline"])
                            and _equip_sprint_owner(pc, weapon) == pending["owner"]):
                        pending["context_zero_until"] = -1.0
                    else:
                        _equip_sprint_handoffs.pop(hand, None)
                if not _equip_sprint_handoffs:
                    _stop_equip_sprint_frames()
            elif not any(now < max(pending["started"] + 0.03, pending["context_zero_until"])
                         for pending in _equip_sprint_handoffs.values()):
                _clear_equip_sprint_handoffs()
            return
        for hand, pending in tuple(_equip_sprint_handoffs.items()):
            weapon = pawn.Weapon if hand == 0 else pawn.OffHandWeapon
            if (now >= pending["deadline"]
                    or _equip_sprint_owner(pc, weapon) != pending["owner"]
                    or (pending["phase"] == "resumed" and not pc.bInSprintState)):
                _equip_sprint_handoffs.pop(hand, None)
            elif pending["phase"] == "ready":
                if input_hook:
                    pending["last_input"] = now
                    if pending["context_zero_until"] > 0.0:
                        pending["context_zero_until"] = -1.0
                elif ("equip-sprint-input" in _hooks
                      and now - (pending["last_input"] or pending["started"]) < 0.03):
                    continue
                _try_equip_sprint(pc, weapon, input_ready=True)
        if not _equip_sprint_handoffs:
            _stop_equip_sprint_frames()
    except Exception:
        _clear_equip_sprint_handoffs()


def _equip_sprint_input_post(obj, args, ret, func):
    if _local_input(obj) is not None:
        _equip_sprint_frame(_pc())


def _equip_sprint_tick(obj, args, ret, func):
    pc = _local_pc(obj)
    if pc is not None:
        _equip_sprint_frame(pc, input_hook=False)


def _stop_activation_input_frame():
    global _activation_input_frame
    _activation_input_frame = None
    _set_hook("activation-input-frame", False, INPUT_FRAME, Type.POST_UNCONDITIONAL,
              _activation_gamepad_frame)


def _activation_stick_aligned(native, physical):
    if physical is None or len(physical) != 2:
        return False
    x, y = native
    px, py = physical
    n, p = math.hypot(x, y), math.hypot(px, py)
    return (0.25 <= n <= 1.5 and 0.30 <= p <= 1.45
            and x * px + y * py >= 0.92 * n * p)


def _capture_activation_input(obj, args):
    global _activation_input_frame
    if not (_active and _cfg_omni and _cfg_assist and _armed):
        return
    try:
        if str(args.NewContextName) != "Gunzerking" or str(obj.CurrentInputContextName) == "Gunzerking":
            return
        pi = _local_input(obj)
        pc = None if pi is None else _pc()
        if pc is None or pc.Pawn is None or pc.Pawn.OffHandWeapon is None or _travel_input.synthetic:
            return
        pending = _equip_sprint_handoffs.get(1)
        if not (pc.bInSprintState or (pending is not None and pending["activation"])):
            return
        settling = bool(_travel_pending and _travel_budget.active and pending is not None
                        and pending["travel_owner"] == (_travel_epoch, _travel_generation))
        if ((_travel_pending and not settling)
                or not _equip_activation_world_ready(pc, settling=settling)
                or not _assist_context(pc, pi=pi)):
            return
        owner = (pc._get_address(), pc.Pawn._get_address(), pi._get_address())
        native = (float(pi.aBaseY), float(pi.aStrafe))
        physical = _travel_input.activation_stick_sample(pi)
        if physical is None:
            # Steam Input or ambiguous XInput slots can leave the independent
            # sample unavailable even while the local engine sees a gamepad.
            # Require both live native axes and the preceding native snapshot
            # to agree before borrowing the snapshot for the context-drop frame.
            # Capturing an engine sample does not write input. The writer
            # still requires current focus and local gamepad mode, so a
            # transient focus read at capture cannot discard a valid frame.
            if not pi.bUsingGamepad:
                return
            raw = (float(pi.RawJoyUp), float(pi.RawJoyRight))
            if (not _activation_stick_aligned(native, raw)
                    or abs(math.hypot(*native) - math.hypot(*raw)) > 0.25):
                return
            native = raw
        else:
            if math.hypot(*native) <= 0.015:
                native = (float(pi.RawJoyUp), float(pi.RawJoyRight))
                if abs(math.hypot(*native) - math.hypot(*physical)) > 0.25:
                    return
            if not _activation_stick_aligned(native, physical):
                return
        if _set_optional_hook("activation-input-frame", True, INPUT_FRAME,
                              Type.POST_UNCONDITIONAL, _activation_gamepad_frame):
            _activation_input_frame = (owner, time.perf_counter() + 0.08, native, physical,
                                       _travel_epoch if settling else None)
    except Exception:
        return


def _activation_gamepad_frame(obj, args, ret, func):
    pi = _local_input(obj)
    if pi is None:
        return
    pending = _activation_input_frame
    _stop_activation_input_frame()
    if pending is None:
        return
    try:
        pc = _pc()
        if (not (_active and _cfg_omni and _cfg_assist and _armed) or pc is None
                or pc.Pawn is None or time.perf_counter() >= pending[1]
                or not _same(pc.PlayerInput, pi)
                or (pc._get_address(), pc.Pawn._get_address(), pi._get_address()) != pending[0]
                or str(pi.CurrentInputContextName) != "Gunzerking"
                or (_travel_pending and (pending[4] != _travel_epoch or not _travel_budget.active))
                or not _equip_activation_world_ready(pc, settling=_travel_pending)
                or not _skill_assist_context(pc, pi=pi)):
            return
        physical = _travel_input.activation_stick_sample(pi)
        if pending[3] is None:
            # Recheck native gamepad ownership. A recovered independent
            # sample can veto the fallback on a release or direction change.
            allowed = (_travel_input.activation_gamepad_active(pi)
                       and (physical is None or (
                           _activation_stick_aligned(pending[2], physical)
                           and abs(math.hypot(*pending[2]) - math.hypot(*physical)) <= 0.25)))
        else:
            allowed = (_activation_stick_aligned(pending[2], physical)
                       and math.dist(pending[3], physical) <= 0.18)
        if allowed:
            _travel_input.bridge_activation_frame(pi, pending[2])
            if _travel_input.synthetic:  # failed rollback, retry only cleanup
                _stop_travel_input_handoff()
    except Exception:
        return


def _equip_sprint_context_pre(obj, args, ret, func):
    _capture_activation_input(obj, args)
    pending = _equip_sprint_handoffs.get(1)
    if pending is None or not pending["activation"]:
        return
    try:
        pi = _local_input(obj)
        pc = None if pi is None else _pc()
        pending["context_before"] = None
        if (pc is not None and (pending["phase"] == "ready"
                                or (pending["phase"] == "resumed" and pc.bInSprintState))
                and _equip_sprint_owner(pc, pc.Pawn.OffHandWeapon) == pending["owner"]
                and _assist_raw_moving(pc, pi=pi)):
            pending["context_before"] = str(pi.CurrentInputContextName)
    except Exception:
        pass


def _equip_sprint_context_post(pc, pi):
    pending = _equip_sprint_handoffs.get(1)
    if pending is None:
        return
    try:
        before = pending["context_before"]
        pending["context_before"] = None
        if (before is None or before == "Gunzerking"
                or str(pi.CurrentInputContextName) != "Gunzerking"
                or pc is None
                or _equip_sprint_owner(pc, pc.Pawn.OffHandWeapon) != pending["owner"]):
            return
        if pending["context_zero_until"] == 0.0:
            # SetInputContext was seen clearing one native input frame in
            # the live trace. Never resume on zero input, but don't discard
            # this verified retry until the next actual input sample.
            pending["context_zero_until"] = time.perf_counter() + 0.04
        if pending["phase"] != "resumed":
            return
        if pc.bInSprintState:
            _equip_sprint_handoffs.pop(1, None)
            return
        # Verified second interruption, bracketed by the exact local context call.
        pending["activation"] = False
        pending["phase"] = "ready"
        _try_equip_sprint(pc, pc.Pawn.OffHandWeapon, input_ready=False)
    except Exception:
        _equip_sprint_handoffs.pop(1, None)


def _weapon_transition_begin(obj, args, ret, func):
    if not _active or not _weapon_belongs_to_local(obj):
        return
    _weapon_transition_addresses.add(obj._get_address())
    _weapon_transition_refs[obj._get_address()] = unrealsdk.unreal.WeakPointer(obj)
    _clear_direction()
    if _cfg_crosshair:
        _release_swapping_reticle(obj)
    pi = None if _pi_ref is None else _pi_ref()
    if pi is not None and _cfg_assist:
        if not (_cfg_omni and _armed) or _fire_requested:
            _set_try_sprint(pi, False)
        elif not pi.bTryToSprint:
            # Keep a stationary request queued across swaps. If BL2 already
            # consumed it, the ordinary recovery tick can retry mid-swap.
            pc = _pc()
            if pc is not None and not pc.bInSprintState:
                _schedule_assist_recovery(pc)


def _weapon_equip_begin(obj, args, ret, func):
    _weapon_transition_begin(obj, args, ret, func)
    _resume_equip_sprint(obj)


def _weapon_equip_end(obj, args, ret, func):
    _weapon_transition_end(obj, args, ret, func)
    if not (_active and _cfg_hip_fire):
        return
    pc = _pc()
    if pc is not None and pc.bInSprintState and _equip_sprint_owner(pc, obj) is not None:
        _sprint_fire_entry(pc, after_equip=True)


def _offhand_equip_pending_fire(obj, args, ret, func):
    """Reconcile only the incoming offhand queue before Active consumes it."""
    _mainhand_equip_pending_fire(obj, args)
    if not (_active and _cfg_hip_fire and not _crosshair_modal_open and not _travel_modal_open):
        return
    try:
        if str(args.NextStateName) != "Active":
            return
        pc = _pc()
        owner = _equip_sprint_owner(pc, obj)
        if owner is None or owner[0] != 1 or not obj.bOffHand:
            return
        pawn = pc.Pawn
        if (str(pc.PlayerInput.CurrentInputContextName) != "Gunzerking"
                or not _basic_context(pc, pawn=pawn, fire_pose_only=True)
                or int(obj.Role) != 3 or pc.WorldInfo.Pauser is not None
                or pc.PlayerReplicationInfo is None or pc.PlayerReplicationInfo.bGFxMenuOpen
                or obj.GetPendingFireLength() <= 0):
            return
        held = (_fire_requested and not pc.bStartFireZeroBeforeGunzerking
                if pc.bGunzerkingAutoSwitch else _pose_offhand_requested)
        pending = bool(obj.PendingFire(0))
        if not held and pending:
            obj.ClearPendingFire(0)
        elif held and not pending and not pawn.bNoWeaponFiring and not obj.bWeaponPutDown:
            obj.SetPendingFire(0)
    except Exception:
        return


def _mainhand_equip_pending_fire(obj, args):
    """Queue a held main-hand trigger on its locally owned incoming weapon."""
    if not (_active and _cfg_hip_fire and not _crosshair_modal_open and not _travel_modal_open):
        return
    try:
        if str(args.NextStateName) != "Active":
            return
        pc = _pc()
        owner = _equip_sprint_owner(pc, obj)
        if owner is None or owner[0] != 0 or obj.bOffHand:
            return
        pawn = pc.Pawn
        if (not _basic_context(pc, pawn=pawn, fire_pose_only=True)
                or int(obj.Role) != 3 or pc.WorldInfo.Pauser is not None
                or pc.PlayerReplicationInfo is None or pc.PlayerReplicationInfo.bGFxMenuOpen
                or obj.GetPendingFireLength() <= 0):
            return
        dual = pawn.OffHandWeapon is not None and str(pc.PlayerInput.CurrentInputContextName) == "Gunzerking"
        held = _pose_offhand_requested if dual and pc.bGunzerkingAutoSwitch else _fire_requested
        if held and not obj.PendingFire(0) and not pawn.bNoWeaponFiring and not obj.bWeaponPutDown:
            obj.SetPendingFire(0)
    except Exception:
        return


def _weapon_transition_end(obj, args, ret, func):
    if not _active or obj is None:
        return
    try:
        address = obj._get_address()
        _weapon_transition_addresses.discard(address)
        reference = _weapon_transition_refs.pop(address, None)
        # A tracked local put-down can finish after the pawn changes weapons.
        if reference is None or not _same(reference(), obj):
            instigator = obj.Instigator
            if not _is_local_pawn(instigator):
                pc = _pc()
                pawn = None if pc is None else pc.Pawn
                if pawn is None or not (_same(obj, pawn.Weapon)
                                        or _same(obj, pawn.OffHandWeapon)):
                    return
    except Exception:
        # Unreadable ownership cannot justify local recovery or HUD work.
        return

    # Sprint Assist now uses BL2's native pending-sprint flag instead of a
    # permanent movement callback. Re-arm when a weapon transition finishes.
    if _active and _cfg_assist and _armed:
        pi = None if _pi_ref is None else _pi_ref()
        if pi is not None:
            if _deathtrap_skill_active:
                _schedule_deathtrap_recovery(_pc())
            elif _fire_requested:
                _set_try_sprint(pi, False)
            else:
                _assist_recover_or_schedule(_pc())

    # Reacquire the native reticle once after a weapon transition if sprint is
    # still active. This is event-driven and replaces per-frame HUD polling.
    if _active and _cfg_crosshair:
        pc = _pc()
        if pc is not None and pc.bInSprintState:
            _schedule_crosshair_resync(pc, ticks=24, preserve_widget=True)
            _schedule_offhand_crosshair(pc)


def _install_weapon_state_hooks():
    global _weapon_state_events_complete
    begin_count = 0
    end_count = 0
    for tag, candidates, is_begin in WEAPON_STATE_PATHS:
        hook_tag = "weapon-" + tag
        if hook_tag in _hooks:
            if is_begin:
                begin_count += 1
            else:
                end_count += 1
            continue

        callback = (_weapon_equip_begin if tag == "equip-begin" else
                    _weapon_equip_end if tag == "equip-end" else
                    _weapon_transition_begin if is_begin else _weapon_transition_end)
        installed = False
        for path in candidates:
            if _set_optional_hook(
                hook_tag,
                True,
                path,
                Type.POST_UNCONDITIONAL,
                callback,
            ):
                installed = True
                break

        if installed:
            if is_begin:
                begin_count += 1
            else:
                end_count += 1

    equip_end = _hooks.get("weapon-equip-end")
    if equip_end is not None and _cfg_hip_fire:
        _set_optional_hook("offhand-equip-fire", True, equip_end[0], Type.PRE,
                           _offhand_equip_pending_fire)
    else:
        _set_hook("offhand-equip-fire", False, "", Type.PRE, _offhand_equip_pending_fire)
    if "weapon-equip-begin" in _hooks:
        post = _set_optional_hook("equip-sprint-post", True, WEAPON_BRINGUP,
                                  Type.POST, _equip_sprint_bringup_post)
        if post:
            _set_optional_hook("equip-sprint-pre", True, WEAPON_BRINGUP,
                               Type.PRE, _equip_sprint_bringup_pre)
            _set_optional_hook("equip-sprint-context-pre", True, SET_INPUT_CONTEXT,
                               Type.PRE, _equip_sprint_context_pre)
    _sync_drop_sprint_hooks(_cfg_omni and _cfg_assist and "weapon-accuracy" in _hooks
                            and "equip-sprint-pre" in _hooks and "equip-sprint-post" in _hooks)
    _weapon_state_events_complete = begin_count == 2 and end_count == 2
    if not _weapon_state_events_complete:
        pass


def _remove_weapon_state_hooks():
    global _weapon_state_events_complete
    _clear_equip_sprint_handoffs()
    _set_hook("offhand-equip-fire", False, "", Type.PRE, _offhand_equip_pending_fire)
    _sync_drop_sprint_hooks(False)
    _set_hook("equip-sprint-context-pre", False, SET_INPUT_CONTEXT,
              Type.PRE, _equip_sprint_context_pre)
    _set_hook("equip-sprint-pre", False, WEAPON_BRINGUP, Type.PRE, _equip_sprint_bringup_pre)
    _set_hook("equip-sprint-post", False, WEAPON_BRINGUP, Type.POST, _equip_sprint_bringup_post)
    for tag, candidates, is_begin in WEAPON_STATE_PATHS:
        callback = (_weapon_equip_begin if tag == "equip-begin" else
                    _weapon_equip_end if tag == "equip-end" else
                    _weapon_transition_begin if is_begin else _weapon_transition_end)
        current = _hooks.get("weapon-" + tag)
        if current is not None:
            _set_hook(
                "weapon-" + tag,
                False,
                current[0],
                current[1],
                callback,
            )
    _weapon_state_events_complete = False
    _weapon_transition_addresses.clear()
    _weapon_transition_refs.clear()


def _continuation_enter(obj, args, ret, func):
    global _continuation_depth
    if _active and _is_local_pawn(obj):
        _continuation_depth += 1


def _continuation_leave(obj, args, ret, func):
    global _continuation_depth
    if _is_local_pawn(obj):
        _continuation_depth = max(0, _continuation_depth - 1)


def _sprint_firing_exclusion(obj, args, ret, func):
    if not (_active and _cfg_hip_fire and _continuation_depth):
        return
    pc = _pc()
    if pc is None or not pc.bInSprintState or not _basic_context(pc, fire_pose_only=True):
        return
    pawn = pc.Pawn
    weapon = pawn.Weapon
    if not _same(obj, weapon):
        return
    if (
        int(pawn.Physics) != 1
        or _fast_zoomed(pc, weapon)
        or pc.bDuck
        or pc.bPressedJump
        or pawn.bIsCrouched
        or pawn.bWantsToCrouch
        or pawn.bReloading
        or pawn.bForceRMVelocity
        or pawn.bForceRegularVelocity
        or pawn.bRMForceStop
    ):
        return
    return Block, False


def _sync_fire_hooks(enable):
    global _hip_hooks_live, _hip_hooks_until
    # Native bytecode owns the main-weapon IsFiring exclusion when validated.
    # Keep the old sprint-scoped Python hooks only as a compatibility fallback.
    enable = bool(
        enable
        and _active
        and _cfg_hip_fire
        and HIP_FIRE_AVAILABLE
        and not _native_hipfire_active
    )
    _set_hook("continue-pre", enable, CONTINUE, Type.PRE, _continuation_enter)
    _set_hook("continue-post", enable, CONTINUE, Type.POST_UNCONDITIONAL, _continuation_leave)
    _set_hook("firing-exclusion", enable, FIRING_STATE, Type.PRE, _sprint_firing_exclusion)
    _hip_hooks_live = enable
    if not enable:
        # These are temporary sprint-continuation hooks only. Trigger ownership is
        # tracked by the cheap StartFire/StopFire boundary hooks so Sprint Assist
        # can recover correctly even if sprint ends while the trigger is held.
        _hip_hooks_until = 0.0
        _set_hook("hip-cleanup", False, PLAYER_TICK, Type.POST_UNCONDITIONAL, _hip_cleanup_tick)


def _sync_fire_input_hooks(enable):
    """Track trigger press/release boundaries without any per-shot callback."""
    global _fire_requested, _pose_offhand_requested, _fire_until
    enable = bool(enable and _active)
    _set_hook("start-fire", enable, START_FIRE, Type.PRE, _start_fire)
    _set_hook("stop-fire", enable, STOP_FIRE, Type.POST_UNCONDITIONAL, _stop_fire)
    if not enable:
        _fire_requested = False
        _pose_offhand_requested = False
        _fire_until = 0.0




def _stop_fire_state_recovery():
    _set_hook(
        "fire-state-recovery",
        False,
        PLAYER_TICK,
        Type.POST_UNCONDITIONAL,
        _fire_state_recovery_tick,
    )


def _weapons_still_firing(pc):
    """Use BL2's actual weapon-state functions, not the earlier StopFire input edge."""
    pawn = None if pc is None else pc.Pawn
    if pawn is None:
        return False
    for weapon in (pawn.Weapon, pawn.OffHandWeapon):
        if weapon is None:
            continue
        try:
            if bool(weapon.IsFiring()):
                return True
        except Exception:
            # If state cannot be resolved, do not consume Sprint Assist's one-shot
            # retry yet. The fallback watcher will try again on the next frame.
            return True
    return False


def _rearm_after_fire(pc):
    """Re-arm remembered Sprint only after the weapon truly leaves WeaponFiring."""
    if not (_active and _cfg_assist and _armed and pc is not None):
        _stop_fire_state_recovery()
        return False
    if pc.bInSprintState:
        _stop_fire_state_recovery()
        return True
    pi = pc.PlayerInput
    if pi is None:
        _stop_fire_state_recovery()
        return False
    if _fire_requested or _weapons_still_firing(pc):
        _set_try_sprint(pi, False)
        return False
    if _deathtrap_skill_active:
        _stop_fire_state_recovery()
        _schedule_deathtrap_recovery(pc)
        return False
    _stop_fire_state_recovery()
    # Action/weapon state may still be blocked (e.g. melee while a deployed
    # skill stays active); never prematurely consume BL2's one-shot request.
    return _assist_recover_or_schedule(pc)


def _fire_state_recovery_tick(obj, args, ret, func):
    """Fallback only if WeaponFiring.EndState cannot be hooked on this install."""
    pc = _local_pc(obj)
    if pc is None or not (_active and _cfg_assist and _armed):
        _stop_fire_state_recovery()
        return
    if _fire_requested:
        _stop_fire_state_recovery()
        return
    _rearm_after_fire(pc)


def _schedule_fire_state_recovery():
    if not (_active and _cfg_assist and _armed):
        _stop_fire_state_recovery()
        return
    _set_hook(
        "fire-state-recovery",
        True,
        PLAYER_TICK,
        Type.POST_UNCONDITIONAL,
        _fire_state_recovery_tick,
    )


def _weapon_firing_ended(obj, args, ret, func):
    """Exact native state boundary: StopFire can precede this on automatic guns."""
    if not (_active and _weapon_belongs_to_local(obj)):
        return
    pc = _pc()
    if pc is None or _fire_requested:
        return
    _rearm_after_fire(pc)


def _sync_weapon_firing_end_hook(enable):
    global _weapon_firing_end_available
    enable = bool(enable and _active and _cfg_assist)
    if not enable:
        current = _hooks.get("weapon-firing-end")
        if current is not None:
            _set_hook(
                "weapon-firing-end",
                False,
                current[0],
                current[1],
                _weapon_firing_ended,
            )
        _weapon_firing_end_available = False
        _stop_fire_state_recovery()
        return

    if "weapon-firing-end" in _hooks:
        _weapon_firing_end_available = True
        return

    _weapon_firing_end_available = False
    for path in WEAPON_FIRING_END_PATHS:
        if _set_optional_hook(
            "weapon-firing-end",
            True,
            path,
            Type.POST,
            _weapon_firing_ended,
        ):
            _weapon_firing_end_available = True
            break
    if not _weapon_firing_end_available:
        pass


def _pose_reload_active(pawn):
    """Check both held weapons at pose boundaries, including mid-reload enable."""
    if pawn.bReloading:
        return True
    offhand = pawn.OffHandWeapon
    if offhand is None and not _pose_reload_addresses:
        return False
    try:
        weapons = tuple(w for w in (pawn.Weapon, offhand) if w is not None)
        live_addresses = {int(w._get_address()) for w in weapons}
        _pose_reload_addresses.intersection_update(live_addresses)
        if offhand is None:
            return bool(_pose_reload_addresses)
        return bool(_pose_reload_addresses or any(
            str(w.GetStateName()).lower() == "weaponreloading" for w in weapons))
    except Exception:
        return True  # Unknown dual-wield reload state cannot justify a pose edit.


def _fire_pose_eligible(pc, pawn, weapon, *, interrupted, zoomed):
    return bool(
        _cfg_hip_fire
        and pc.bInSprintState
        and pawn.bIsSprinting
        and int(pawn.Physics) in (1, 2)
        and weapon is not None
        and not interrupted
        and not zoomed
        and not pc.bPendingZoom
        and not pawn.bIsCrouched
        and not pawn.bWantsToCrouch
        and (not _pose_reload_active(pawn) or _opposite_hand_fire_during_reload(pc, pawn))
        and not pawn.bIsInjured
        and not pawn.bIsDead
        and not pawn.bForceRMVelocity
        and not pawn.bForceRegularVelocity
        and not pawn.bRMForceStop
        and not pc.bCinematicMode
        and not pc.bIgnoreMoveInput
        and firing_pose_context(pc, pawn, pc.PlayerInput)
    )


def _ready_gunzerk_hand_can_fire_during_swap(pc, pawn):
    """One hand's equip must not suppress the other hand's firing pose."""
    try:
        main, offhand = pawn.Weapon, pawn.OffHandWeapon
        if (main is None or offhand is None or not _combat_fire_mask
                or str(pc.PlayerInput.CurrentInputContextName) != "Gunzerking"
                or pc.bThrowingGrenade or pc.bPerformingMeleeAttack
                or pc.bPerformingSharedWeaponAction):
            return False
        main_ready = _crosshair_weapon_ready(main)
        offhand_ready = _crosshair_weapon_ready(offhand)
        if main_ready == offhand_ready:
            return False
        offhand_bit = 1 if pc.bGunzerkingAutoSwitch else 2
        return bool(_combat_fire_mask & (offhand_bit if offhand_ready else 3 ^ offhand_bit))
    except Exception:
        return False


def _update_fire_pose(pc, *, firing, now, interrupted=None, zoomed=None):
    pawn = pc.Pawn
    weapon = None if pawn is None else pawn.Weapon
    if pawn is None:
        _fire_pose.restore()
        return
    if interrupted is None:
        interrupted = _interrupted(pc)
    if interrupted and firing and _ready_gunzerk_hand_can_fire_during_swap(pc, pawn):
        interrupted = False
    if zoomed is None:
        zoomed = _fast_zoomed(pc, weapon)
    eligible = _fire_pose_eligible(
        pc, pawn, weapon, interrupted=interrupted, zoomed=zoomed
    )
    _fire_pose.update(pc, eligible=eligible, firing=firing, now=now)


def _stop_pose_reload_retry():
    global _pose_reload_retry
    _pose_reload_retry = 0
    _set_hook("pose-reload-retry", False, PLAYER_TICK, Type.POST_UNCONDITIONAL,
              _pose_reload_retry_tick)


def _pose_reload_retry_tick(obj, args, ret, func):
    global _pose_reload_retry
    pc = _local_pc(obj)
    if (pc is None or not _active or not _cfg_hip_fire or not pc.bInSprintState
            or not _pose_fire_requested() or _pose_reload_retry <= 0):
        _stop_pose_reload_retry()
        return
    pawn = pc.Pawn
    other_hand = pawn is not None and _opposite_hand_fire_during_reload(pc, pawn)
    if _pose_reload_addresses and not other_hand:
        _stop_pose_reload_retry()
        return
    _pose_reload_retry -= 1
    if pawn is not None and (not pawn.bReloading or other_hand):
        _update_fire_pose(pc, firing=True, now=float(pc.WorldInfo.TimeSeconds))
        if _fire_pose.aliases_active:
            _stop_pose_reload_retry()
    if _pose_reload_retry <= 0:
        _stop_pose_reload_retry()


def _pose_reload_begin(obj, args, ret, func):
    if not (_active and _cfg_hip_fire and _weapon_belongs_to_local(obj)):
        return
    _pose_reload_addresses.add(obj._get_address())
    _stop_pose_reload_retry()
    pc = _pc()
    if pc is not None and pc.bInSprintState and _opposite_hand_fire_during_reload(pc, pc.Pawn):
        _update_fire_pose(pc, firing=True, now=float(pc.WorldInfo.TimeSeconds))
        return
    _fire_pose.restore()


def _pose_reload_end_retry():
    global _pose_reload_retry
    _pose_reload_retry = 60
    _set_hook("pose-reload-retry", True, PLAYER_TICK, Type.POST_UNCONDITIONAL,
              _pose_reload_retry_tick)


def _pose_reload_end(obj, args, ret, func):
    try:
        address = obj._get_address()
    except Exception:
        return
    if address not in _pose_reload_addresses:
        return
    _pose_reload_addresses.discard(address)
    pc = _pc()
    if not (_active and _cfg_hip_fire and _pose_fire_requested()
            and (not _pose_reload_addresses or (pc is not None and pc.Pawn is not None
                 and _opposite_hand_fire_during_reload(pc, pc.Pawn)))):
        return
    _pose_reload_end_retry()


def _pose_fire_requested():
    """A dual-wield pose remains active until both fire inputs are released."""
    global _pose_offhand_requested
    if _fire_requested:
        return True
    if not _pose_offhand_requested:
        return False
    pc = _pc()
    pawn = None if pc is None else pc.Pawn
    if pawn is None or pawn.OffHandWeapon is None:
        _pose_offhand_requested = False
        return False
    return True


def _hip_cleanup_tick(obj, args, ret, func):
    """Short-lived cleanup tick used only after hip-fire release."""
    global _pose_release_at
    pc = _local_pc(obj)
    if pc is None:
        return
    if not (_active and _cfg_hip_fire and pc.bInSprintState):
        _fire_pose.restore()
        _sync_fire_hooks(False)
        return

    # If another fire press happened before the release blend completed, the
    # StartFire event owns the pose again and no cleanup tick is needed.
    if _pose_fire_requested():
        _set_hook("hip-cleanup", False, PLAYER_TICK, Type.POST_UNCONDITIONAL, _hip_cleanup_tick)
        return

    now = float(pc.WorldInfo.TimeSeconds)
    if _pose_release_at and now >= _pose_release_at:
        _update_fire_pose(pc, firing=False, now=now)
        _pose_release_at = 0.0

    if _hip_hooks_live and now >= _hip_hooks_until:
        _sync_fire_hooks(False)
        return

    if not _pose_release_at and not _hip_hooks_live:
        _set_hook("hip-cleanup", False, PLAYER_TICK, Type.POST_UNCONDITIONAL, _hip_cleanup_tick)


def _schedule_hip_cleanup():
    _set_hook(
        "hip-cleanup",
        True,
        PLAYER_TICK,
        Type.POST_UNCONDITIONAL,
        _hip_cleanup_tick,
    )


def _action_skid_tick(obj, args, ret, func):
    pc = _local_pc(obj)
    if pc is None:
        _action_skid.restore()
    else:
        _action_skid.update(pc)
    if _action_skid.owner is None:
        _set_hook("action-skid-tick", False, PLAYER_TICK, Type.POST_UNCONDITIONAL, _action_skid_tick)


def _schedule_action_skid_tick():
    if _action_skid.owner is not None:
        _set_hook(
            "action-skid-tick",
            True,
            PLAYER_TICK,
            Type.POST_UNCONDITIONAL,
            _action_skid_tick,
        )


def _clear_crosshair_widget():
    global _crosshair_widget, _crosshair_movie_address, _crosshair_retry_at
    _crosshair_widget = None
    _crosshair_movie_address = 0
    _crosshair_retry_at = 0.0


def _get_crosshair_widget(pc, *, retry_interval=0.5):
    global _crosshair_widget, _crosshair_movie_address, _crosshair_retry_at
    hud = pc.myHUD
    movie = None if hud is None else hud.HUDMovie
    if movie is None:
        _clear_crosshair_widget()
        return None

    movie_address = movie._get_address()
    widget = None if _crosshair_widget is None else _crosshair_widget()
    if (
        _crosshair_movie_address == movie_address
        and widget is not None
        and _same(widget.MyHUDMovie, movie)
    ):
        primary = getattr(movie, "CrosshairWidget", None)
        if primary is None or _same(primary, widget):
            return widget
        _crosshair_widget = None
        _crosshair_retry_at = 0.0

    if movie_address != _crosshair_movie_address:
        _crosshair_widget = None
        _crosshair_movie_address = movie_address
        _crosshair_retry_at = 0.0

    # The direct widget can become usable between global search attempts.
    widget = _owned_crosshair_widget(movie)
    if widget is None:
        now = float(pc.WorldInfo.TimeSeconds)
        if now < _crosshair_retry_at:
            return None
        _crosshair_retry_at = now + max(0.0, float(retry_interval))
        widget = _find_crosshair_widget(movie)
    if widget is not None:
        _crosshair_widget = unrealsdk.unreal.WeakPointer(widget)
    return widget


def _owned_crosshair_widget(movie):
    """Read the current HUD's native normal reticle without a global search.

    The BL2 movie owns CrosshairWidget. Re-read it after cache invalidation;
    neither pause nor a replacement movie can reuse an older widget pointer.
    Readiness and weapon association remain the resync supervisor's checks.
    """
    try:
        widget = movie.CrosshairWidget
        if (widget is not None
                and widget.Class._inherits(unrealsdk.find_class(
                    "WillowGame.HUDWidget_Crosshairs"))
                and _same(widget.MyHUDMovie, movie)
                and not widget.bScopeCrosshair and not widget.bDualWieldCrosshair):
            return widget
    except Exception:
        pass
    # An unavailable/unsupported HUD retains the original cold fallback.
    return None


def _find_crosshair_widget(movie):
    """Cold fallback; measure the iteration, not just creation of an iterable."""
    for candidate in unrealsdk.find_all("WillowGame.HUDWidget_Crosshairs"):
        if (_same(candidate.MyHUDMovie, movie)
                and not candidate.bScopeCrosshair and not candidate.bDualWieldCrosshair):
            return candidate
    return None


def _crosshair_eligible(pc, weapon=None, *, hud=None):
    # While a modal game menu owns the HUD, do not lie about crosshair visibility.
    # BL2 temporarily removes/restores the weapon during this lifecycle, and native
    # GFx code must be allowed to complete that transition without Omni blocking it.
    if _crosshair_modal_open:
        return False
    if not (_active and _cfg_crosshair and pc is not None and pc.bInSprintState):
        return False
    pawn = pc.Pawn
    # Reticle visibility is presentation, like the already-fixed sprint-fire
    # arms pose. A deployed ability may keep a valid class-declared input
    # context (e.g. Scorpio) while ordinary firing and sprint stay active.
    # Require the same owned action skill + registered PlayerClass context
    # guard; do not widen the movement or sprint predicates.
    if pawn is None or not _basic_context(pc, pawn=pawn, fire_pose_only=True):
        return False
    if weapon is None:
        weapon = pawn.Weapon
    hud = pc.myHUD if hud is None else hud
    return bool(
        weapon is not None
        and hud is not None
        and hud.bShowHUD
        and bool(weapon.bCrosshairEnabled)
        # Crosshair While Sprinting deliberately owns the normal hip-fire
        # reticle through temporary crouch/reload/melee/weapon-state changes.
        # Native suppression flags are therefore not treated as permanent loss
        # of ownership. ADS/scope remains the hard exclusion.
        and not _fast_zoomed(pc, weapon)
    )


def _crosshair_weapon_ready(weapon):
    try:
        # HUD readiness follows native per-weapon state. A missed EndState
        # must not leave a reticle permanently hidden behind the assist ledger.
        return (weapon is not None and not weapon.bWeaponPutDown and not weapon.IsEquipping()
                and not weapon.IsPuttingDown())
    except Exception:
        return False


def _right_crosshair_ready(pc, widget):
    if not _crosshair_widget_ready(pc, widget) or not _crosshair_weapon_ready(pc.Pawn.Weapon):
        return False
    try:
        primary = pc.myHUD.HUDMovie.CrosshairWidget
    except AttributeError:
        primary = None
    return primary is None or _same(primary, widget)


def _hide_weapon_crosshair(widget):
    with unrealsdk.hooks.prevent_hooking_direct_calls():
        if int(widget.CrosshairVisibility) != 0:
            widget.SetCrosshairVisibility(bVisible=False)
        widget.OuterClip.SetVisible(False)
    widget.CrosshairVisibility = 0


def _offhand_crosshair_matches(pc, widget, *, require_clips=True):
    try:
        pawn, hud = pc.Pawn, pc.myHUD
        weapon = pawn.OffHandWeapon
        return bool(weapon is not None and hud is not None and hud.HUDMovie is not None
                    and widget.bDualWieldCrosshair and not widget.bScopeCrosshair
                    and _same(widget.MyHUDMovie, hud.HUDMovie) and _same(widget.WWeap, weapon)
                    and (not require_clips
                         or (widget.OuterClip is not None and widget.InnerClip is not None)))
    except Exception:
        return False


def _offhand_crosshair_eligible(pc, widget):
    return (_offhand_crosshair_matches(pc, widget)
            and _crosshair_eligible(pc, pc.Pawn.OffHandWeapon)
            and _crosshair_weapon_ready(pc.Pawn.OffHandWeapon)
            and not _fast_zoomed(pc, pc.Pawn.Weapon))


def _release_offhand_crosshair(*, refresh_native=True):
    global _offhand_crosshair
    owned, _offhand_crosshair = _offhand_crosshair, None
    if owned is None:
        return
    try:
        widget = owned[0]()
        if widget is None:
            return
        _hide_weapon_crosshair(widget)
        widget.InnerClip.SetVisible(owned[1])
        pc = _pc()
        if refresh_native and pc is not None and _offhand_crosshair_matches(pc, widget):
            with unrealsdk.hooks.prevent_hooking_direct_calls():
                widget.Update(0.0)
    except Exception:
        pass


def _release_swapping_reticle(weapon):
    """Hide only the reticle owned for the weapon entering a transition."""
    try:
        right = None if _crosshair_forced_widget is None else _crosshair_forced_widget()
        if right is not None and _same(right.WWeap, weapon):
            _release_crosshair_visibility(refresh_native=False, include_offhand=False)
        left = None if _offhand_crosshair is None else _offhand_crosshair[0]()
        if left is not None and _same(left.WWeap, weapon):
            _release_offhand_crosshair(refresh_native=False)
    except Exception:
        pass


def _select_offhand_crosshair(pc, widgets, updated=()):
    matches = [widget for widget in widgets if _offhand_crosshair_matches(pc, widget)]
    if len(matches) == 1:
        return matches[0]
    # A visible native widget, or equivalent wrappers for the same visible
    # clip, distinguishes the live presentation from a stale association.
    visible = [widget for widget in matches if int(widget.CrosshairVisibility) == 1
               and bool(widget.OuterClip.GetBool("_visible"))]
    if len(visible) == 1:
        return visible[0]
    live = [widget for widget in matches if widget._get_address() in updated]
    if len(live) == 1:
        return live[0]
    if matches and all(_same(matches[0].OuterClip, widget.OuterClip) for widget in matches[1:]):
        return matches[0]
    return None


def _show_offhand_crosshair(pc, widget):
    global _offhand_crosshair
    if not _offhand_crosshair_eligible(pc, widget):
        return False
    owned = None if _offhand_crosshair is None else _offhand_crosshair[0]()
    if owned is not None and _same(owned, widget):
        return True
    _release_offhand_crosshair(refresh_native=False)
    try:
        _offhand_crosshair = (unrealsdk.unreal.WeakPointer(widget),
                              bool(widget.InnerClip.GetBool("_visible")))
        with unrealsdk.hooks.prevent_hooking_direct_calls():
            widget.UpdateCrosshairFrame()
            widget.SetCrosshairVisibility(bVisible=True)
            widget.OuterClip.SetVisible(True)
            widget.InnerClip.SetVisible(True)
        widget.CrosshairVisibility = 0
        return True
    except Exception:
        _release_offhand_crosshair(refresh_native=False)
        return False


def _offhand_crosshair_visibility(pc, widget, args):
    owned = None if _offhand_crosshair is None else _offhand_crosshair[0]()
    try:
        if not _offhand_crosshair_eligible(pc, widget):
            if owned is not None and _same(owned, widget):
                _release_offhand_crosshair(refresh_native=False)
            if (_offhand_crosshair_matches(pc, widget)
                    and _crosshair_eligible(pc, pc.Pawn.OffHandWeapon)
                    and not _fast_zoomed(pc, pc.Pawn.Weapon)
                    and not _crosshair_weapon_ready(pc.Pawn.OffHandWeapon)):
                _hide_weapon_crosshair(widget)
                return Block
            return
        if owned is not None and _same(owned, widget):
            widget.CrosshairVisibility = 0
            return Block
        # A real native show, or a hide of a visible widget, identifies the
        # live widget when more than one cached association matches the gun.
        if owned is None and (bool(args.bVisible) or int(widget.CrosshairVisibility) == 1):
            if _show_offhand_crosshair(pc, widget):
                return Block
    except Exception:
        return


def _offhand_crosshair_update(obj, args, ret, func):
    # Only installed during a bounded retry. Association alone is ambiguous
    # when a stale copy of the same weapon's widget is also in this movie.
    if _offhand_crosshair_retry is None or _offhand_crosshair is not None:
        return
    try:
        pc = _pc()
        if _offhand_crosshair_matches(pc, obj):
            _offhand_native_updates[obj._get_address()] = unrealsdk.unreal.WeakPointer(obj)
    except Exception:
        pass


def _stop_offhand_crosshair_retry():
    global _offhand_crosshair_retry, _offhand_associated_widget
    _offhand_crosshair_retry = None
    _offhand_associated_widget = None
    _offhand_native_updates.clear()
    _set_hook("offhand-crosshair-update", False, CROSSHAIR_UPDATE, Type.POST,
              _offhand_crosshair_update)
    _set_hook("offhand-crosshair-retry", False, PLAYER_TICK, Type.POST_UNCONDITIONAL,
              _offhand_crosshair_tick)


def _schedule_offhand_crosshair(pc, *, pending_draw=False):
    global _offhand_crosshair_retry
    if not (_active and _cfg_crosshair and pc is not None and pc.Pawn is not None
            and (pending_draw or pc.Pawn.OffHandWeapon is not None)):
        return
    now = time.perf_counter()
    owner = pc._get_address(), pc.Pawn._get_address()
    pending = _offhand_crosshair_retry
    if pending is not None and pending[0] == owner and now < pending[1]:
        return
    _offhand_native_updates.clear()
    _offhand_crosshair_retry = (owner, now + 3.0, now)
    if not _set_hook("offhand-crosshair-retry", True, PLAYER_TICK, Type.POST_UNCONDITIONAL,
                     _offhand_crosshair_tick):
        _offhand_crosshair_retry = None
        return
    _set_optional_hook("offhand-crosshair-update", True, CROSSHAIR_UPDATE, Type.POST,
                       _offhand_crosshair_update)


def _offhand_crosshair_tick(obj, args, ret, func):
    global _offhand_crosshair_retry
    pc = _local_pc(obj)
    if pc is None:
        return
    try:
        pending = _offhand_crosshair_retry
        now = time.perf_counter()
        if (pending is None or not (_active and _cfg_crosshair) or pc.Pawn is None
                or pending[0] != (pc._get_address(), pc.Pawn._get_address())
                or now >= pending[1] or _crosshair_modal_open):
            _stop_offhand_crosshair_retry()
            return
        if now < pending[2]:
            return
        _offhand_crosshair_retry = (pending[0], pending[1], now + 0.10)
        owned = None if _offhand_crosshair is None else _offhand_crosshair[0]()
        associated = None if _offhand_associated_widget is None else _offhand_associated_widget()
        if (associated is not None and not _same(owned, associated)
                and _offhand_crosshair_matches(pc, associated, require_clips=False)):
            _show_offhand_crosshair(pc, associated)
            return
        if owned is not None and _offhand_crosshair_eligible(pc, owned):
            # A native HUD rebuild can hide the clip without changing WWeap.
            if not (owned.OuterClip.GetBool("_visible") and owned.InnerClip.GetBool("_visible")):
                with unrealsdk.hooks.prevent_hooking_direct_calls():
                    owned.OuterClip.SetVisible(True)
                    owned.InnerClip.SetVisible(True)
                owned.CrosshairVisibility = 0
            return
        _release_offhand_crosshair()
        weapon = pc.Pawn.OffHandWeapon
        if (not pc.bInSprintState or not _crosshair_weapon_ready(weapon)
                or not _crosshair_eligible(pc, weapon)):
            return
        updated = {address for address, ref in _offhand_native_updates.items()
                   if _offhand_crosshair_matches(pc, ref())}
        _offhand_native_updates.clear()
        widget = _select_offhand_crosshair(
            pc, unrealsdk.find_all("WillowGame.HUDWidget_Crosshairs"), updated)
        if widget is not None:
            _show_offhand_crosshair(pc, widget)
    except Exception:
        _stop_offhand_crosshair_retry()



def _take_crosshair_ownership(widget, *, keep_cache_hidden=True):
    global _crosshair_forced_widget, _crosshair_inner_visible
    owned = None if _crosshair_forced_widget is None else _crosshair_forced_widget()
    if owned is not None and not _same(owned, widget):
        _release_crosshair_visibility(include_offhand=False)
        owned = None

    if owned is None:
        try:
            _crosshair_inner_visible = bool(widget.InnerClip.GetBool("_visible"))
        except Exception:
            _crosshair_inner_visible = None
        _crosshair_forced_widget = unrealsdk.unreal.WeakPointer(widget)
        try:
            with unrealsdk.hooks.prevent_hooking_direct_calls():
                widget.UpdateCrosshairFrame()
                widget.SetCrosshairVisibility(bVisible=True)
            widget.OuterClip.SetVisible(True)
            widget.InnerClip.SetVisible(True)
            # Steady state keeps the native cache at Hidden so repeated native hide
            # requests become no-ops while the real clips remain visible. During a
            # pause/HUD lifecycle repair we deliberately keep this cache truthful
            # until the restored widget has settled for a few HUD updates.
            if keep_cache_hidden:
                widget.CrosshairVisibility = 0
        except Exception:
            _release_crosshair_visibility(include_offhand=False)
            raise
        return True
    return False


def _ensure_crosshair_visible(pc, widget=None, *, force_clips=False):
    global _crosshair_failure_logged
    if not _crosshair_eligible(pc):
        _release_crosshair_visibility(include_offhand=False)
        return False

    if widget is None:
        widget = _get_crosshair_widget(pc)
    if widget is None or bool(widget.bScopeCrosshair) or bool(widget.bDualWieldCrosshair):
        _release_crosshair_visibility(include_offhand=False)
        return False

    if not _right_crosshair_ready(pc, widget):
        _release_crosshair_visibility(refresh_native=False, include_offhand=False)
        return False
    try:
        _take_crosshair_ownership(widget)
        if force_clips:
            widget.OuterClip.SetVisible(True)
            widget.InnerClip.SetVisible(True)
            widget.CrosshairVisibility = 0
        return True
    except Exception as exc:
        _release_crosshair_visibility(include_offhand=False)
        _clear_crosshair_widget()
        if not _crosshair_failure_logged:
            logging.warning("omnimovement: sprint reticle update failed: " + repr(exc))
            _crosshair_failure_logged = True
        return False


def _release_crosshair_visibility(*, refresh_native=True, include_offhand=True):
    global _crosshair_forced_widget, _crosshair_inner_visible
    if include_offhand:
        _release_offhand_crosshair(refresh_native=refresh_native)
    widget = None if _crosshair_forced_widget is None else _crosshair_forced_widget()
    inner_visible = _crosshair_inner_visible
    _crosshair_forced_widget = None
    _crosshair_inner_visible = None
    if widget is None:
        return

    pc = _pc()
    try:
        if int(widget.CrosshairVisibility) == 0:
            widget.CrosshairVisibility = 1
        with unrealsdk.hooks.prevent_hooking_direct_calls():
            widget.SetCrosshairVisibility(bVisible=False)
        if inner_visible is not None:
            widget.InnerClip.SetVisible(inner_visible)
        if (
            refresh_native and pc is not None
            and _right_crosshair_ready(pc, widget)
            and pc.myHUD is not None
            and _same(widget.MyHUDMovie, pc.myHUD.HUDMovie)
        ):
            with unrealsdk.hooks.prevent_hooking_direct_calls():
                widget.Update(0.0)
    except Exception:
        pass


def _crosshair_visibility_pre(obj, args, ret, func):
    """Own sprint reticle visibility only when native code tries to change it."""
    pc = _pc()
    if pc is None:
        return
    hud = pc.myHUD
    if hud is None or not _same(obj.MyHUDMovie, hud.HUDMovie):
        return

    if bool(obj.bDualWieldCrosshair):
        return _offhand_crosshair_visibility(pc, obj, args)

    # While eligible, the real clips are already visible but the native cache is
    # intentionally kept at Hidden. This prevents HUD Update from repeatedly
    # requesting the same sprint-hide transition. Block both True and False
    # setter calls so the cache cannot be flipped back to Visible behind us.
    eligible = _crosshair_eligible(pc, hud=hud)
    if eligible:
        try:
            eligible = not bool(obj.bScopeCrosshair) and _right_crosshair_ready(pc, obj)
        except Exception:
            eligible = False

    if eligible:
        try:
            # During the short post-unpause settling window, keep the native cache
            # truthful (Visible) while still blocking BL2 from hiding the rebuilt
            # GFx clips. Once the new HUD generation is stable, steady-state goes
            # back to the Hidden-cache optimization.
            truthful = bool(_crosshair_resync_truthful_cache)
            _take_crosshair_ownership(obj, keep_cache_hidden=not truthful)
            obj.CrosshairVisibility = 1 if truthful else 0
            return Block
        except Exception as exc:
            global _crosshair_failure_logged
            _release_crosshair_visibility(include_offhand=False)
            _clear_crosshair_widget()
            if not _crosshair_failure_logged:
                logging.warning("omnimovement: sprint reticle event failed: " + repr(exc))
                _crosshair_failure_logged = True
            return

    if (_crosshair_eligible(pc, hud=hud) and not obj.bScopeCrosshair
            and _same(obj, hud.HUDMovie.CrosshairWidget)
            and not _right_crosshair_ready(pc, obj)):
        try:
            _hide_weapon_crosshair(obj)
            return Block
        except Exception:
            return

    # ADS/scope or loss of sprint legitimately returns visibility ownership to
    # native BL2. Reload/crouch/equip suppression is intentionally ignored while
    # sprint remains active and is repaired by transition events.
    owned = None if _crosshair_forced_widget is None else _crosshair_forced_widget()
    if owned is not None and _same(owned, obj):
        _release_crosshair_visibility(include_offhand=False)


def _crosshair_tick_fallback(obj, args, ret, func):
    """Compatibility fallback only when SetCrosshairVisibility is unavailable."""
    pc = _local_pc(obj)
    if pc is None or not pc.bInSprintState:
        return
    _ensure_crosshair_visible(pc, force_clips=True)































def _crosshair_widget_ready(pc, widget):
    """Return True only for the active HUD widget after weapon association settles."""
    if pc is None or widget is None:
        return False
    hud = pc.myHUD
    pawn = pc.Pawn
    weapon = None if pawn is None else pawn.Weapon
    if hud is None or hud.HUDMovie is None or weapon is None:
        return False
    try:
        if not _same(widget.MyHUDMovie, hud.HUDMovie):
            return False
        if widget.WWeap is None or not _same(widget.WWeap, weapon):
            return False
        if widget.OuterClip is None or widget.InnerClip is None:
            return False
    except Exception:
        return False
    return True


def _refresh_crosshair_native_presentation(pc, widget):
    """Rebuild the replacement widget's native weapon/frame presentation once.

    Pause recreates the HUD movie and crosshair widget while the same sprint may
    remain active. Visibility alone can leave the replacement widget with stale
    or uninitialized native crosshair presentation. Re-run the native association
    and frame/color/update path only during this bounded pause recovery.
    """
    if pc is None or widget is None or not _crosshair_widget_ready(pc, widget):
        return False
    pawn = pc.Pawn
    weapon = None if pawn is None else pawn.Weapon
    if weapon is None:
        return False
    try:
        with unrealsdk.hooks.prevent_hooking_direct_calls():
            widget.AssociateWeapon(InWeap=weapon)
            widget.UpdateCrosshairFrame()
            widget.UpdateCrosshairColor()
            widget.Update(0.0)
        return True
    except Exception as exc:
        logging.warning("omnimovement: pause crosshair native refresh failed: " + repr(exc))
        return False


def _reassert_crosshair_visible(pc, widget=None, *, finalize_cache=True, retry_interval=0.5):
    """Rare transition repair which re-runs BL2's native visibility setter.

    During pause restoration the visibility cache stays truthful for several HUD
    updates. Only the final successful repair reinstates Omni's hidden-cache trick.
    This lets native GFx/weapon restoration finish before steady-state ownership.
    """
    global _crosshair_failure_logged
    if not _crosshair_eligible(pc):
        return False
    if widget is None:
        widget = _get_crosshair_widget(pc, retry_interval=retry_interval)
    if widget is None or bool(widget.bScopeCrosshair) or bool(widget.bDualWieldCrosshair):
        return False
    if not _right_crosshair_ready(pc, widget):
        return False
    try:
        if _take_crosshair_ownership(widget, keep_cache_hidden=finalize_cache):
            return True
        with unrealsdk.hooks.prevent_hooking_direct_calls():
            widget.SetCrosshairVisibility(bVisible=True)
        widget.OuterClip.SetVisible(True)
        widget.InnerClip.SetVisible(True)
        if finalize_cache:
            # Re-enable the steady-state optimization only after the lifecycle repair
            # has survived several post-HUD updates with the real native cache visible.
            widget.CrosshairVisibility = 0
        return True
    except Exception as exc:
        _clear_crosshair_widget()
        if not _crosshair_failure_logged:
            logging.warning("omnimovement: sprint reticle resync failed: " + repr(exc))
            _crosshair_failure_logged = True
        return False

def _stop_crosshair_resync():
    global _crosshair_resync_ticks, _crosshair_resync_attempts
    global _crosshair_resync_truthful_cache, _crosshair_resync_force_tick
    global _crosshair_resync_native_widget_address, _crosshair_resync_missed_updates
    global _crosshair_resync_last_request
    _crosshair_resync_last_request = None
    _crosshair_resync_missed_updates = 0
    _crosshair_resync_ticks = 0
    _crosshair_resync_attempts = 0
    _crosshair_resync_truthful_cache = False
    _crosshair_resync_force_tick = False
    _crosshair_resync_native_widget_address = 0
    _set_hook(
        "crosshair-resync-update",
        False,
        CROSSHAIR_UPDATE,
        Type.POST_UNCONDITIONAL,
        _crosshair_resync_update,
    )
    _set_hook(
        "crosshair-resync-tick",
        False,
        PLAYER_TICK,
        Type.POST_UNCONDITIONAL,
        _crosshair_resync_tick,
    )


def _schedule_crosshair_resync(
    pc,
    ticks=4,
    *,
    defer_first=False,
    truthful_cache=False,
    force_tick=False,
    max_attempts=None,
    preserve_widget=False,
    widget=None,
):
    """Bounded repair after rare HUD/weapon lifecycle transitions.

    Normal transitions may still use a reflected HUD Update callback when it is
    genuinely useful. Pause/unpause explicitly uses PlayerTick because runtime
    tracing proved BL2 replaces HUDMovie after unpause while the reflected
    HUDWidget_Crosshairs.Update hook can register successfully without ever
    executing. The tick watcher exists only until the new HUD movie/widget is
    ready, performs a handful of successful repairs, then removes itself.
    """
    global _crosshair_resync_ticks, _crosshair_resync_attempts
    global _crosshair_resync_truthful_cache, _crosshair_resync_force_tick
    global _crosshair_resync_native_widget_address
    global _crosshair_widget, _crosshair_movie_address, _crosshair_retry_at
    global _crosshair_resync_last_request
    if pc is None or not (_active and _cfg_crosshair):
        return
    # A deferred pause repair may need to wait for sprint to begin after the
    # menu closes, when movement was first pressed while the game was paused.
    if not pc.bInSprintState and not (force_tick and defer_first):
        return
    _crosshair_resync_ticks = max(_crosshair_resync_ticks, int(ticks))
    attempts = max(60, int(ticks) * 30) if max_attempts is None else int(max_attempts)
    _crosshair_resync_attempts = max(_crosshair_resync_attempts, attempts)
    _crosshair_resync_truthful_cache = bool(
        _crosshair_resync_truthful_cache or truthful_cache
    )
    _crosshair_resync_force_tick = bool(_crosshair_resync_force_tick or force_tick)
    if force_tick:
        _crosshair_resync_native_widget_address = 0
    request = None
    # Ordinary swaps may reuse a live, already-associated widget. Menu/HUD
    # lifecycle callers retain the original unconditional invalidation default.
    if preserve_widget:
        if widget is None:
            widget = None if _crosshair_widget is None else _crosshair_widget()
        if (_crosshair_widget_ready(pc, widget)
                and not bool(widget.bScopeCrosshair)
                and not bool(widget.bDualWieldCrosshair)):
            _crosshair_widget = unrealsdk.unreal.WeakPointer(widget)
            _crosshair_movie_address = widget.MyHUDMovie._get_address()
            _crosshair_retry_at = 0.0
            if not (defer_first or _crosshair_resync_force_tick
                    or _crosshair_resync_truthful_cache):
                try:
                    request = (pc._get_address(), _crosshair_movie_address,
                               widget._get_address(), widget.WWeap._get_address(),
                               float(pc.WorldInfo.TimeSeconds))
                except Exception:
                    pass
        else:
            _clear_crosshair_widget()
    else:
        _clear_crosshair_widget()
    if (request is not None and request == _crosshair_resync_last_request
            and _crosshair_forced_widget is not None
            and ("crosshair-resync-update" in _hooks or "crosshair-resync-tick" in _hooks)
            and _same(_crosshair_forced_widget(), widget)):
        return
    _crosshair_resync_last_request = None
    if not defer_first and _reassert_crosshair_visible(
        pc, widget if request is not None else None,
        finalize_cache=not _crosshair_resync_truthful_cache,
        retry_interval=0.05 if _crosshair_resync_force_tick else 0.5,
    ):
        _crosshair_resync_last_request = request

    if _crosshair_resync_force_tick:
        _set_hook(
            "crosshair-resync-update",
            False,
            CROSSHAIR_UPDATE,
            Type.POST_UNCONDITIONAL,
            _crosshair_resync_update,
        )
        _set_hook(
            "crosshair-resync-tick",
            True,
            PLAYER_TICK,
            Type.POST_UNCONDITIONAL,
            _crosshair_resync_tick,
        )
        return

    _set_optional_hook(
        "crosshair-resync-update",
        True,
        CROSSHAIR_UPDATE,
        Type.POST_UNCONDITIONAL,
        _crosshair_resync_update,
    )
    # Registration alone does not prove that the live HUD invokes this hook.
    _set_hook("crosshair-resync-tick", True, PLAYER_TICK,
              Type.POST_UNCONDITIONAL, _crosshair_resync_tick)

def _crosshair_resync_update(obj, args, ret, func):
    """Temporary post-HUD repair used only after rare lifecycle transitions."""
    global _crosshair_resync_ticks, _crosshair_resync_attempts
    global _crosshair_resync_missed_updates
    pc = _pc()
    hud = None if pc is None else pc.myHUD
    if (
        pc is None
        or hud is None
        or not (_active and _cfg_crosshair and pc.bInSprintState)
        or _crosshair_resync_ticks <= 0
        or _crosshair_resync_attempts <= 0
    ):
        _stop_crosshair_resync()
        return
    if obj.bScopeCrosshair or obj.bDualWieldCrosshair or not _same(obj.MyHUDMovie, hud.HUDMovie):
        return

    _crosshair_resync_attempts -= 1
    truthful = _crosshair_resync_truthful_cache
    # Keep the cache truthful until the final successful post-HUD repair. This
    # prevents pause restoration from repeatedly rebuilding from a synthetic Hidden
    # cache while still allowing us to end in the low-overhead steady-state design.
    final = not truthful or _crosshair_resync_ticks == 1
    success = _reassert_crosshair_visible(
        pc,
        obj,
        finalize_cache=final,
    )
    if success:
        _crosshair_resync_missed_updates = 0
        _crosshair_resync_ticks -= 1
    if _crosshair_resync_ticks <= 0 or _crosshair_resync_attempts <= 0:
        _stop_crosshair_resync()


def _crosshair_resync_tick(obj, args, ret, func):
    global _crosshair_resync_ticks, _crosshair_resync_attempts
    global _crosshair_resync_native_widget_address, _crosshair_resync_missed_updates
    if not (_active and _cfg_crosshair):
        _stop_crosshair_resync()
        return
    pc = _local_pc(obj)
    if pc is None:
        return
    if _crosshair_resync_ticks <= 0 or _crosshair_resync_attempts <= 0:
        _stop_crosshair_resync()
        return

    # Do not abandon a pause repair merely because sprint state flickers during
    # HUD recreation. Keep the watcher bounded and wait for the persistent sprint
    # to be live again. If the user really stopped sprinting, the watcher naturally
    # expires or is removed by normal hook synchronization.
    if not pc.bInSprintState:
        _crosshair_resync_attempts -= 1
        return

    if "crosshair-resync-update" in _hooks:
        _crosshair_resync_missed_updates += 1
        if _crosshair_resync_missed_updates < 3:
            return
        _set_hook("crosshair-resync-update", False, CROSSHAIR_UPDATE,
                  Type.POST_UNCONDITIONAL, _crosshair_resync_update)
        if "crosshair-resync-update" in _hooks:
            # A failed removal must not leave two writers active.
            _crosshair_resync_attempts -= 1
            return

    widget = _get_crosshair_widget(pc, retry_interval=0.05)

    if widget is None or not _crosshair_widget_ready(pc, widget):
        _crosshair_resync_attempts -= 1
        if _crosshair_resync_attempts <= 0:
            _stop_crosshair_resync()
        return

    widget_address = widget._get_address()
    if widget_address != _crosshair_resync_native_widget_address:
        refreshed = _refresh_crosshair_native_presentation(pc, widget)
        if refreshed:
            _crosshair_resync_native_widget_address = widget_address
        # Native refresh can change association. Do not repair a retired widget.
        if not _crosshair_widget_ready(pc, widget):
            _crosshair_resync_attempts -= 1
            return

    _crosshair_resync_attempts -= 1
    truthful = _crosshair_resync_truthful_cache
    final = not truthful or _crosshair_resync_ticks == 1
    success = _reassert_crosshair_visible(
        pc,
        widget,
        finalize_cache=final,
        retry_interval=0.05,
    )
    if success:
        _crosshair_resync_ticks -= 1


    if _crosshair_resync_ticks <= 0 or _crosshair_resync_attempts <= 0:
        _stop_crosshair_resync()


def _pause_sprint_grace(pc):
    """Bridge native and Python input gates across a brief unpause input gap."""
    return bool(_pause_sprint_grace_until and pc.bInSprintState
                and time.perf_counter() <= _pause_sprint_grace_until)


def _crosshair_pause_state_post(obj, args, ret, func):
    """Track the real GFx pause-menu lifecycle and rebuild ownership afterward.

    Runtime tracing proved that closing the pause menu does not restore the same
    HUD movie. BL2 briefly sets HUDMovie=None, then installs a new movie and a new
    HUDWidget_Crosshairs generation. Any widget from before the pause is stale.
    """
    global _crosshair_modal_open, _gate_last_moving_at, _pause_sprint_grace_until
    global _travel_modal_open, _travel_close_pending, _travel_close_ready_ticks
    pc = _local_pc(obj)
    if pc is None or not _active:
        return
    try:
        is_open = bool(args.bIsOpen)
    except Exception:
        return

    if _travel_pending and _cfg_assist:
        # Fast-travel stations sometimes finish the destination's GFx pause
        # teardown *after* NotifyLoadedWorld and even after the pawn can move.
        # An early bTryToSprint is then consumed/reset by that input rebuild.
        # Record the real menu edge and rearm from a later bounded PlayerTick,
        # not from within this UI callback.
        _travel_budget.sample(time.monotonic(), False)
        _travel_modal_open = is_open
        _travel_close_pending = not is_open
        _reset_travel_readiness()

    # Center Gap uses a real-time timestamp. Time spent in the pause menu would
    # otherwise age that timestamp out, so one transient zero-input controller
    # frame immediately after unpause can end sprint and cause a visible hitch.
    # Rebase the existing grace window at the real pause-close boundary. This is
    # inert when real movement is already present (including with Deadzone Control).
    if not is_open:
        _pause_sprint_grace_until = 0.0
        if _cfg_omni and _cfg_gap > 0.0 and pc.bInSprintState:
            _gate_last_moving_at = time.perf_counter()
            _pause_sprint_grace_until = _gate_last_moving_at + _cfg_gap

    if not _cfg_crosshair:
        return

    if is_open:
        _crosshair_modal_open = True
        _stop_crosshair_resync()
        _stop_offhand_crosshair_retry()
        _release_crosshair_visibility()
        _clear_crosshair_widget()
        return

    _crosshair_modal_open = False
    _stop_crosshair_resync()
    _release_crosshair_visibility()
    _clear_crosshair_widget()
    if pc.bInSprintState or (_cfg_assist and _armed):
        # The replacement HUD movie appears after this callback. A remembered
        # sprint can also begin after this callback from input held in the menu.
        # Poll only during this bounded transition, wait for a widget whose MyHUDMovie exactly
        # matches the new pc.myHUD.HUDMovie and whose WWeap matches Pawn.Weapon,
        # then hold a truthful Visible cache for a few ticks before restoring the
        # low-overhead Hidden-cache steady state.
        _schedule_crosshair_resync(
            pc,
            ticks=8,
            defer_first=True,
            truthful_cache=True,
            force_tick=True,
            max_attempts=720,
        )
        _schedule_offhand_crosshair(pc)


def _crosshair_crouch_transition(obj, args, ret, func):
    """Crouch can directly change reticle clips without ending sprint."""
    pc = _pc()
    if pc is None or not (_active and _cfg_crosshair and pc.bInSprintState):
        return
    if str(obj.Class.Name).endswith("PlayerPawn") and not _is_local_pawn(obj):
        return
    _schedule_crosshair_resync(pc, ticks=12)


def _crosshair_associate_post(obj, args, ret, func):
    """Reacquire a crosshair widget after native HUD/weapon reassociation."""
    global _offhand_associated_widget
    pc = _pc()
    if pc is None or not (_active and _cfg_crosshair):
        return
    hud = pc.myHUD
    if hud is None or not _same(obj.MyHUDMovie, hud.HUDMovie):
        return
    if obj.bDualWieldCrosshair:
        if _offhand_crosshair_matches(pc, obj, require_clips=False):
            _schedule_offhand_crosshair(pc)
            _offhand_associated_widget = unrealsdk.unreal.WeakPointer(obj)
        return
    if pc.bInSprintState:
        _schedule_crosshair_resync(pc, preserve_widget=True, widget=obj)


def _crosshair_resync_pawn(obj, args, ret, func):
    """Repair reticle ownership on jump/fall/landing boundaries without polling."""
    if not (_active and _cfg_crosshair and _is_local_pawn(obj)):
        return
    pc = _pc()
    if pc is None or not pc.bInSprintState:
        return
    _ensure_crosshair_visible(pc, force_clips=True)

def _sync_crosshair_hooks(pc=None):
    """Use native visibility events; never permanently hook HUDWidget_Crosshairs.Update."""
    global _crosshair_strategy, _crosshair_modal_open
    if pc is None:
        pc = _pc()

    sprinting = bool(
        _active and _cfg_crosshair and pc is not None and pc.bInSprintState
    )
    modal_hooks = bool(
        _active
        and pc is not None
        and (_cfg_crosshair or (_cfg_omni and _cfg_gap > 0.0) or _cfg_assist)
    )
    if not _cfg_crosshair:
        _crosshair_modal_open = False

    # These are rare transition events, not frame callbacks. Native/GFx can hide
    # the clips during pause and airborne transitions without ending sprint, so
    # explicitly reacquire the reticle at those boundaries.
    _set_optional_hook(
        "crosshair-pause-state", modal_hooks, CROSSHAIR_PAUSE_STATE, Type.POST_UNCONDITIONAL, _crosshair_pause_state_post
    )
    _set_optional_hook(
        "crosshair-crouch",
        sprinting,
        CROSSHAIR_CROUCH,
        Type.POST_UNCONDITIONAL,
        _crosshair_crouch_transition,
    )
    _set_optional_hook(
        "crosshair-uncrouch",
        sprinting,
        CROSSHAIR_UNCROUCH,
        Type.POST_UNCONDITIONAL,
        _crosshair_crouch_transition,
    )
    _set_optional_hook(
        "crosshair-associate",
        sprinting,
        CROSSHAIR_ASSOCIATE,
        Type.POST_UNCONDITIONAL,
        _crosshair_associate_post,
    )
    _set_optional_hook(
        "crosshair-falling",
        sprinting,
        CROSSHAIR_FALLING,
        Type.POST_UNCONDITIONAL,
        _crosshair_resync_pawn,
    )
    _set_optional_hook(
        "crosshair-landed",
        sprinting,
        CROSSHAIR_LANDED,
        Type.POST_UNCONDITIONAL,
        _crosshair_resync_pawn,
    )

    if not sprinting:
        # Pause/unpause can transiently clear bInSprintState for a single input
        # epoch while the replacement HUD movie is still being rebuilt. Keep an
        # already-armed bounded PlayerTick pause repair alive through that
        # transient instead of destroying it before its first useful tick.
        preserve_pause_repair = bool(
            _crosshair_resync_force_tick
            and _crosshair_resync_ticks > 0
            and _crosshair_resync_attempts > 0
        )
        if not preserve_pause_repair:
            _stop_crosshair_resync()
        _set_hook(
            "crosshair-vis",
            False,
            CROSSHAIR_VISIBILITY,
            Type.PRE,
            _crosshair_visibility_pre,
        )
        _set_hook(
            "crosshair-tick",
            False,
            PLAYER_TICK,
            Type.POST_UNCONDITIONAL,
            _crosshair_tick_fallback,
        )
        _release_crosshair_visibility()
        return

    # Preferred fast path: hook only the native visibility setter and force the
    # clips once when sprint starts. Native HUD Update still updates frame/color;
    # Python no longer runs once per HUD Update.
    if _crosshair_visibility_available and _set_optional_hook(
        "crosshair-vis",
        True,
        CROSSHAIR_VISIBILITY,
        Type.PRE,
        _crosshair_visibility_pre,
    ):
        _crosshair_strategy = "visibility-event"
        _set_hook(
            "crosshair-tick",
            False,
            PLAYER_TICK,
            Type.POST_UNCONDITIONAL,
            _crosshair_tick_fallback,
        )
        if not _ensure_crosshair_visible(pc) and _crosshair_resync_ticks <= 0:
            _schedule_crosshair_resync(pc, defer_first=True, max_attempts=240)
        _schedule_offhand_crosshair(pc)
        return

    # Extremely conservative compatibility fallback for an SDK/game build where
    # the reflected setter is absent. This preserves old behavior, but the user's
    # BL2 build exposes SetCrosshairVisibility and should never take this path.
    _crosshair_strategy = "tick-fallback"
    _set_hook(
        "crosshair-vis",
        False,
        CROSSHAIR_VISIBILITY,
        Type.PRE,
        _crosshair_visibility_pre,
    )
    _set_hook(
        "crosshair-tick",
        True,
        PLAYER_TICK,
        Type.POST_UNCONDITIONAL,
        _crosshair_tick_fallback,
    )


def _begin_sprint(obj, args, ret, func):
    global _sprint_depth
    if not _active or _local_pc(obj) is None:
        return
    if _cfg_baseline and BASELINE_AVAILABLE:
        if not obj.bInSprintState:
            _suppressed.clear()
        _sprint_depth += 1
        _set_hook(
            "accuracy-scope",
            True,
            ATTRIBUTE_MODIFIER,
            Type.PRE,
            _accuracy_modifier,
        )


def _began_sprint(obj, args, ret, func):
    global _sprint_depth, _gate_sprinting, _assist_airborne_hold
    pc = _local_pc(obj)
    if pc is None:
        return
    _gate_sprinting = True
    _assist_airborne_hold = False
    _set_hook(
        "assist-airborne-gate",
        False,
        INPUT_ALLOWS_SPRINT,
        Type.PRE,
        _assist_airborne_gate,
    )
    _sprint_depth = max(0, _sprint_depth - 1)
    if _sprint_depth == 0:
        _set_hook(
            "accuracy-scope",
            False,
            ATTRIBUTE_MODIFIER,
            Type.PRE,
            _accuracy_modifier,
        )
    if _cfg_assist and pc.PlayerInput is not None:
        # Pending intent is only needed while waiting to (re)enter sprint.
        _stop_assist_recovery()
        _set_try_sprint(pc.PlayerInput, False)
    _sync_sprint_hooks(pc)
    if _active_fire_entry and _cfg_hip_fire:
        _sprint_fire_entry(pc)


def _stop_sprint_fire_entry_probe():
    global _sprint_fire_entry_remaining
    _sprint_fire_entry_remaining = 0
    _set_hook("sprint-fire-entry", False, PLAYER_TICK,
              Type.POST_UNCONDITIONAL, _sprint_fire_entry_tick)


def _sprint_fire_entry_tick(obj, args, ret, func):
    """Bounded sprint-entry pose probe. No steady-state animation polling."""
    global _sprint_fire_entry_remaining
    pc = _local_pc(obj)
    if pc is None:
        return
    if not (
        _active and _cfg_hip_fire and pc.bInSprintState and _pose_fire_requested()
        and _sprint_fire_entry_remaining > 0
    ):
        _stop_sprint_fire_entry_probe()
        return
    _sprint_fire_entry_remaining -= 1
    _update_fire_pose(pc, firing=True, now=float(pc.WorldInfo.TimeSeconds))
    if _fire_pose.aliases_active:
        _stop_sprint_fire_entry_probe()
    elif _sprint_fire_entry_remaining <= 0:
        _stop_sprint_fire_entry_probe()


def _sprint_fire_entry(pc, *, after_equip=False):
    """Event-only transition coordination; retain native BL2 arms animation."""
    global _sprint_fire_entry_remaining
    if not (
        _active and (_active_fire_entry or after_equip)
        and _cfg_hip_fire and (_pose_fire_requested() or _weapons_still_firing(pc))
    ):
        return
    _clear_direction()
    now = float(pc.WorldInfo.TimeSeconds)
    _sync_fire_hooks(True)
    _update_fire_pose(pc, firing=True, now=now)
    if not _fire_pose.aliases_active and _pose_fire_requested():
        _sprint_fire_entry_remaining = 6
        _set_hook("sprint-fire-entry", True, PLAYER_TICK,
                  Type.POST_UNCONDITIONAL, _sprint_fire_entry_tick)


def _accuracy_modifier(obj, args, ret, func):
    if (
        not _active
        or not BASELINE_AVAILABLE
        or not _cfg_baseline
        or _restoring
        or not _sprint_depth
    ):
        return

    pc = _pc()
    if pc is None:
        return

    name = str(obj.Name)
    if (
        name not in _accuracy_rules
        or str(obj._path_name()) != "D_Attributes.AccuracyResourcePool." + name
        or not _same(args.Context, pc.AccuracyPool.Data)
        or int(args.Modifier.Type) != 2
        or abs(float(args.Modifier.Value) - _accuracy_rules[name]) > 0.001
    ):
        return

    if any(all(_same(ref(), value) for ref, value in zip(refs,
               (pc, obj, args.Context, args.Modifier))) for refs in _suppressed):
        return Block, True
    _suppressed.append(
        tuple(
            unrealsdk.unreal.WeakPointer(x)
            for x in (pc, obj, args.Context, args.Modifier)
        )
    )
    return Block, True


def _restore_baseline():
    global _restoring
    if not _suppressed:
        return
    _restoring = True
    try:
        for refs in _suppressed:
            owner, attr, context, modifier = (r() for r in refs)
            if owner is None or attr is None or context is None or modifier is None:
                continue
            if owner.bInSprintState and any(
                _same(entry.Modifier, modifier)
                and _same(entry.Context, context)
                and _same(entry.Attribute, attr)
                for entry in owner.SprintModifiers
            ):
                with unrealsdk.hooks.prevent_hooking_direct_calls():
                    attr.AddAttributeModifier(
                        Context=context,
                        Modifier=modifier,
                        bSuppressNotify=False,
                    )
    finally:
        _restoring = False
        _suppressed.clear()


def _input_sprint_exit_skid(pc, pawn, weapon, now):
    """Recognize ADS or an input stop without changing native movement state."""
    if not (_active and _cfg_omni and _cfg_assist and _armed and pawn is not None):
        return False
    try:
        if (not _basic_context(pc, pawn=pawn, fire_pose_only=True)
                or int(pawn.Physics) != 1 or pawn.DrivenVehicle is not None
                or pc.bDuck or pc.bPressedJump or pawn.bIsCrouched
                or pawn.bWantsToCrouch or pawn.bForceRMVelocity
                or pawn.bForceRegularVelocity or pawn.bRMForceStop
                or _knockback_until > now or _interrupted(pc)):
            return False
        pi = pc.PlayerInput
        if pi is None:
            return False
        fwd, side = float(pi.aBaseY), float(pi.aStrafe)
        if not (math.isfinite(fwd) and math.isfinite(side)):
            return False
        edge = _ads_sprint_edge
        aiming = bool(_fast_zoomed(pc, weapon) or (
            edge is not None and edge[0] == pc._get_address() and now <= edge[1]))
        return aiming or fwd * fwd + side * side <= 0.0001
    except Exception:
        return False


def _ended_sprint(obj, args, ret, func):
    global _gate_sprinting
    _precision.restore()
    pc = _local_pc(obj)
    if pc is None:
        return

    _gate_sprinting = False
    if _sprint_fire_entry_remaining:
        _stop_sprint_fire_entry_probe()
    _clear_center_transition()
    pawn = pc.Pawn
    weapon = None if pawn is None else pawn.Weapon
    transitioning = _has_weapon_transition(pc)
    if not transitioning and not _weapon_state_events_complete and weapon is not None:
        try:
            transitioning = bool(weapon.bWeaponPutDown or weapon.IsEquipping())
        except Exception:
            transitioning = bool(weapon.bWeaponPutDown)

    # Ordinary and overridden melee can request an EndSprint before the
    # action flag is set (or after it has just been cleared). Include the short
    # local melee-dispatch edge instead of relying on a character-specific flag.
    recent_nonforward_melee = (
        _melee_nonforward_controller == pc._get_address()
        and 0 <= time.perf_counter() - _melee_nonforward_event_at <= 0.35
        and _moving_nonforward_on_foot(pc)
    )
    # Starting Decepti0n uses the same native EndSprint camera skid as a
    # manual stop. The game continues to own actual velocity and skill cast,
    # but this action-triggered lurch can look like a second movement stall.
    # Only cancel the *current* native lurch during the short, local cast edge.
    global _zero_deception_cast_edge
    cast_edge = _zero_deception_cast_edge
    if cast_edge is not None:
        _zero_deception_cast_edge = None
        if (cast_edge[0] == pc._get_address()
                and time.perf_counter() <= cast_edge[1]
                and _active and _cfg_omni and pawn is not None
                and int(pawn.Physics) == 1):
            _action_skid.cancel(pc)
            _schedule_action_skid_tick()

    if (
        _active
        and _cfg_omni
        and pawn is not None
        and _basic_context(pc)
        and int(pawn.Physics) == 1
        and (pc.bThrowingGrenade or transitioning or _deathtrap_skill_active
             or recent_nonforward_melee or (pc.bPerformingMeleeAttack and _moving_nonforward_on_foot(pc)))
    ):
        # Deathtrap/Digi-Jack activation is now an intentional one-time sprint
        # drop to walking. Expire that single skid so backward/side movement is
        # never pinned while the skill remains active.
        _action_skid.cancel(pc)
        _schedule_action_skid_tick()

    # Native EndSprint creates a camera skid even when the player only enters
    # ADS or briefly releases movement. Expire that lurch, not velocity or the
    # game's sprint/zoom decision. Other action and movement locks win above.
    if _input_sprint_exit_skid(pc, pawn, weapon, time.perf_counter()):
        try:
            _action_skid.cancel(pc)
            _schedule_action_skid_tick()
        except Exception:
            pass

    _suppressed.clear()
    _clear_direction()
    _fire_pose.restore()
    _sync_fire_hooks(False)
    if _active and _cfg_assist and _armed and pc.PlayerInput is not None:
        # If sprint drops while airborne, do not let BL2 consume bTryToSprint
        # into its separate bWantsToSprint one-shot. That native queue can be
        # discarded when movement reaches zero before landing, leaving remembered
        # Sprint armed internally but requiring a double toggle to recover.
        # Hold Omni's pending intent until the pawn is grounded instead.
        if not _hold_assist_through_airborne(pc):
            # Grounded/action interruptions keep the established recovery path.
            _assist_recover_or_schedule(pc)
    _sync_sprint_hooks(pc)


def _weapon_accuracy(obj, args, ret, func):
    global _gate_sprinting
    pc = _local_pc(obj)
    if not (_active and pc is not None):
        return

    if pc.bInSprintState and _cfg_omni and _cfg_assist and _armed:
        try:
            pawn = pc.Pawn
            physics = None if pawn is None else int(pawn.Physics)
            if physics in (1, 2):
                owner = _equip_sprint_owner(pc, args.TheWeapon)
                pending = None if owner is None else _equip_sprint_handoffs.get(owner[0])
                now = time.perf_counter()
                if (pending is not None and pending["owner"] == owner
                        and pending["phase"] == "bringup" and not pending["activation"]
                        and now < pending["deadline"]
                        and (physics == 2
                             or (_drop_sprint_scope is not None and now < _drop_sprint_scope[1]
                                 and owner[1:4] == _drop_sprint_scope[0][1:4])
                             or _skill_swap_keeps_sprint(pc, pawn, args.TheWeapon))
                        and _assist_context(pc, pawn=pawn)
                        and (not _suppressed or (
                            (pool := pc.AccuracyPool.Data) is not None
                            and all(_same(refs[0](), pc) and _same(refs[2](), pool)
                                    for refs in _suppressed)))):
                    # The stock accuracy initializer refills the controller's
                    # pool. During drop, BL2 can re-equip the outgoing weapon
                    # before equipping the replacement or picking it back up.
                    # Each bring-up still uses the same suppression context.
                    # During a live skill a reset can also create a sprint skid.
                    # Let it run without restarting sprint and resetting FOV.
                    _clear_direction()
                    return
        except Exception:
            pass  # Unverified equipment or pool ownership keeps the old reset.

    _restore_baseline()
    _clear_direction()
    if pc.bInSprintState:
        _gate_sprinting = False
        with unrealsdk.hooks.prevent_hooking_direct_calls():
            pc.EndSprint()
        if (
            _cfg_omni
            and pc.Pawn is not None
            and _basic_context(pc)
            and int(pc.Pawn.Physics) == 1
        ):
            _action_skid.cancel(pc)
        _schedule_action_skid_tick()
        _sync_sprint_hooks(pc)


def _skill_swap_keeps_sprint(pc, pawn, incoming):
    """An equipped weapon can initialize during a live on-foot skill."""
    # This predicate is reached only during an exact already-sprinting equip.
    # Let a guarded summon use the same preservation path, without changing
    # the summon guard's sprint-entry or recovery policies.
    if not _skill_assist_context(pc, pawn=pawn, allow_guarded_swap=True):
        return False
    reload_active = _pose_reload_active(pawn)
    other_reloading = False
    if reload_active and pawn.OffHandWeapon is not None:
        other = pawn.Weapon if _same(incoming, pawn.OffHandWeapon) else pawn.OffHandWeapon
        def reloading(weapon):
            return (weapon is not None and (weapon._get_address() in _pose_reload_addresses
                    or str(weapon.GetStateName()).lower() == "weaponreloading"))
        other_reloading = reloading(other) and not reloading(incoming)
    return bool(
        (not reload_active or other_reloading)
        and _combat_sprint_safe(pc, pawn, other_weapon_reloading=other_reloading)
        and not _crosshair_modal_open and not _travel_modal_open
        and pc.WorldInfo.Pauser is None and pc.PlayerReplicationInfo is not None
        and not pc.PlayerReplicationInfo.bGFxMenuOpen
        and not (pc.bThrowingGrenade or pc.bPerformingMeleeAttack
                 or pc.bPerformingSharedWeaponAction)
        and not _zero_execute_waiting())


def _movement_world_direction(pc, fwd, side, length):
    if length <= 0.01:
        return None

    yaw = float(pc.Rotation.Yaw) * math.tau / 65536.0
    sine = math.sin(yaw)
    cosine = math.cos(yaw)
    return (
        (fwd * cosine - side * sine) / length,
        (fwd * sine + side * cosine) / length,
    )


def _python_center_event(fwd, side, moving, now, enabled):
    """Reference center observer for Python mode, independent of old EndSprint hook."""
    global _center_input_previous, _center_input_started
    if not enabled:
        _center_input_previous = None
        _center_input_started = None
        return False, False
    if not moving:
        if _center_input_started is None and _center_input_previous is not None:
            _center_input_started = now
            return True, False
        if (_center_input_started is not None and
                (now < _center_input_started or now - _center_input_started > _cfg_gap)):
            _center_input_started = None
            _center_input_previous = None
        return False, False
    crossed = False
    if (_center_input_started is not None and _center_input_previous is not None
            and _center_input_started <= now <= _center_input_started + _cfg_gap):
        old_fwd, old_side = _center_input_previous
        magnitude_sq = fwd * fwd + side * side
        old_sq = old_fwd * old_fwd + old_side * old_side
        dot = fwd * old_fwd + side * old_side
        crossed = dot < 0.0 and dot * dot > 0.01 * magnitude_sq * old_sq
    _center_input_started = None
    _center_input_previous = (fwd, side)
    return False, bool(crossed)


def _remember_center_origin():
    """Read a single sprint velocity snapshot only when the stick first centers."""
    global _center_origin_velocity
    _center_origin_velocity = None
    pc = _pc()
    if pc is None or not pc.bInSprintState:
        return
    pawn = pc.Pawn
    if pawn is None:
        return
    try:
        if int(pawn.Physics) != 1:
            return
        velocity = pawn.Velocity
        _center_origin_velocity = (float(velocity.X), float(velocity.Y))
    except Exception:
        _center_origin_velocity = None


def _apply_center_cross(fwd, side, magnitude_sq, now):
    """Use the existing guarded event reversal, not the disabled EndSprint hook."""
    global _center_origin_velocity
    try:
        if _center_origin_velocity is None or not _gate_sprinting:
            return
        pc = _pc()
        if pc is None or not pc.bInSprintState:
            return
        if _begin_event_reversal(pc, fwd, side, magnitude_sq, now):
            # Responsive blend only: one short PlayerTick hook, removed on finish.
            _set_hook("center-transition", True, PLAYER_TICK,
                      Type.POST_UNCONDITIONAL, _center_transition_tick)
    finally:
        _center_origin_velocity = None


def _force_end_sprint(pc):
    pc.EndSprint()


def _write_reversal_velocity(pawn, vx, vy):
    """Single guarded output boundary shared by both correction paths.

    Native mode attempts an owner-validated C++ write. A rejected or uncertain
    native result forbids same-frame Python fallback; only a native None
    response permits it. Python mode writes directly.
    """
    pc = _pc()
    pi = None if pc is None else pc.PlayerInput
    # All callers also check weapon/skill/knockback state before calculating a
    # correction. Revalidate this shared output boundary for either backend.
    if (pc is None or pi is None or not _same(pc.Pawn, pawn)
            or not _same(pawn.Controller, pc)
            or not pc.bInSprintState or not _basic_context(pc, pi, pawn)
            or int(pawn.Physics) != 1 or not math.isfinite(vx) or not math.isfinite(vy)):
        return False
    if _cfg_native_precision:
        committed = _precision.velocity_write(pc, pi, pawn, _travel_epoch, vx, vy)
        if committed is not None:
            return committed
    pawn.Velocity.X = vx
    pawn.Velocity.Y = vy
    return True


def _begin_event_reversal(pc, fwd, side, magnitude_sq, now):
    """Start Responsive/Near-instant correction only after a real center crossing."""
    global _center_transition_origin, _center_transition_target
    global _center_transition_speed, _center_transition_started

    if _cfg_direction == VANILLA or _center_origin_velocity is None:
        return False
    pawn = pc.Pawn
    if pawn is None or not magnitude_sq > 0.0001:
        return False
    if _fire_requested or (
        _fire_until > 0.0 and float(pc.WorldInfo.TimeSeconds) < _fire_until
    ):
        return False
    if _knockback_until > 0.0 and now < _knockback_until:
        return False
    # Match the established event-reversal safety checks. A center crossing
    # must not force velocity during a skill, reload, jump, or weapon transition.
    skill = pawn.MyActionSkill
    if skill is not None and str(skill.Class.Name) != "DeathtrapActionSkill":
        return False
    if _interrupted(pc) or _fast_zoomed(pc, pawn.Weapon):
        return False
    if (int(pawn.Physics) != 1 or pawn.bIsCrouched or pawn.bWantsToCrouch
            or pawn.bReloading or pc.bDuck or pc.bPressedJump or pc.bPendingZoom
            or pawn.bForceRMVelocity or pawn.bForceRegularVelocity or pawn.bRMForceStop):
        return False

    length = math.sqrt(magnitude_sq)
    direction = _movement_world_direction(pc, fwd, side, length)
    if direction is None:
        return False
    ox, oy = _center_origin_velocity
    try:
        cap = float(pawn.GroundSpeed) * min(1.0, float(pawn.MaxDesiredSpeed))
    except Exception:
        return False
    # Native center-crossing math is optional. Python keeps the eligibility
    # checks and math fallback; the shared guarded output boundary chooses
    # the native writer or a permitted Python fallback.
    native_begin = (
        _precision.center_begin(direction, (ox, oy), cap)
        if _cfg_native_precision else None
    )
    if native_begin is None:
        speed = math.hypot(ox, oy)
        if speed <= 1.0 or direction[0] * ox + direction[1] * oy >= -speed * 0.1:
            return False
        if cap <= 0.0 or speed > cap * 1.01:
            return False
        speed = min(speed, cap)
        instant_vx, instant_vy = direction[0] * speed, direction[1] * speed
    else:
        _handled, values = native_begin
        if values is None:
            return False
        speed, instant_vx, instant_vy = values

    if _cfg_direction == INSTANT:
        _write_reversal_velocity(pawn, instant_vx, instant_vy)
        return False

    _center_transition_origin = (ox, oy)
    _center_transition_target = direction
    _center_transition_speed = speed
    _center_transition_started = now
    return True


def _update_event_reversal(pc, now, fwd, side, magnitude_sq):
    global _center_transition_origin, _center_transition_target
    global _center_transition_speed, _center_transition_started
    origin = _center_transition_origin
    target = _center_transition_target
    if origin is None or target is None:
        return False
    pawn = pc.Pawn
    if pawn is None or not magnitude_sq > 0.0001:
        _center_transition_origin = None
        _center_transition_target = None
        return False

    length = math.sqrt(magnitude_sq)
    direction = _movement_world_direction(pc, fwd, side, length)
    if direction is None or direction[0] * target[0] + direction[1] * target[1] < 0.8:
        _center_transition_origin = None
        _center_transition_target = None
        return False

    if _knockback_until > 0.0 and now < _knockback_until:
        _center_transition_origin = None
        _center_transition_target = None
        return False
    if _fire_requested or (
        _fire_until > 0.0 and float(pc.WorldInfo.TimeSeconds) < _fire_until
    ):
        _center_transition_origin = None
        _center_transition_target = None
        return False
    if _interrupted(pc) or _fast_zoomed(pc, pawn.Weapon):
        _center_transition_origin = None
        _center_transition_target = None
        return False
    if (
        int(pawn.Physics) != 1
        or pawn.bIsCrouched
        or pawn.bWantsToCrouch
        or pawn.bReloading
        or pc.bDuck
        or pc.bPressedJump
        or pc.bPendingZoom
        or pawn.bForceRMVelocity
        or pawn.bForceRegularVelocity
        or pawn.bRMForceStop
    ):
        _center_transition_origin = None
        _center_transition_target = None
        return False

    try:
        cap = float(pawn.GroundSpeed) * min(1.0, float(pawn.MaxDesiredSpeed))
    except Exception:
        _center_transition_origin = None
        _center_transition_target = None
        return False
    if cap <= 0.0:
        _center_transition_origin = None
        _center_transition_target = None
        return False

    elapsed = max(0.0, now - _center_transition_started)
    native_blend = (
        _precision.center_blend(origin, target, _center_transition_speed,
                                cap, elapsed)
        if _cfg_native_precision else None
    )
    if native_blend is None:
        blend = min(1.0, elapsed / 0.120)
        speed = min(_center_transition_speed, cap)
        ox, oy = origin
        tx, ty = target
        inv = 1.0 - blend
        rx = inv * ox + blend * tx * speed
        ry = inv * oy + blend * ty * speed
        magnitude = math.hypot(rx, ry)
        if magnitude > cap and magnitude > 0.0:
            scale = cap / magnitude
            rx *= scale
            ry *= scale
        finished = blend >= 1.0
    else:
        _handled, values = native_blend
        if values is None:
            _center_transition_origin = None
            _center_transition_target = None
            return False
        rx, ry, finished = values
    _write_reversal_velocity(pawn, rx, ry)

    if finished:
        _center_transition_origin = None
        _center_transition_target = None
        return False
    return True


def _center_transition_tick(obj, args, ret, func):
    """Temporary tick during the 120 ms center-crossing reversal blend."""
    pc = _local_pc(obj)
    if pc is None or not _active or _deathtrap_skill_active or not pc.bInSprintState:
        _clear_center_transition(remove_hook=False)
        _set_hook("center-transition", False, PLAYER_TICK, Type.POST_UNCONDITIONAL, _center_transition_tick)
        return
    pawn = pc.Pawn
    pi = pc.PlayerInput
    if pawn is None or pi is None:
        _clear_center_transition(remove_hook=False)
        _set_hook("center-transition", False, PLAYER_TICK, Type.POST_UNCONDITIONAL, _center_transition_tick)
        return

    now = time.perf_counter()
    try:
        fwd = float(pi.aBaseY)
        side = float(pi.aStrafe)
    except Exception:
        fwd = side = 0.0
    magnitude_sq = fwd * fwd + side * side
    if _center_transition_origin is not None:
        if not _update_event_reversal(pc, now, fwd, side, magnitude_sq):
            _set_hook("center-transition", False, PLAYER_TICK, Type.POST_UNCONDITIONAL, _center_transition_tick)


def _observe_reversal(fwd, side, magnitude_sq, preobserved=None):
    """Observe reversal candidates natively or through the Python fallback.

    Python retains gameplay guards and decides when to request a velocity
    correction. The selected guarded output backend performs the write.
    The native observer never retains game-object addresses.
    """
    global _native_reversal_was_active
    if preobserved is not None or (_cfg_native_precision and _cfg_direction != VANILLA):
        observation = (preobserved if preobserved is not None else
                       _precision.reversal_observe(
                           fwd, side, magnitude_sq,
                           _reversal.origin is not None))
        if observation is not None:
            _native_reversal_was_active = True
            needed, candidate, released = observation
            _reversal.candidate = bool(candidate)
            if released:
                # Mirror Reversal.observe_raw's >50% strength-drop release.
                _reversal.origin = None
                _reversal.target = None
                _reversal.candidate = False
            return bool(needed)
    if _native_reversal_was_active:
        # A one-time native failure cannot leave an old Python correction
        # active or stale input history in charge of the next frame.
        _reset_reversal_state()
    return _reversal.observe_raw(fwd, side, magnitude_sq, _cfg_direction)


def _reversal_from_gate(fwd, side, magnitude_sq, now, preobserved=None):
    """Observe raw movement cheaply; cross into Unreal only on a real reversal."""
    global _reversal_last_at, _gate_sprinting

    if _cfg_direction == VANILLA or not _gate_sprinting:
        _reset_reversal_state()
        _reversal_last_at = now
        return

    previous_at = _reversal_last_at
    _reversal_last_at = now
    if previous_at is None:
        _observe_reversal(fwd, side, magnitude_sq, preobserved)
        return

    dt = now - previous_at
    if dt <= 0.0 or dt > 0.1:
        _reset_reversal_state()
        _observe_reversal(fwd, side, magnitude_sq)
        return

    # The steady-state path ends here with no PlayerController/Pawn property
    # reads. Only a true opposing transition enters Unreal state.
    if not _observe_reversal(fwd, side, magnitude_sq, preobserved):
        return

    pc = _pc()
    if pc is None or not pc.bInSprintState:
        _gate_sprinting = False
        _reset_reversal_state()
        return

    pawn = pc.Pawn
    if pawn is None:
        _reset_reversal_state()
        return

    # The verified Doppelganger compatibility path is special: its Digi-Jacks
    # use BL2's DeathtrapActionSkill and switch input context to DeathTrap for
    # the whole skill. Older working OmniMovement builds kept reversal handling
    # active in this context. Suppress correction for other action skills, but
    # preserve it for DeathtrapActionSkill instead of treating the entire skill
    # as an interruption.
    skill = pawn.MyActionSkill
    if skill is not None and str(skill.Class.Name) != "DeathtrapActionSkill":
        _reset_reversal_state()
        return

    if _knockback_until > 0.0 and now < _knockback_until:
        _reset_reversal_state()
        return

    weapon = pawn.Weapon
    if _fire_requested or (
        _fire_until > 0.0 and float(pc.WorldInfo.TimeSeconds) < _fire_until
    ):
        _reset_reversal_state()
        return
    if _interrupted(pc) or _fast_zoomed(pc, weapon):
        _reset_reversal_state()
        return
    if (
        int(pawn.Physics) != 1
        or pawn.bIsCrouched
        or pawn.bWantsToCrouch
        or pawn.bReloading
        or pc.bDuck
        or pc.bPressedJump
        or pc.bPendingZoom
        or pawn.bForceRMVelocity
        or pawn.bForceRegularVelocity
        or pawn.bRMForceStop
    ):
        _reset_reversal_state()
        return

    length = math.sqrt(magnitude_sq)
    unit = _movement_world_direction(pc, fwd, side, length)
    if unit is None:
        _reset_reversal_state()
        return

    velocity = pawn.Velocity
    vx = float(velocity.X)
    vy = float(velocity.Y)
    cap = float(pawn.GroundSpeed) * min(1.0, float(pawn.MaxDesiredSpeed))
    if _cfg_native_precision and _native_reversal_was_active:
        # Native mode calculates the reversal blend after Python's gameplay
        # checks. The shared output boundary selects a guarded native write
        # or permitted Python fallback. Failed blend evaluation cannot reuse
        # stale velocity values.
        native_result = _precision.reversal_apply(
            unit, (vx, vy), cap, dt,
            _cfg_direction == INSTANT, _reversal.candidate,
        )
        if native_result is None:
            _reset_reversal_state()
            return
        _handled, correction = native_result
    else:
        correction = _reversal.apply(
            unit,
            (vx, vy),
            cap,
            dt,
            _cfg_direction,
        )
    if correction is not None:
        _write_reversal_velocity(pawn, *correction)


def _allows(obj, args, ret, func):
    """Python-safe sprint gate; optional controller precision runs at this existing boundary."""
    global _gate_last_moving_at, _reversal_last_at, _assist_airborne_hold
    global _center_origin_velocity
    global _native_reversal_was_active
    global _gate_sprinting
    if not (_active and _cfg_omni):
        return

    pc = _pc()
    pi = None if pc is None else pc.PlayerInput
    if pi is None:
        return
    input_address = obj._get_address()
    owner_input_address = pi._get_address()
    if input_address != owner_input_address:
        return
    if input_address != _pi_address:
        _cache_local(pc)

    # BL2 WillowPlayerInput.PlayerInput clears bDuck/bHoldDuck whenever
    # bTryToSprint is consumed and InputAllowsSprint returns True. Omni's
    # omnidirectional gate can otherwise make that true on side/backward
    # input during rapid crouch toggles. Crouch must win over remembered
    # auto-Sprint until the pawn has actually stood up. Only the sprint
    # permission result is blocked; regular crouched movement is untouched.
    pawn = None
    if _cfg_assist and _armed:
        pawn = pc.Pawn
        if pawn is None or _assist_crouching(pc, pawn) or _crouch_sprint_paused():
            return Block, False

    # Native gate/center/reversal remains selected even with Precision disabled.
    combined_gate = None
    combined_reversal = None
    if _cfg_native_precision or _cfg_precision:
        # The current owner was authenticated above. No engine function
        # runs between that read and this handoff. Native read_motion still
        # revalidates current input/pawn ownership at its own boundary.
        if pc is not None:
            if _cfg_native_precision:
                center_enabled = (_gate_sprinting and _cfg_direction != VANILLA
                                  and _cfg_gap > 0.0)
                reversal_enabled = (_gate_sprinting and _cfg_direction != VANILLA
                                    and _center_transition_origin is None)
                fused_motion = _precision.motion_fused(
                    pc, obj, _travel_epoch, precision_enabled=_cfg_precision,
                    strength=_cfg_precision_strength,
                    center_grace=_cfg_gap, center_enabled=center_enabled,
                    reversal_enabled=reversal_enabled,
                    correction_active=_reversal.origin is not None,
                    owner_input=pi, input_address=input_address, pawn=pawn,
                    owner_input_address=owner_input_address,
                    compact_gate=_cfg_direction == VANILLA)
                if fused_motion is not None:
                    if type(fused_motion) is int:
                        combined_gate = fused_motion
                        changed = fused_motion & 4
                    else:
                        combined_gate, combined_reversal, changed = fused_motion
                    if changed:
                        # Native already reset before observing this frame;
                        # clear Python mirrors without discarding that sample.
                        _reversal.reset()
                        _native_reversal_was_active = False
                        _reversal_last_at = None
                        _gate_last_moving_at = None
                        _gate_sprinting = bool(pc.bInSprintState)
                        _clear_center_transition()
            if combined_gate is None and _cfg_precision:
                _precision.update(pc, obj, enabled=True,
                                  strength=_cfg_precision_strength,
                                  prefer_native=_cfg_native_precision)


    # Sprint Assist's pending bTryToSprint must not be consumed while airborne.
    # BL2 otherwise converts it to bWantsToSprint, which can be cleared again if
    # the stick crosses/reaches center before landing. The guard is normally
    # false, so the steady-state hot path pays only this boolean check. While it
    # is true, one cheap Physics read releases it on the first grounded input
    # frame; no permanent PlayerTick or landing watcher is needed.
    if _cfg_assist and _armed and _assist_airborne_hold and not _gate_sprinting:
        pc = _pc()
        grounded = _assist_grounded(pc)
        if grounded:
            _assist_airborne_hold = False
            _set_hook(
                "assist-airborne-gate",
                False,
                INPUT_ALLOWS_SPRINT,
                Type.PRE,
                _assist_airborne_gate,
            )
        else:
            return Block, False
    # Use the raw movement axis that BL2 snapshots before its later input-context
    # processing. This is the same axis used by the older verified Doppelganger
    # build. Probe data also shows aBaseY can remain non-zero while aForward is
    # temporarily zero during state transitions, so aForward is not a safe gate.
    # Stage 8: preserve the proven Python PRE hook and reversal/assist paths.
    # Only the local PlayerInput uses the native movement-axis/center-grace
    # core; remote or replaced inputs retain the exact Python behavior.
    if (_cfg_native_precision and _pi_address != 0
            and input_address == _pi_address):
        center_enabled = (_gate_sprinting and _cfg_direction != VANILLA
                          and _cfg_gap > 0.0)
        native_gate = (combined_gate if combined_gate is not None else
                       _precision.gate_update(obj, _cfg_gap, center_enabled))
        if type(native_gate) is int:
            if native_gate & 2 or _pause_sprint_grace(pc):
                return Block, True
            _center_origin_velocity = None
            if _reversal_last_at is not None:
                _reset_reversal_state()
                _reversal_last_at = None
            return Block, False
        if native_gate is not None:
            fwd, side, moving, allowed, center_entered, center_crossed = native_gate
            if center_entered:
                _remember_center_origin()
            if moving:
                if center_crossed:
                    # Never let the older direct-flip observer compete with
                    # the bounded center-crossing velocity transition.
                    _reset_reversal_state()
                    _reversal_last_at = None
                    _apply_center_cross(fwd, side, fwd * fwd + side * side,
                                        time.perf_counter())
                elif (_gate_sprinting and _cfg_direction != VANILLA
                      and _center_transition_origin is None):
                    now = time.perf_counter()
                    magnitude_sq = fwd * fwd + side * side
                    _reversal_from_gate(fwd, side, magnitude_sq, now,
                                        preobserved=combined_reversal)
                return Block, True
            if not allowed and _pause_sprint_grace(pc):
                return Block, True
            if not allowed:
                _center_origin_velocity = None
                if _reversal_last_at is not None:
                    _reset_reversal_state()
                    _reversal_last_at = None
            return Block, bool(allowed)

    try:
        fwd = float(obj.aBaseY)
        side = float(obj.aStrafe)
    except Exception:
        return Block, False

    magnitude_sq = fwd * fwd + side * side
    moving = magnitude_sq > 0.0001  # equivalent to the established 0.01 input magnitude
    reversal_live = _gate_sprinting and _cfg_direction != VANILLA

    if moving:
        # Avoid even the clock call when both center grace and reversal timing are
        # disabled. With defaults one timestamp services both features.
        if _cfg_gap > 0.0 or reversal_live:
            now = time.perf_counter()
        else:
            now = 0.0
        if _cfg_gap > 0.0:
            _gate_last_moving_at = now
        _entered, crossed = _python_center_event(
            fwd, side, True, now, reversal_live and _cfg_gap > 0.0)
        if crossed:
            _reset_reversal_state()
            _reversal_last_at = None
            _apply_center_cross(fwd, side, magnitude_sq, now)
        elif reversal_live and _center_transition_origin is None:
            _reversal_from_gate(fwd, side, magnitude_sq, now)
        return Block, True

    if reversal_live and _cfg_gap > 0.0:
        entered, _crossed = _python_center_event(
            fwd, side, False, time.perf_counter(), True)
        if entered:
            _remember_center_origin()
    else:
        _python_center_event(fwd, side, False, 0.0, False)
    # The native gate owns a separate real-time clock; pause time can expire it
    # before the first nonzero input arrives. Apply the same bounded close grace
    # to either backend without granting a new sprint while stationary.
    if _pause_sprint_grace(pc):
        return Block, True
    # Preserve reversal history only through the configured center gap. A real
    # stop expires both sprint grace and reversal state.
    if _cfg_gap <= 0.0 or _gate_last_moving_at is None:
        # Sprint Assist can leave bTryToSprint pending while stationary, which
        # makes InputAllowsSprint execute every input frame. Once reversal state
        # has already been cleared, avoid rewriting all of its fields again on
        # every stationary frame.
        if _reversal_last_at is not None:
            _reset_reversal_state()
            _reversal_last_at = None
        return Block, False

    now = time.perf_counter()
    grace = 0.0 <= now - _gate_last_moving_at <= _cfg_gap
    if not grace:
        _gate_last_moving_at = None
        _center_origin_velocity = None
        _reset_reversal_state()
        _reversal_last_at = None
    return Block, grace


def _skill_assist_context(pc, pi=None, pawn=None, *, allow_guarded_swap=False):
    """Admit a live, class-declared action-skill input context for Sprint Assist.

    Each BL2 class uses its own selector (e.g. Scorpio, Phaselock, VisionMode,
    Gunzerking, Buzzaxe). The context can remain active after the skill's
    brief casting animation even when ordinary on-foot actions are allowed.
    Only Sprint Assist's remembered input and retry paths use this exception.
    CanSprint, weapon/animation vetoes, collision and general movement gates
    stay native/unchanged. The guarded-swap exception is exclusively for
    preserving an existing sprint through a verified equip handoff.
    """
    if not (_cfg_assist and pc is not None):
        return False
    pi = pc.PlayerInput if pi is None else pi
    pawn = pc.Pawn if pawn is None else pawn
    if pi is None or pawn is None:
        return False
    try:
        return bool(
            _same(pi, pc.PlayerInput)
            and _same(pawn, pc.Pawn)
            and _same(pawn.Controller, pc)
            and pawn.DrivenVehicle is None
            and int(pawn.Physics) in (1, 2)
            and (allow_guarded_swap or not _deathtrap_skill_active)
            and pawn.MyActionSkill is not None
            and bool(pawn.IsActionSkillRunning())
            # Reuses the existing PlayerClass.InputContexts ownership check.
            # Unknown, unrelated, vehicle and modal contexts remain denied.
            and _basic_context(pc, pi=pi, pawn=pawn, fire_pose_only=True)
        )
    except Exception:
        return False


def _assist_context(pc, pi=None, pawn=None):
    """Ordinary on-foot or verified live skill context, for Sprint Assist only."""
    if _basic_context(pc, pi=pi, pawn=pawn):
        return True
    return _skill_assist_context(pc, pi=pi, pawn=pawn)


def _pressed(obj, args, ret, func):
    global _armed, _gate_sprinting, _assist_airborne_hold, _travel_mode
    global _assist_press_held
    if not (_active and (_cfg_omni or _cfg_assist)):
        return
    pi = _local_input(obj)
    if pi is None:
        return
    pc = _pc()
    if pc is None:
        return
    if not _assist_context(pc, pi=pi):
        return
    # A held button can be replayed by destination input initialization. Do
    # not turn remembered Sprint into Walk before first travel rearm. Genuine
    # presses after the first ready destination boundary remain interactive.
    if _travel_pending and _travel_initial_rearm_at == 0.0:
        return

    if not _cfg_assist:
        # Native SprintPressed uses either the native bytecode gate or fallback.
        # Action-skill restrictions remain native; Omni does not globally block
        # the Sprint action while Digi-Jacks are active.
        return

    if _assist_press_held:
        return Block
    _assist_press_held = True
    selection = _precision.intent_press(
        pc, pi, _travel_epoch, _armed, prefer_native=_cfg_native_precision)
    if selection is None:
        _armed = not _armed
    else:
        _armed, changed = selection
        if not changed:
            return Block
    if _travel_pending:
        _travel_mode = _armed
    _set_try_sprint(pi, _armed)
    if _armed and _deathtrap_skill_active:
        _schedule_deathtrap_recovery(pc)
    elif _armed:
        # Selecting remembered Sprint in mid-air should remain pending until
        # landing; do not hand the request to BL2's lossy bWantsToSprint queue.
        _hold_assist_through_airborne(pc)
        if _skill_assist_context(pc, pi) and not pc.bInSprintState:
            # Native SprintPressed is still allowed to run below. Re-arm via
            # the existing bounded retry if its one-shot is consumed by
            # a class-defined skill's input/animation context.
            _assist_recover_or_schedule(pc)

    if not _armed:
        if _travel_pending:
            _stop_travel_recovery('walk-selected')
        _assist_airborne_hold = False
        _set_hook(
            "assist-airborne-gate",
            False,
            INPUT_ALLOWS_SPRINT,
            Type.PRE,
            _assist_airborne_gate,
        )
        # Explicit Walk is authoritative over every native pending sprint flag,
        # including bWantsToSprint created by an earlier airborne DoSprint.
        try:
            pc.bWantsToSprint = False
            pc.bPressedSprint = False
        except Exception:
            pass
        # Explicit Walk selection is authoritative. Do not leave a bounded pause
        # repair alive after the user intentionally leaves Sprint mode.
        _stop_crosshair_resync()
        _stop_assist_recovery()
        _clear_direction()
        _gate_sprinting = False
        with unrealsdk.hooks.prevent_hooking_direct_calls():
            pc.EndSprint()
        _sync_sprint_hooks(pc)
        return Block

    # Let the user's native SprintPressed action run. InputAllowsSprint supplies
    # omnidirectional eligibility plus the configured center-crossing grace.

def _released(obj, args, ret, func):
    global _assist_press_held
    if not (_active and _cfg_assist):
        return
    pi = _local_input(obj)
    if pi is None:
        return
    _assist_press_held = False
    pc = _pc()
    if pc is not None:
        _precision.intent_release(
            pc, pi, _travel_epoch, _armed, prefer_native=_cfg_native_precision)
        if _armed and not pc.bInSprintState:
            _assist_recover_or_schedule(pc)
        else:
            _stop_assist_recovery()
            _set_try_sprint(pi, False)
        return Block


def _start_fire(obj, args, ret, func):
    global _fire_requested, _pose_offhand_requested, _pose_release_at, _hip_hooks_until
    global _ads_sprint_edge
    pc = _local_pc(obj)
    if not (_active and pc is not None):
        return
    mode = int(args.FireModeNum)
    _combat_fire_edge(pc, mode, True)
    if mode == 1:
        pawn = pc.Pawn
        if pawn is None or pawn.OffHandWeapon is None:
            # Native zoom state may not be set yet when StartFire synchronously
            # ends sprint. Remember only the local ADS-entry edge briefly.
            if pawn is not None and pc.bInSprintState and _cfg_assist and _armed:
                try:
                    if not _fast_zoomed(pc, pawn.Weapon):
                        _ads_sprint_edge = (pc._get_address(), time.perf_counter() + 0.25)
                except Exception:
                    pass
            return  # Normal alternate fire is ADS, not a second firing hand.
        _pose_offhand_requested = True
        _pose_release_at = 0.0
        if _cfg_hip_fire and pc.bInSprintState:
            now = float(pc.WorldInfo.TimeSeconds)
            _hip_hooks_until = now + 0.20
            _sync_fire_hooks(True)
            _update_fire_pose(pc, firing=True, now=now)
            if (not _fire_pose.aliases_active
                    and _opposite_hand_fire_during_reload(pc, pawn)):
                _pose_reload_end_retry()
        return
    if mode != 0:
        return

    _fire_requested = True
    _pose_release_at = 0.0
    _stop_fire_state_recovery()
    if _cfg_assist and pc.PlayerInput is not None and not pc.bInSprintState:
        if _active_fire_entry and _armed:
            # Preserve the remembered request while the trigger remains held.
            # Existing native CanSprint and bounded assist retry still own admission.
            _assist_recover_or_schedule(pc)
        else:
            _set_try_sprint(pc.PlayerInput, False)

    if _cfg_hip_fire and pc.bInSprintState:
        _clear_direction()
        now = float(pc.WorldInfo.TimeSeconds)
        _hip_hooks_until = now + 0.20
        _sync_fire_hooks(True)
        _update_fire_pose(pc, firing=True, now=now)
        if (pc.Pawn is not None and not _fire_pose.aliases_active
                and _opposite_hand_fire_during_reload(pc, pc.Pawn)):
            _pose_reload_end_retry()


def _stop_fire(obj, args, ret, func):
    global _fire_requested, _pose_offhand_requested, _fire_until, _pose_release_at, _hip_hooks_until
    pc = _local_pc(obj)
    if not (_active and pc is not None):
        return
    mode = int(args.FireModeNum)
    _combat_fire_edge(pc, mode, False)
    if _pose_reload_addresses and _fire_pose.aliases_active:
        _update_fire_pose(pc, firing=bool(_combat_fire_mask), now=float(pc.WorldInfo.TimeSeconds))
    if mode == 1:
        was_requested = _pose_offhand_requested
        _pose_offhand_requested = False
        if not was_requested or _pose_fire_requested():
            return
        if _sprint_fire_entry_remaining:
            _stop_sprint_fire_entry_probe()
        if _cfg_hip_fire and pc.bInSprintState:
            now = float(pc.WorldInfo.TimeSeconds)
            _pose_release_at = now + DUAL_FIRE_RELEASE_DELAY
            _hip_hooks_until = max(_hip_hooks_until, _pose_release_at)
            _schedule_hip_cleanup()
        return
    if mode != 0:
        return

    _fire_requested = False
    if _sprint_fire_entry_remaining and not _pose_fire_requested():
        _stop_sprint_fire_entry_probe()
    now = float(pc.WorldInfo.TimeSeconds)
    # Preserve the old short post-shot reversal guard without paying a Python
    # callback for every AddWeaponKick on automatic weapons.
    _fire_until = now + 0.15

    if _cfg_hip_fire and (_hip_hooks_live or pc.bInSprintState) and not _pose_fire_requested():
        delay = DUAL_FIRE_RELEASE_DELAY if pc.Pawn is not None and pc.Pawn.OffHandWeapon is not None else 0.18
        _pose_release_at = now + delay
        _hip_hooks_until = max(_hip_hooks_until, _pose_release_at)
        _schedule_hip_cleanup()
    else:
        _pose_release_at = 0.0

    if _cfg_assist and _armed and not pc.bInSprintState and pc.PlayerInput is not None:
        # StopFire is an input-release edge, not the end of WeaponFiring.
        # CanSprint may also be patched for sprint entry; wait for the actual
        # weapon-state boundary rather than assuming sprint is eligible.
        if _deathtrap_skill_active:
            _schedule_deathtrap_recovery(pc)
        elif _weapons_still_firing(pc):
            _set_try_sprint(pc.PlayerInput, False)
            if not _weapon_firing_end_available:
                _schedule_fire_state_recovery()
        else:
            _rearm_after_fire(pc)


def _impulse(obj, args, ret, func):
    global _knockback_until
    if not (_active and _is_local_pawn(obj)):
        return
    pc = _pc()
    if pc is not None:
        _knockback_until = time.perf_counter() + 0.6
        _clear_direction()
        _clear_center_transition()


def _spawned(obj, args, ret, func):
    global _armed, _weapon_address, _gate_sprinting
    global _deathtrap_skill_active, _deathtrap_skill_ref, _deathtrap_context_seen
    pc = _local_pc(obj)
    if pc is None and _active:
        # The local controller may have been replaced by the map load.
        fresh = _pc(refresh=True)
        if fresh is not None and _same(fresh, obj):
            pc = fresh
    if not (_active and pc is not None):
        return

    _restore_baseline()
    _clear_assist()
    _reset_runtime_motion()
    _release_crosshair_visibility()
    _clear_crosshair_widget()
    _cache_local(pc)
    _armed = _travel_mode if _travel_pending and _travel_mode is not None else _cfg_starting
    _precision.intent_reset(_armed)
    _deathtrap_skill_active = False
    _deathtrap_skill_ref = None
    _deathtrap_context_seen = False
    _coyote_seed(pc)
    _gate_sprinting = bool(pc.bInSprintState)
    _weapon_address = 0 if pc.Pawn is None or pc.Pawn.Weapon is None else pc.Pawn.Weapon._get_address()
    # During travel this is too early: the destination can replace PlayerInput
    # and discard bTryToSprint after this spawn callback. The travel watcher
    # will rearm on a stable, genuinely ready destination generation instead.
    if _cfg_assist and _armed and pc.PlayerInput is not None and not _travel_pending:
        _assist_recover_or_schedule(pc)
    _sync_sprint_hooks(pc)
    if _travel_pending:
        _start_travel_recovery()


def _stop_travel_input_handoff():
    # A two-field synthetic write may still be owned when input recovery ends.
    # Release against the exact current PlayerInput before retiring the hook.
    # Do not follow an old input pointer across a travel generation.
    if _travel_input.synthetic:
        try:
            pc = _pc()
            pi = None if pc is None else pc.PlayerInput
            if pi is not None and pi._get_address() == _travel_input.pi_address:
                _travel_input.apply_pre(pi, False)
        except Exception as exc:
            logging.warning("omnimovement: travel input release failed: " + repr(exc))
    _set_hook("travel-input-handoff", False, INPUT_FRAME,
              Type.POST_UNCONDITIONAL, _travel_input_frame)
    if _travel_input.synthetic:
        # Keep only the numeric owner/generation ledger. Do not resume writes
        # to an uncertain or retired PlayerInput during module shutdown.
        logging.warning(
            "omnimovement: travel input remains unverified after hook stop; "
            "restart BL2 if forward movement is affected"
        )
    else:
        _travel_input.reset()


def _travel_input_frame(obj, args, ret, func):
    """Bridge physically held input just BEFORE native PIProcessInput.

    Engine.PlayerInput copies RawJoyUp then calls Willow.PreProcessInput, followed by
    native PIProcessInput and input matching. The POST of that *base helper*
    lands at the safe handoff; WillowPlayerInput.PlayerInput PRE did not.
    """
    if not (_active and _cfg_assist
            and (_travel_pending or _travel_input.synthetic)):
        _stop_travel_input_handoff()
        return
    pi = _local_input(obj)
    if pi is None:
        return
    pc = _pc()
    current_pi = None if pc is None else pc.PlayerInput
    if current_pi is None or not _same(current_pi, pi):
        return
    try:
        modal = (_travel_modal_open or _crosshair_modal_open or
                 bool(pc.GFxUIManager.IsBlockingMoviePlaying()))
    except Exception:
        modal = _travel_modal_open or _crosshair_modal_open
    # A W press from the loading screen may exist only in our established
    # travel ledger. Changing to Gunzerking must not discard that exact press.
    # Do not start a new keyboard injection or take over a controller here.
    carry_key = bool(not modal and _travel_input.synthetic_kind == "keyboard"
                     and _travel_input.pi_address == pi._get_address()
                     and str(pi.CurrentInputContextName) == "Gunzerking"
                     and _skill_assist_context(pc, pi=pi))
    _travel_input.apply_pre(pi, not modal and (_basic_context(pc, pi=pi) or carry_key),
                            keyboard_only=carry_key)
    # A key held through loading may have no native press event in the new
    # world. Keep its per-frame repair until release/native input takes over,
    # even after sprint recovery succeeds or its deadline expires.
    if not _travel_pending and not _travel_input.synthetic:
        _stop_travel_input_handoff()


def _travel_walking_process_move(obj, args, ret, func):
    """Grounded travel-only reconciliation AFTER vanilla walking ProcessMove.

    WillowPlayerInput and super.ProcessMove have processed current analog input
    here; unlike PlayerTick, both the pawn physics and normalized aForward are
    synchronized. Normal DoSprint owns the native sprint flags and attribute
    effects. Never write bInSprintState, speed, velocity or input here.
    """
    global _travel_walk_last_attempt
    if not (_active and _cfg_assist and _travel_pending and _travel_mode):
        return
    pc = _local_travel_controller(obj)
    if pc is None:
        return
    pawn = pc.Pawn
    if pawn is None:
        return
    pi = pc.PlayerInput
    if pi is None:
        return
    wall_now = time.monotonic()
    context_ready = (not _travel_modal_open and not _crosshair_modal_open
                     and _basic_context(pc, pi=pi, pawn=pawn))
    now = _travel_budget.sample(wall_now, context_ready)
    expired = _travel_budget.expiration(wall_now)
    if expired:
        _stop_travel_recovery(expired)
        return
    if not context_ready:
        return
    if bool(pc.bInSprintState):
        return
    # Only repair a genuinely held physical forward input recorded recently by
    # the post-PreProcessInput hook. A stale input-generation sample is invalid.
    held = (_travel_input.pi_address == pi._get_address()
            and wall_now - _travel_input.last_sample_at <= 0.30
            and _travel_input.last_source in ('controller', 'keyboard')
            and _travel_input.last_physical >= 0.40)
    if not held:
        return
    # Only pure budget arithmetic and property reads separate this from the
    # context check above. The engine calls below retain their fresh guards.
    if now - _travel_walk_last_attempt < 0.30:
        return
    # Input must be native-processed and pointing forward. This avoids making
    # sprint active when the game has lost movement or a menu owns the sticks.
    try:
        if (float(pi.aForward) <= 0.10 or not bool(pi.bCanSprint)
                or not bool(pawn.IsOnGroundOrShortFall())
                or not _assist_can_retry(pc)):
            return
    except Exception:
        return
    _travel_walk_last_attempt = now
    try:
        with unrealsdk.hooks.prevent_hooking_direct_calls():
            accepted = bool(pawn.DoSprint(False))
        native_sprint = bool(pc.bInSprintState)
        if accepted and native_sprint:
            _set_try_sprint(pi, False)
    except Exception as exc:
        pass


def _travel_normal_sprint_retry(pc, pi):
    """Retry native guarded DoSprint; never force BeginSprint or velocity."""
    if not _assist_can_retry(pc) or not _assist_raw_moving(pc):
        return False
    try:
        with unrealsdk.hooks.prevent_hooking_direct_calls():
            accepted = bool(pc.Pawn.DoSprint(False))
        if accepted and bool(pc.bInSprintState):
            _set_try_sprint(pi, False)
        return accepted
    except Exception:
        return False


def _begin_travel_operation():
    """Save one selected mode; arrival duplicates retain the same operation."""
    global _travel_pending, _travel_mode, _travel_operation
    if not _travel_pending:
        _travel_operation += 1
        _travel_pending = True
        _travel_mode = _armed


def _reset_travel_readiness():
    """Invalidate transient readiness without restarting the eligible clock."""
    global _travel_ready_ticks, _travel_close_ready_ticks
    global _travel_ready_validated, _travel_native_owner
    _travel_ready_ticks = _travel_close_ready_ticks = 0
    _travel_ready_validated = False
    _travel_native_owner = None
    _precision.reset_travel()


def _travel_readiness(pc, now):
    """One selected readiness owner; None means wait for a stable destination."""
    global _travel_native_owner, _travel_ready_ticks, _travel_close_ready_ticks
    global _travel_close_pending, _travel_last_rearm_at
    global _travel_initial_rearm_at, _travel_verified_sprint
    state = _precision.travel_gate(
        _travel_epoch, ready=True, modal_open=False,
        close_pending=_travel_close_pending,
        sprinting=bool(pc.bInSprintState), armed=bool(_travel_mode),
        now=now, prefer_native=_cfg_native_precision)
    native_owner = state is not None
    if _travel_native_owner is not None and native_owner != _travel_native_owner:
        # A partially observed native sequence never counts toward Python's
        # three/six frame gates. Keep operation, selected mode and budget.
        _travel_ready_ticks = _travel_close_ready_ticks = 0
        _travel_last_rearm_at = _travel_initial_rearm_at = 0.0
        _travel_verified_sprint = False
    _travel_native_owner = native_owner
    if state is not None:
        ready, retry_due, close_settled = state
        if not ready:
            return None
        if close_settled:
            _travel_close_pending = False
            _travel_last_rearm_at = 0.0
        return retry_due
    _travel_ready_ticks = min(3, _travel_ready_ticks + 1)
    if _travel_ready_ticks < 3:
        return None
    if _travel_close_pending:
        _travel_close_ready_ticks += 1
        if _travel_close_ready_ticks < 6:
            return None
        _travel_close_pending = False
        _travel_last_rearm_at = 0.0
    return (_travel_last_rearm_at == 0.0 or now - _travel_last_rearm_at >= 0.25)


def _start_travel_recovery():
    global _travel_ready_ticks, _travel_initial_rearm_at
    global _travel_generation, _travel_epoch, _travel_ready_validated, _travel_verified_sprint
    global _travel_last_rearm_at
    global _travel_modal_open, _travel_close_pending, _travel_close_ready_ticks
    global _travel_walk_hook_live
    global _travel_walk_last_attempt
    # Allow for a late GFx teardown and streaming load. The watcher exits as
    # soon as confirmed, but never survives an unrelated session forever.
    if _travel_budget.active:
        # Arrival notifications belong to the same operation, not a new budget.
        _travel_budget.sample(time.monotonic(), False)
        _travel_modal_open = bool(_crosshair_modal_open)
        _reset_travel_readiness()
        return
    _travel_epoch += 1
    _reset_travel_readiness()
    _travel_budget.start(time.monotonic())
    _travel_generation = None
    _travel_ready_validated = False
    _travel_verified_sprint = False
    _travel_last_rearm_at = 0.0
    _travel_input.reset()
    _travel_walk_last_attempt = -1.0
    _travel_ready_ticks = 0
    _precision.reset_assist('travel')
    _travel_initial_rearm_at = 0.0
    # If the travel UI opened before NotifyLoadedWorld, its pause-open event
    # may already have fired. Carry that modal state into destination recovery.
    _travel_modal_open = bool(_crosshair_modal_open)
    _travel_close_pending = False
    _travel_close_ready_ticks = 0
    if not _set_optional_hook("travel-input-handoff", True, INPUT_FRAME,
                              Type.POST_UNCONDITIONAL, _travel_input_frame):
        logging.warning("omnimovement: destination input-handoff hook unavailable; "
                        "falling back to normal guarded sprint retry")
    _travel_walk_hook_live = _set_optional_hook(
        "travel-walking-state", True, WALKING_PROCESS_MOVE,
        Type.POST_UNCONDITIONAL, _travel_walking_process_move)
    if not _travel_walk_hook_live:
        logging.warning("omnimovement: grounded ProcessMove travel hook unavailable; "
                        "using conservative PlayerTick fallback")
    _set_hook("travel-recovery", True, PLAYER_TICK,
              Type.POST_UNCONDITIONAL, _travel_recovery_tick)


def _stop_travel_recovery(reason='cancelled'):
    global _travel_walk_hook_live
    global _travel_pending, _travel_mode, _travel_ready_ticks
    global _travel_generation, _travel_ready_validated, _travel_verified_sprint
    global _travel_last_rearm_at
    global _travel_initial_rearm_at, _travel_modal_open, _travel_close_pending
    global _travel_close_ready_ticks
    _travel_budget.finish(reason)
    _reset_travel_readiness()
    _travel_pending = False
    _travel_generation = None
    _travel_ready_validated = False
    _travel_verified_sprint = False
    _travel_last_rearm_at = 0.0
    _travel_mode = None
    _travel_ready_ticks = 0
    _precision.reset_assist('travel')
    _travel_initial_rearm_at = 0.0
    _travel_modal_open = False
    _travel_close_pending = False
    _travel_close_ready_ticks = 0
    _set_hook("travel-walking-state", False, WALKING_PROCESS_MOVE,
              Type.POST_UNCONDITIONAL, _travel_walking_process_move)
    _travel_walk_hook_live = False
    if not (_active and _cfg_assist and _travel_input.synthetic):
        _stop_travel_input_handoff()
    _set_hook("travel-recovery", False, PLAYER_TICK,
              Type.POST_UNCONDITIONAL, _travel_recovery_tick)


def _local_travel_controller(obj):
    """Recognize a genuinely local event even if get_pc still points at old world.

    Native IsLocalPlayerController comes from the exported Controller contract;
    it is not a cached weak-pointer test. Never accept a remote client's event.
    """
    if obj is None:
        return None
    existing = _local_pc(obj)
    if existing is not None:
        return existing
    try:
        if bool(obj.IsLocalPlayerController()):
            _cache_local(obj)
            return obj
    except Exception:
        pass
    return None


def _pre_client_travel(obj, args, ret, func):
    global _coyote_traveling
    if globals().get("_sprint_fire_entry_remaining", 0):
        _stop_sprint_fire_entry_probe()
    if _active and _coyote_enabled() and _local_travel_controller(obj) is not None:
        _coyote_traveling = True
        _coyote.invalidate()
    if not (_active and _cfg_assist and _local_travel_controller(obj) is not None):
        return
    # PRE runs before the old world and its input hooks disappear. A POST
    # callback is not guaranteed to return on level transitions.
    if _travel_pending:
        if not _travel_budget.active:
            return  # Duplicate departure before destination recovery starts.
        _stop_travel_recovery('superseded-by-travel')
    _begin_travel_operation()
    _precision.restore()
    _clear_assist()


def _world_loaded(obj, args, ret, func):
    global _coyote_traveling
    if _active and _coyote_enabled() and _local_travel_controller(obj) is not None:
        _coyote_traveling = False
        _coyote.invalidate()
    if not (_active and _cfg_assist):
        return
    local = _local_travel_controller(obj)
    if local is None:
        return
    # NotifyLoadedWorld fires for intermediate travel too; arm for the actual
    # destination only. The engine passes bFinalDest to the script event.
    try:
        if not bool(args.bFinalDest):
            return
    except AttributeError:
        pass
    # Some fast travel paths do not pass through PreClientTravel. Reconcile the
    # already selected intent after NotifyLoadedWorld on those paths as well.
    _begin_travel_operation()
    _start_travel_recovery()


def _fast_travel_loaded(obj, args, ret, func):
    """BL2 streaming fast travel bypasses PreClientTravel/NotifyLoadedWorld."""
    global _coyote_traveling
    if _active and _coyote_enabled() and _local_travel_controller(obj) is not None:
        _coyote_traveling = False
        _coyote_seed(_pc())
    if not (_active and _cfg_assist):
        return
    if _local_travel_controller(obj) is None:
        return
    _begin_travel_operation()
    _start_travel_recovery()


def _travel_recovery_tick(obj, args, ret, func):
    """Repair held-stick sprint across stable input generations and late GFx.

    Travel may deliver SpawningProcessComplete before PlayerInput/HUD teardown
    finishes. Re-arm on stable readiness and retry at bounded intervals until
    the *game's* bInSprintState confirms success. Never force BeginSprint.
    """
    global _armed, _travel_ready_ticks, _travel_initial_rearm_at
    global _travel_close_pending, _travel_close_ready_ticks
    global _travel_generation, _travel_epoch, _travel_ready_validated, _travel_verified_sprint
    global _travel_last_rearm_at
    if not (_active and _cfg_assist and _travel_pending):
        _stop_travel_recovery('cancelled')
        return
    wall_now = time.monotonic()
    expired = _travel_budget.expiration(wall_now)
    if expired:
        _stop_travel_recovery(expired)
        return
    # Never stop a "previously successful" recovery until this CURRENT game
    # frame has confirmed native bInSprintState. Late UI/input replacement can
    # revoke an earlier success right at the three-second settle boundary.
    # Don't resolve old controller from a stale local weak ref during travel.
    pc = _local_pc(obj)
    if pc is None:
        fresh = _pc(refresh=True)
        if fresh is None or not _same(obj, fresh):
            _travel_budget.sample(wall_now, False)
            if _travel_ready_validated:
                _reset_travel_readiness()
            return
        pc = fresh
    pi = pc.PlayerInput
    pawn = pc.Pawn
    if pi is None or pawn is None:
        _travel_budget.sample(wall_now, False)
        if _travel_ready_validated:
            _reset_travel_readiness()
        return
    try:
        generation = (pc._get_address(), pi._get_address(), pawn._get_address())
    except Exception:
        _travel_budget.sample(wall_now, False)
        if _travel_ready_validated:
            _reset_travel_readiness()
        return
    if generation != _travel_generation:
        _travel_budget.sample(wall_now, False)
        _travel_generation = generation
        _travel_epoch += 1
        _travel_verified_sprint = False
        _reset_travel_readiness()
        _travel_initial_rearm_at = 0.0
        _travel_last_rearm_at = 0.0
        # A stale prior input generation's pending request is not an indication
        # that the freshly initialized destination input has been rearmed.
    if (_travel_modal_open or _crosshair_modal_open
            or not _basic_context(pc, pi=pi, pawn=pawn)):
        _travel_budget.sample(wall_now, False)
        if _travel_ready_validated:
            # Native gate must not retain three "good" destination frames
            # across a later invalid context on the SAME input generation.
            _reset_travel_readiness()
        return
    now = _travel_budget.sample(wall_now, True)
    expired = _travel_budget.expiration(wall_now)
    if expired:
        _stop_travel_recovery(expired)
        return
    _travel_ready_validated = True

    retry_due = _travel_readiness(pc, now)
    if retry_due is None:
        return

    # Hold the selected mode across map handoff; incidental native input
    # reinitialization must not flip Omni's own remembered toggle.
    if _armed != bool(_travel_mode):
        _armed = bool(_travel_mode)
        _precision.intent_reset(_armed)
    is_sprinting = bool(pc.bInSprintState)
    if _armed and is_sprinting:
        _travel_verified_sprint = True
    elif not _armed:
        _travel_verified_sprint = True
        _stop_assist_recovery()
        _set_try_sprint(pi, False)
    else:
        _travel_verified_sprint = False
        # Two separate events can consume bTryToSprint while held: the
        # destination's one-shot DoSprint and a subsequent HUD/input rebuild.
        # Repeat only during this bounded transition, not during normal play.
        if retry_due:
            # Stage17.4: do not clear bWantsToSprint here. Native DoSprint
            # can queue it during other maps' extended falling/spawn handoff.
            # The grounded walking-state POST hook owns direct retries after
            # native input processing; only use the old tick retry if this
            # state-specific hook could not be installed.
            if not _travel_walk_hook_live:
                _travel_normal_sprint_retry(pc, pi)
            if not bool(pc.bInSprintState):
                _assist_recover_or_schedule(pc)
            _travel_last_rearm_at = now
    if _travel_initial_rearm_at == 0.0:
        _travel_initial_rearm_at = now
    # On a second UI close or a replaced PlayerInput, the state-generation and
    # six-frame settle logic above automatically restarts the recovery gate.
    if (_travel_verified_sprint and not _travel_modal_open
            and not _travel_close_pending and _travel_initial_rearm_at > 0
            and now - _travel_initial_rearm_at >= 3.0):
        _stop_travel_recovery('confirmed-recovery' if _armed else 'inactive-intent')


def _sync_sprint_hooks(pc=None):
    if pc is None:
        pc = _pc()
    sprinting = bool(_active and pc is not None and pc.bInSprintState)

    # Reversal observation is fused into InputAllowsSprint, so there is no
    # PlayerMove Python callback in any direction mode.

    # Broad AddVelocity interception exists only during a real sprint and only for
    # the two modes that can write reversal velocity.
    _set_hook(
        "impulse",
        sprinting and _cfg_omni and _cfg_direction != VANILLA,
        ADD_VELOCITY,
        Type.PRE,
        _impulse,
    )

    # Start/StopFire are low-frequency input boundaries, not per-frame/per-shot
    # callbacks. Keep them active whenever Sprint Assist or hip-fire needs trigger
    # state so firing can recover remembered Sprint even if sprint is not active.
    _sync_fire_input_hooks(_cfg_assist or _cfg_hip_fire)

    if not sprinting:
        if _sprint_fire_entry_remaining:
            _stop_sprint_fire_entry_probe()
        _sync_fire_hooks(False)
        _fire_pose.restore()

    _sync_crosshair_hooks(pc)


def _sync_feature_hooks():
    press_live = _active and (_cfg_omni or _cfg_assist)
    # These callbacks are required for the corresponding sprint features.
    # A partial registration must not be reported as enabled or leave any
    # other feature hook active after the caller rolls back the transaction.
    allows_live = _active and _cfg_omni
    allows_ok = _set_hook(
        "allows-sprint",
        allows_live,
        INPUT_ALLOWS_SPRINT,
        Type.PRE,
        _allows,
    )
    if allows_live and not allows_ok:
        raise RuntimeError("required sprint hook unavailable: InputAllowsSprint")
    press_ok = _set_hook("sprint-pressed", press_live, PRESSED, Type.PRE, _pressed)
    if press_live and not press_ok:
        raise RuntimeError("required sprint hook unavailable: SprintPressed")
    # Optional coyote, skill and presentation callbacks may be unavailable
    # on individual versions without aborting normal sprint features.
    _sync_coyote_hooks()
    _set_hook("assist-released", _active and _cfg_assist, RELEASED, Type.PRE, _released)
    _set_hook("assist-duck-pressed", _active and _cfg_assist,
              DUCK_PRESSED, Type.POST_UNCONDITIONAL, _assist_duck_input)
    _set_hook("assist-duck-released", _active and _cfg_assist,
              DUCK_RELEASED, Type.POST_UNCONDITIONAL, _assist_duck_input)
    _set_hook("assist-uncrouched", _active and _cfg_assist,
              CROSSHAIR_UNCROUCH, Type.POST_UNCONDITIONAL, _assist_uncrouched)
    _set_hook(
        "assist-do-sprint",
        _active and _cfg_assist,
        DO_SPRINT,
        Type.POST_UNCONDITIONAL,
        _do_sprint_attempt,
    )
    _set_optional_hook(
        "assist-landed",
        _active and _cfg_assist,
        CROSSHAIR_LANDED,
        Type.POST_UNCONDITIONAL,
        _assist_landed,
    )
    # Never arm reload tracking without its matching end callback. Otherwise a
    # missing optional SDK endpoint could leave pose ownership disabled forever.
    reload_live = bool(_active and _cfg_hip_fire)
    reload_end_ok = _set_optional_hook("pose-reload-end", reload_live,
                                      POSE_RELOAD_END, Type.POST_UNCONDITIONAL, _pose_reload_end)
    reload_begin_ok = _set_optional_hook("pose-reload-begin", reload_live and reload_end_ok,
                                        POSE_RELOAD_BEGIN, Type.POST_UNCONDITIONAL, _pose_reload_begin)
    if not reload_begin_ok:
        _set_optional_hook("pose-reload-end", False, POSE_RELOAD_END,
                           Type.POST_UNCONDITIONAL, _pose_reload_end)
        _pose_reload_addresses.clear()
        _stop_pose_reload_retry()
    _set_optional_hook(
        "combat-action-begin", _active and _cfg_omni and _cfg_assist,
        SHARED_WEAPON_ACTION, Type.POST_UNCONDITIONAL, _combat_action_begin,
    )
    _set_optional_hook(
        "weapon-action-complete",
        _active and _cfg_assist,
        WEAPON_ACTION_COMPLETE,
        Type.POST_UNCONDITIONAL,
        _weapon_action_completed,
    )
    _set_optional_hook(
        "weapon-action-pose-complete",
        _active and _cfg_hip_fire,
        WEAPON_ACTION_COMPLETE,
        Type.POST_UNCONDITIONAL,
        _weapon_action_pose_completed,
    )
    _set_optional_hook(
        "zero-execute-control-return",
        _active and _cfg_assist,
        ZERO_EXECUTE_CONTROLS_RESTORED,
        Type.POST_UNCONDITIONAL,
        _zero_execute_controls_restored,
    )
    _set_optional_hook(
        "zero-execute-final-cleanup",
        _active and _cfg_assist,
        ZERO_EXECUTE_CLEANUP,
        Type.POST_UNCONDITIONAL,
        _zero_execute_cleanup_completed,
    )
    _set_optional_hook(
        "zero-execute-physics-pre",
        _active and _cfg_assist,
        ZERO_EXECUTE_PHYSICS_RESTORE,
        Type.PRE,
        _zero_final_restore_pre,
    )
    _set_optional_hook(
        "zero-execute-physics-post",
        _active and _cfg_assist,
        ZERO_EXECUTE_PHYSICS_RESTORE,
        Type.POST_UNCONDITIONAL,
        _zero_final_restore_post,
    )
    _set_optional_hook(
        "melee-pose-release",
        _active and (_cfg_hip_fire or _cfg_omni),
        MELEE_DISPATCH,
        Type.PRE,
        _melee_dispatch_enter,
    )
    _set_optional_hook(
        "melee-locomotion-exit",
        _active and _cfg_omni,
        MELEE_DISPATCH,
        Type.POST_UNCONDITIONAL,
        _melee_dispatch_exit,
    )
    _set_hook("spawned", _active, SPAWNED, Type.POST_UNCONDITIONAL, _spawned)
    travel_depart_hook = _set_optional_hook(
        "travel-depart", _active and (_cfg_assist or _coyote_enabled()), PRE_CLIENT_TRAVEL,
        Type.PRE, _pre_client_travel)
    travel_loaded_hook = _set_optional_hook(
        "travel-loaded", _active and (_cfg_assist or _coyote_enabled()), NOTIFY_LOADED_WORLD,
        Type.PRE, _world_loaded)
    fast_travel_depart_hook = _set_optional_hook(
        "fast-travel-depart", _active and (_cfg_assist or _coyote_enabled()), FAST_TRAVEL_REQUEST,
        Type.PRE, _pre_client_travel)
    fast_travel_loaded_hook = _set_optional_hook(
        "fast-travel-loaded", _active and (_cfg_assist or _coyote_enabled()), FAST_TRAVEL_LOADED,
        Type.POST_UNCONDITIONAL, _fast_travel_loaded)
    if _active and _cfg_assist and not (travel_depart_hook and travel_loaded_hook):
        logging.warning("omnimovement: missing travel lifecycle hook(s): "
                        + "depart=" + str(travel_depart_hook)
                        + ", loaded=" + str(travel_loaded_hook))
    if _active and _cfg_assist and not (fast_travel_depart_hook and fast_travel_loaded_hook):
        logging.warning("omnimovement: missing fast-travel lifecycle hook(s): "
                        + "depart=" + str(fast_travel_depart_hook)
                        + ", loaded=" + str(fast_travel_loaded_hook))
    _set_hook(
        "action-skill-start",
        _active and (_cfg_omni or _cfg_assist or _coyote_enabled()),
        ACTION_SKILL_STARTED,
        Type.PRE,
        _action_skill_started,
    )
    _set_hook(
        "action-skill-end",
        _active and (_cfg_omni or _cfg_assist),
        ACTION_SKILL_ENDED,
        Type.POST_UNCONDITIONAL,
        _action_skill_ended,
    )
    _set_optional_hook(
        "input-context",
        _active and (_cfg_omni or _cfg_assist or _cfg_crosshair or _coyote_enabled()),
        SET_INPUT_CONTEXT,
        Type.POST_UNCONDITIONAL,
        _input_context_changed,
    )
    # StartFire/StopFire remain cheap input boundaries. Sprint Assist re-arms from
    # WeaponFiring.EndState so automatic weapons cannot consume the retry early.
    _sync_weapon_firing_end_hook(_active and _cfg_assist)

    # The expensive continuation override is still sprint/fire scoped.
    _set_hook(
        "begin-sprint-pre",
        _active and ((_cfg_baseline and BASELINE_AVAILABLE) or _cfg_omni or _cfg_assist),
        BEGIN_SPRINT,
        Type.PRE,
        _begin_sprint,
    )
    _set_hook(
        "begin-sprint-post",
        _active and (_cfg_omni or _cfg_baseline or _cfg_crosshair),
        BEGIN_SPRINT,
        Type.POST_UNCONDITIONAL,
        _began_sprint,
    )
    _set_hook(
        "end-sprint",
        _active and (_cfg_omni or _cfg_assist or _cfg_baseline or _cfg_hip_fire or _cfg_crosshair),
        END_SPRINT,
        Type.POST,
        _ended_sprint,
    )
    _set_hook(
        "weapon-accuracy",
        _active and _cfg_baseline,
        INIT_ACCURACY,
        Type.PRE,
        _weapon_accuracy,
    )

    if _active and (_cfg_omni or _cfg_assist or _cfg_hip_fire or _cfg_crosshair):
        _install_weapon_state_hooks()
    else:
        _remove_weapon_state_hooks()

    if not _cfg_assist:
        _clear_assist()
    if not _cfg_baseline:
        _set_hook(
            "accuracy-scope",
            False,
            ATTRIBUTE_MODIFIER,
            Type.PRE,
            _accuracy_modifier,
        )
        _restore_baseline()

    _sync_sprint_hooks()


def _option_changed(option, value):
    global _armed, _gate_sprinting, _assist_recovery_ready_frames
    overrides = {option.identifier: value}
    previous_crosshair = _cfg_crosshair
    previous_hip = _cfg_hip_fire
    previous_baseline = _cfg_baseline


    _sync_config(overrides)
    if option.identifier in {"coyote_time", "coyote_window"}:
        _coyote.invalidate()
    if option.identifier == "direction":
        logging.info("omnimovement: Direction Handling set to " + _cfg_direction)

    if option.identifier in {"precision", "precision_strength", "native_precision"}:
        _precision.reset()
        _precision.intent_reset(_armed)
        _assist_recovery_ready_frames = 0
    if option.identifier == "native_precision":
        if _travel_pending:
            _travel_budget.sample(time.monotonic(), False)
            _reset_travel_readiness()
        logging.info(
            "omnimovement: movement backend applied="
            + ("C++" if _cfg_native_precision else "Python")
            + "; precision=" + str(_cfg_precision)
            + "; active=" + str(_active)
        )
    if option.identifier in {"direction", "gap_window", "omni", "native_precision", "precision", "precision_strength"}:
        _clear_direction()
        _clear_center_transition()
    if option.identifier in {"gap_window", "omni", "native_precision", "precision"}:
        _reset_gate_grace()
    if option.identifier == "assist":
        _stop_travel_recovery()
        _clear_assist()
        _armed = _cfg_starting
        _precision.intent_reset(_armed)
        if _active and _cfg_assist and _armed:
            pc = _pc()
            if pc is not None and pc.PlayerInput is not None and not pc.bInSprintState:
                _assist_recover_or_schedule(pc)

    if option.identifier == "begin_fire_sprint" and _active:
        logging.info(
            "omnimovement: Begin Sprint While Firing saved="
            + str(_cfg_begin_fire_sprint)
            + "; currently active=" + str(_active_fire_entry)
            + "; disable and re-enable OmniMovement to apply"
        )

    if option.identifier == "hip_fire" and _active:
        if _cfg_hip_fire:
            _set_native_hipfire(True)
        else:
            _sync_fire_hooks(False)
            _set_native_hipfire(False)
            _fire_pose.restore()
    elif previous_hip and not _cfg_hip_fire:
        _sync_fire_hooks(False)
        _fire_pose.restore()

    if previous_crosshair and not _cfg_crosshair:
        _release_crosshair_visibility()
        _clear_crosshair_widget()

    if previous_baseline != _cfg_baseline:
        _restore_baseline()
        pc = _pc()
        if _cfg_baseline and pc is not None and pc.Pawn is not None and pc.bInSprintState:
            _gate_sprinting = False
            with unrealsdk.hooks.prevent_hooking_direct_calls():
                pc.EndSprint()

    if _active:
        try:
            _sync_feature_hooks()
        except RuntimeError as exc:
            logging.warning("omnimovement: " + str(exc) + "; disabling incomplete sprint features")
            # A menu change can request Omni/Assist while already enabled.
            # Roll back the whole runtime without rewriting the saved choice.
            mod.disable(dont_update_setting=True)
            return
        if option.identifier in {"coyote_time", "coyote_window"}:
            _coyote_seed(_pc())


for _option in (
    omni,
    assist,
    starting,
    direction,
    gap_window,
    coyote_time,
    coyote_window,
    hip_fire,
    begin_fire_sprint,
    crosshair,
    baseline,
    precision,
    native_precision,  # Apply C++/Python changes without re-enabling the mod.
    precision_strength,
):
    _option.on_change_while_enabled = _option_changed
del _option


def on_enable():
    global _active, _armed, _sprint_depth, _weapon_address, _gate_sprinting
    global _requested_fire_entry
    global _crosshair_weapon_address, _crosshair_failure_logged
    global _crosshair_strategy, _crosshair_visibility_available
    global _coyote_traveling, _coyote_failure_logged

    if _active:
        return

    _sync_config()
    _requested_fire_entry = bool(_cfg_begin_fire_sprint)
    _coyote_traveling = False
    _coyote_failure_logged = False
    _stop_travel_recovery()
    _armed = _cfg_starting
    _sprint_depth = 0
    _crosshair_weapon_address = 0
    _crosshair_failure_logged = False
    _crosshair_strategy = "visibility-event"
    _crosshair_visibility_available = _function_exists(CROSSHAIR_VISIBILITY)

    _clear_crosshair_widget()
    _release_crosshair_visibility()
    _reset_runtime_motion()

    try:
        _cache_local(get_pc(possibly_loading=True))
    except Exception:
        _cache_local(None)

    pc = _pc()
    _gate_sprinting = bool(pc is not None and pc.bInSprintState)
    _weapon_address = (
        0
        if pc is None or pc.Pawn is None or pc.Pawn.Weapon is None
        else pc.Pawn.Weapon._get_address()
    )

    _active = True
    _precision.intent_reset(_armed)
    # Stable Python-safe gate: do not patch PlayerInput.bCanSprint. That field is
    # shared with crouch logic, so omnidirectional eligibility stays on the narrow
    # InputAllowsSprint hook.
    _set_native_hipfire(_cfg_hip_fire)
    if (
        _cfg_assist
        and _armed
        and pc is not None
        and pc.PlayerInput is not None
        and not pc.bInSprintState
    ):
        _assist_recover_or_schedule(pc)
    _sync_feature_hooks()
    _coyote_seed(pc)

    if _cfg_assist and any(
        mod.name == "Sprint Set-and-Forget" and mod.is_enabled
        for mod in get_ordered_mod_list()
    ):
        logging.warning(
            "omnimovement: disable Sprint Set-and-Forget or turn Sprint Assist off; "
            "both control sprint intent."
        )

    logging.info(
        "omnimovement 1.2.0 enabled: gate=python-hook/native-optional"
        + "; movement=event-reversal"
        + "; precision=" + str(_cfg_precision)
        + "; precision strength=" + str(int(_cfg_precision_strength * 100)) + "%"
        + "; C++ movement selected=" + str(_cfg_native_precision)
        + "; direction="
        + _cfg_direction
        + "; center grace="
        + str(int(_cfg_gap * 1000))
        + "ms; baseline="
        + str(_cfg_baseline)
        + "; begin sprint while firing=" + str(_active_fire_entry)
        + "; hip-fire="
        + str(_cfg_hip_fire)
        + ("(native-continue)" if _native_hipfire_active else "(python-fallback)" if _cfg_hip_fire else "")
        + "; crosshair="
        + str(_cfg_crosshair)
        + "; coyote=" + str(_coyote_ready)
        + "; coyote window=" + str(int(_cfg_coyote_window * 1000)) + "ms"
    )


def on_disable():
    """Retire runtime work even if one engine-facing cleanup operation fails."""
    global _requested_fire_entry
    global _active, _sprint_depth, _crosshair_weapon_address, _gate_sprinting
    global _crosshair_modal_open
    global _deathtrap_skill_active, _deathtrap_skill_ref, _deathtrap_context_seen
    global _coyote_ready, _coyote_attempt, _coyote_traveling

    # Inactivate callbacks first. A remaining SDK hook from a failed removal
    # must not continue processing player input while teardown proceeds.
    _active = False
    _crosshair_modal_open = False
    _coyote_ready = False
    _coyote_attempt = None
    _coyote_traveling = False
    _deathtrap_skill_active = False
    _deathtrap_skill_ref = None
    _deathtrap_context_seen = False
    _gate_sprinting = False

    def cleanup(label, operation, *args):
        try:
            operation(*args)
            return True
        except Exception as exc:
            try:
                logging.warning("omnimovement: disable cleanup " + label
                                + " failed: " + repr(exc))
            except Exception:
                pass
            return False

    pc = None
    try:
        pc = _pc()
    except Exception as exc:
        try:
            logging.warning("omnimovement: disable controller lookup failed: " + repr(exc))
        except Exception:
            pass

    try:
        # Independent cleanup boundaries: a stale first-person animation tree
        # cannot skip native sprint restoration, motion state or SDK hook removal.
        cleanup("sprint-fire-entry", _stop_sprint_fire_entry_probe)
        cleanup("crosshair-resync", _stop_crosshair_resync)
        cleanup("crosshair-visibility", _release_crosshair_visibility)
        cleanup("native-hipfire", _set_native_hipfire, False)
        cleanup("fire-hooks", _sync_fire_hooks, False)
        cleanup("weapon-firing-end", _sync_weapon_firing_end_hook, False)
        cleanup("fire-pose", _fire_pose.restore)
        cleanup("baseline", _restore_baseline)
        cleanup("travel-recovery", _stop_travel_recovery)
        cleanup("sprint-assist", _clear_assist)
        if not cleanup("runtime-motion", _reset_runtime_motion):
            # _reset_runtime_motion normally resets this native speed owner;
            # preserve that safety step if another part of the reset failed.
            cleanup("native-precision", _precision.reset)
        cleanup("crosshair-widget", _clear_crosshair_widget)
        cleanup("weapon-state-hooks", _remove_weapon_state_hooks)
        cleanup("remaining-hooks", _remove_all_hooks)

        def finish_local_sprint():
            if pc is not None and pc.Pawn is not None and pc.bInSprintState:
                with unrealsdk.hooks.prevent_hooking_direct_calls():
                    pc.EndSprint()

        cleanup("local-sprint", finish_local_sprint)
    finally:
        # Keep final state normalization independent even if logging or engine
        # cleanup reports an unexpected exception.
        _sprint_depth = 0
        _requested_fire_entry = False
        _crosshair_weapon_address = 0
        cleanup("local-cache", _cache_local, None)


class OmniMovementMod(Mod):
    def enable(self):
        """Keep SDK Enabled consistent when mandatory startup hooks cannot install."""
        try:
            super().enable()
        except Exception as exc:
            logging.warning("omnimovement: enable failed; restoring SDK Disabled state: " + repr(exc))
            if self.is_enabled:
                # mods_base sets is_enabled before calling on_enable. Its
                # disable path invokes on_disable and releases active hooks.
                try:
                    self.disable(dont_update_setting=True)
                except Exception as cleanup_exc:
                    logging.warning("omnimovement: activation cleanup failed: " + repr(cleanup_exc))


from ._settings_migration import migrate_settings
migrate_settings(SETTINGS_DIR)
mod = build_mod(
    cls=OmniMovementMod,
    settings_file=SETTINGS_DIR / "OmniMovement.json",
    options=[
        omni,
        assist,
        starting,
        direction,
        gap_window,
        coyote_time,
        coyote_window,
        hip_fire,
        begin_fire_sprint,
        crosshair,
        baseline,
        precision,
        native_precision,
        precision_strength,
    ],
    hooks=[],
    on_enable=on_enable,
    on_disable=on_disable,
)


def _iter_player_options():
    """Presentation-only groups; mod.options remains flat for saved settings."""
    yield GroupedOption("Sprint & Movement", [omni, assist, starting, direction, gap_window])
    yield GroupedOption("Jumping", [coyote_time, coyote_window])
    yield GroupedOption("Combat", [hip_fire, begin_fire_sprint, baseline, crosshair])
    yield GroupedOption("Controller & Backend", [precision, precision_strength, native_precision])


mod.iter_display_options = _iter_player_options
