"""Supervised native movement/intent with an independent Python fallback.

No additional hook is installed. SprintingPct ownership is separate from motion
observation and correction writes; only one backend owns each output boundary.
"""
from __future__ import annotations

import math
from unrealsdk import logging
from .precision import PrecisionSprint as PythonPrecisionSprint


class PrecisionSprint:
    def __init__(self):
        self._python = PythonPrecisionSprint()
        self._native = None
        self._import_failed = False
        self._native_quarantined = False
        self._native_active = False
        self._native_restore_warned = False
        self._native_gate_failed = False
        self._native_reversal_failed = False
        self._native_center_failed = False
        self._native_velocity_failed = False
        self._native_assist_failed = False
        self._native_preflight_failed = False
        self._native_snapshot_failed = False
        self._native_motion_fusion_failed = False
        self._native_travel_failed = False
        self._native_observer_failed = False
        self._native_combat_failed = False
        self._native_intent_failed = False

    def _restore_native(self):
        pending = False
        if self._native is not None:
            try:
                pending = self._native.restore() is False
            except Exception as exc:
                pending = True
                if not self._native_restore_warned:
                    self._native_restore_warned = True
                    logging.warning(f'omnimovement: native restore failed: {exc!r}')
        self._native_active = pending
        return not pending

    def _try_native(self):
        if self._native_quarantined or self._import_failed:
            return None
        if self._native is None:
            try:
                from . import omni_precision_backend
                if omni_precision_backend.backend_version() != 'precision-native-stage29-velocity-guard-20261111':
                    raise RuntimeError('unrecognized native backend version')
                self._native = omni_precision_backend
                logging.info('omnimovement: native movement backend loaded; Python fallback available')
            except Exception as exc:
                self._import_failed = True
                logging.warning(f'omnimovement: native backend unavailable; using Python Precision Sprint: {exc!r}')
                return None
        return self._native

    def update(self, pc, pi, *, enabled: bool, strength: float,
               prefer_native: bool = False):
        if self._native_active and self._native_quarantined:
            if not self._restore_native():
                return  # Native speed value still has an unresolved owner.
        if prefer_native:
            native = self._try_native()
            if native is not None:
                try:
                    if not self._native_active:
                        # A settings switch must not leave the Python owner
                        # writing the same pawn while native takes over.
                        self._python.restore()
                    pc_addr = 0 if pc is None else int(pc._get_address())
                    pi_addr = 0 if pi is None else int(pi._get_address())
                    if native.update(pc_addr, pi_addr, bool(enabled), float(strength)):
                        self._native_active = True
                        return
                    raise RuntimeError('native Precision update rejected')
                except Exception as exc:
                    restored = self._restore_native()
                    self._native_quarantined = True
                    logging.warning(
                        'omnimovement: native Precision Sprint disabled for session; '
                        + ('falling back to Python: ' if restored else
                           'native speed restore pending, Python writes paused: ')
                        + repr(exc)
                    )
                    if not restored:
                        return
        elif self._native_active:
            if not self._restore_native():
                return
        return self._python.update(pc, pi, enabled=enabled, strength=strength)

    def motion_fused(self, pc, pi, epoch, *, precision_enabled, strength,
                     center_grace, center_enabled=False, reversal_enabled=False,
                     correction_active=False, owner_input=None, input_address=None,
                     pawn=None, owner_input_address=None, compact_gate=False):
        """One native movement snapshot, independently of Precision ownership.

        Vanilla may request v1 gate flags instead of axes and observations.
        None selects the established split/Python paths. On failure, release
        speed and reset partially observed motion before another owner runs.
        """
        if (self._native_motion_fusion_failed or self._native_gate_failed
                or self._native_reversal_failed or self._native_quarantined):
            return None
        if (pc is not None and pi is not None and pawn is not None
                and owner_input is not None and input_address is not None
                and owner_input_address is not None):
            # _allows already read the live pawn and compared the input.
            # Native read_motion validates the current pawn and its controller.
            pc_address = pc._get_address()
            if (not pc_address or not input_address
                    or input_address != owner_input_address or epoch < 0):
                self.reset_motion()
                return None
        else:
            context = self._intent_context(
                pc, pi, epoch, pawn=pawn, owner_input=owner_input,
                input_address=input_address, owner_input_address=owner_input_address)
            if context is None:
                self.reset_motion()
                return None
            pc_address, input_address, _, epoch = context
        native = self._try_native()
        if native is None:
            return None
        try:
            if precision_enabled and not self._native_active:
                self._python.restore()
            compact_update = (getattr(native, 'motion_gate_v1', None)
                              if compact_gate and not (center_enabled or reversal_enabled)
                              else None)
            if compact_update is not None:
                flags = compact_update(pc_address, input_address, epoch,
                    bool(precision_enabled), float(strength), float(center_grace))
                if type(flags) is not int or not 0 <= flags <= 7 or flags & 3 == 1:
                    raise RuntimeError('invalid native compact movement gate')
                if precision_enabled:
                    self._native_active = True
                elif self._native_active:
                    self._restore_native()
                return flags
            result = native.motion_update(pc_address, input_address, epoch,
                bool(precision_enabled), float(strength), float(center_grace),
                bool(center_enabled), bool(reversal_enabled), bool(correction_active))
            if (not isinstance(result, tuple) or len(result) != 4
                    or result[0] is not True or type(result[3]) is not bool):
                raise RuntimeError('invalid native motion result')
            _, gate, scan, changed = result
            if (not isinstance(gate, tuple) or len(gate) != 6
                    or type(gate[2]) is not bool or type(gate[3]) is not bool
                    or type(gate[4]) is not bool or type(gate[5]) is not bool
                    or not math.isfinite(float(gate[0]))
                    or not math.isfinite(float(gate[1]))):
                raise RuntimeError('invalid native movement gate')
            moving = gate[0] * gate[0] + gate[1] * gate[1] > 0.0001
            if gate[2] != moving or (moving and not gate[3]) or (gate[5] and not moving):
                raise RuntimeError('inconsistent native movement gate')
            if scan is not None:
                if (not isinstance(scan, tuple) or len(scan) != 3
                        or type(scan[0]) is not bool or type(scan[1]) is not bool
                        or type(scan[2]) is not bool
                        or not moving or gate[5] or not reversal_enabled
                        or (scan[1] and (not scan[0] or scan[2]))):
                    raise RuntimeError('native reversal observation out of context')
            if precision_enabled:
                self._native_active = True
            elif self._native_active:
                self._restore_native()
            return gate, scan, changed
        except Exception as exc:
            self._native_motion_fusion_failed = True
            self._restore_native()
            self.reset_motion()
            logging.warning('omnimovement: native motion pipeline unavailable; '
                            'using established split/Python paths: ' + repr(exc))
            return None

    def reset_motion(self):
        if self._native is not None:
            try:
                self._native.motion_reset()
            except Exception:
                self._native_motion_fusion_failed = True



    def reset_travel(self):
        """Drop native destination-generation state on travel or disable."""
        native = self._native
        if native is not None:
            try:
                native.travel_reset()
            except Exception:
                self._native_travel_failed = True

    def travel_gate(self, epoch, *, ready, modal_open, close_pending,
                    sprinting, armed, now, prefer_native=False):
        """Native generation/readiness/retry pacing; gameplay stays in Python.

        Returns (destination_ready, rearm_due, six_frame_close_settled).
        Caller still validates local PC, on-foot state and CanSprint.
        """
        if not prefer_native or self._native_travel_failed:
            return None
        native = self._try_native()
        if native is None:
            return None
        try:
            state = native.travel_step(int(epoch), bool(ready),
                bool(modal_open), bool(close_pending), bool(sprinting),
                bool(armed), float(now))
            if (not isinstance(state, tuple) or len(state) != 3
                    or any(type(x) is not bool for x in state)
                    or (state[1] and (not state[0] or not armed or sprinting))
                    or (state[2] and (not state[0] or not close_pending))):
                raise RuntimeError('unexpected travel gate response')
            return state
        except Exception as exc:
            self._native_travel_failed = True
            self.reset_travel()
            logging.warning('omnimovement: native travel gate unavailable; '
                            'using Python readiness: ' + repr(exc))
            return None

    def combat_observe(self, pc, pi, *, retry=False, weapon_transitioning=False,
                       prefer_native=False, pawn=None, recovery_only=False):
        """Read combat once; retry status remains a candidate for CanSprint.

        Weapon state functions and IsEquipping overrides stay in the Python
        supervisor. No native read failure can fabricate an unblocked frame.
        This read-only path does not require Precision speed ownership.
        """
        if (not prefer_native or self._native_combat_failed
                or self._native_quarantined or pc is None or pi is None
                or (pc.Pawn if pawn is None else pawn) is None):
            return None
        native = self._try_native()
        if native is None:
            return None
        try:
            addresses = (int(pc._get_address()), int(pi._get_address()))
            snapshot_size = 8
            if retry:
                result = native.combat_retry_observe(*addresses, bool(weapon_transitioning))
                if result is None or len(result) != 2 or type(result[0]) is not int:
                    raise RuntimeError('invalid combat retry result')
                status, snapshot = result
                if status not in (0, 1, 2, 3, 4):
                    raise RuntimeError('invalid combat retry status')
            else:
                observe = getattr(native, 'assist_combat_v1', None) if recovery_only else None
                if observe is None:
                    observe = native.combat_snapshot
                else:
                    snapshot_size = 6
                status, snapshot = None, observe(*addresses)
            if (snapshot is None or len(snapshot) != snapshot_size
                    or any(type(value) is not bool for value in snapshot)):
                raise RuntimeError('invalid combat snapshot')
            snapshot = tuple(snapshot)
            if retry:
                blocked = any(snapshot[i] for i in (1, 2, 4, 5)) or weapon_transitioning
                if ((status == 0) != snapshot[0]
                        or (not snapshot[0] and ((status == 1) != (not snapshot[3])))
                        or (not snapshot[0] and snapshot[3] and ((status == 2) != bool(blocked)))):
                    raise RuntimeError('inconsistent combat retry observation')
            return status, snapshot
        except Exception as exc:
            self._native_combat_failed = True
            self.reset_assist('retry')
            logging.warning('omnimovement: native combat observation unavailable; '
                            'using Python combat guards: ' + repr(exc))
            return None

    @staticmethod
    def _intent_context(pc, pi, epoch, *, pawn=None, owner_input=None,
                        input_address=None, owner_input_address=None):
        if pc is None or pi is None:
            return None
        pawn = pc.Pawn if pawn is None else pawn
        if pawn is None:
            return None
        owner_input = pc.PlayerInput if owner_input is None else owner_input
        if owner_input is None:
            return None
        # Reuse only inside this validation; do not retain engine owners.
        # Optional snapshots belong to this callback only. Callers obtain them
        # from current engine fields before any engine call; they are never
        # retained. Native movement/writes also validate their own live owners.
        addresses = (int(pc._get_address()),
                     int(pi._get_address() if input_address is None else input_address),
                     int(pawn._get_address()), int(epoch))
        if not addresses[0] or not addresses[1] or not addresses[2] or addresses[3] < 0:
            return None
        if owner_input_address is None:
            owner_input_address = owner_input._get_address()
        if int(owner_input_address) != addresses[1]:
            return None
        return addresses

    def _intent_backend(self, prefer_native):
        if not prefer_native or self._native_intent_failed or self._native_quarantined:
            return None
        return self._try_native()

    def intent_available(self, *, prefer_native=False):
        """Avoid full recovery preparation after a known intent failure.

        Availability grants no gameplay permission and performs no engine
        writes. The final observation retains current owner validation.
        """
        return self._intent_backend(prefer_native) is not None

    def _intent_failure(self, exc, armed):
        self._native_intent_failed = True
        try:
            self._native.intent_reset(bool(armed))
        except Exception:
            pass
        logging.warning('omnimovement: native Sprint Assist unavailable; '
                        'using Python intent/recovery: ' + repr(exc))

    def intent_reset(self, armed):
        # Lifecycle/backend switches never import a backend merely to reset it.
        if self._native is None or self._native_intent_failed:
            return
        try:
            self._native.intent_reset(bool(armed))
        except Exception as exc:
            self._intent_failure(exc, armed)

    def intent_press(self, pc, pi, epoch, armed, *, prefer_native=False):
        native = self._intent_backend(prefer_native)
        if native is None:
            return None
        try:
            context = self._intent_context(pc, pi, epoch)
            if context is None:
                return None
            result = native.intent_press(*context, bool(armed))
            if (not isinstance(result, tuple) or len(result) != 2
                    or any(type(x) is not bool for x in result)
                    or result[0] != (not armed if result[1] else bool(armed))):
                raise RuntimeError('invalid native Sprint selection')
            return result
        except Exception as exc:
            self._intent_failure(exc, armed)
            return None

    def intent_release(self, pc, pi, epoch, armed, *, prefer_native=False):
        native = self._intent_backend(prefer_native)
        if native is None:
            return
        try:
            context = self._intent_context(pc, pi, epoch)
            if context is not None:
                native.intent_release(*context, bool(armed))
        except Exception as exc:
            self._intent_failure(exc, armed)

    def intent_observe(self, pc, pi, epoch, armed, *, context_ready, sprinting,
                       moving, grounded, permitted, prefer_native=False,
                       owner_input=None, pawn=None):
        native = self._intent_backend(prefer_native)
        if native is None:
            return None
        try:
            context = self._intent_context(pc, pi, epoch, owner_input=owner_input,
                                           pawn=pawn)
            if context is None:
                return None
            pending_try, pending_wants = bool(pi.bTryToSprint), bool(pc.bWantsToSprint)
            result = native.intent_observe(*context, bool(armed), bool(context_ready),
                bool(sprinting), bool(moving), bool(grounded), bool(permitted),
                pending_try, pending_wants)
            if (not isinstance(result, tuple) or len(result) != 5
                    or type(result[0]) is not int or not 0 <= result[0] <= 7
                    or type(result[1]) is not bool or type(result[2]) is not bool
                    or type(result[3]) is not bool or type(result[4]) is not bool):
                raise RuntimeError('invalid native intent observation')
            phase, remembered, request, write, airborne = result
            expected_phase = (0 if not armed else 1 if not context_ready
                              else 2 if sprinting else 7 if not grounded
                              else 3 if not moving else 4 if not permitted else None)
            if (remembered != bool(armed) or airborne != (phase == 7)
                    or request != (phase in (3, 6, 7))
                    or write != (pending_try != request)
                    or (expected_phase is not None and phase != expected_phase)
                    or (expected_phase is None and phase not in (5, 6))):
                raise RuntimeError('inconsistent native intent decision')
            return result
        except Exception as exc:
            self._intent_failure(exc, armed)
            return None


    def assist_summon_observe(self, pc, now, *, prefer_native=False):
        """Controller blocker + 50ms summons debounce in one native call.

        Python MUST have approved pawn.CanContinueSprinting and its own
        action/weapon/context gates before it treats a 4 as eligible.
        """
        if (not prefer_native or self._native_observer_failed
                or pc is None or self._intent_context(pc, pc.PlayerInput, 0) is None):
            return None
        native = self._try_native()
        if native is None:
            return None
        try:
            result = int(native.assist_summon_observe(
                int(pc._get_address()), float(now)))
            if result not in (0, 2, 3, 4):
                raise RuntimeError(f'native summon observer unavailable: {result}')
            return result
        except Exception as exc:
            self._native_observer_failed = True
            self.reset_assist('summon')
            logging.warning('omnimovement: native summon observer unavailable; '
                            'using Python recovery: ' + repr(exc))
            return None

    def assist_snapshot(self, pc, pi, *, prefer_native=False):
        """Optional cheap state read; never a substitute for CanSprint().

        (sprinting, native_action_blocker, pending_zoom, raw_moving).
        A missing native module, reflection error, or old ABI means Python
        uses all its original property reads and gameplay predicates.
        """
        if (not prefer_native or self._native_snapshot_failed
                or self._intent_context(pc, pi, 0) is None):
            return None
        native = self._try_native()
        if native is None:
            return None
        try:
            snapshot = native.assist_snapshot(
                int(pc._get_address()), int(pi._get_address()))
            if snapshot is None:
                # The native function was called with a verified local pair.
                # A missing tuple usually means unavailable reflection on
                # this build. Stop retrying the failing native read every
                # recovery frame; the Python path remains authoritative.
                self._native_snapshot_failed = True
                logging.warning('omnimovement: native recovery snapshot missing; '
                                'using Python recovery for this session')
                return None
            if len(snapshot) != 4:
                raise RuntimeError('unexpected native assist snapshot layout')
            return tuple(bool(v) for v in snapshot)
        except Exception as exc:
            self._native_snapshot_failed = True
            logging.warning('omnimovement: native recovery snapshot unavailable; '
                            'using Python fallback: ' + repr(exc))
            return None

    def gate_update(self, pi, center_grace: float, center_enabled: bool = False):
        """Native movement-axis/center-grace path; None means use Python gate.

        Native gate errors must not quarantine the separately working native
        Precision Sprint speed owner. One warning per session is sufficient.
        """
        if self._native_gate_failed:
            return None
        native = self._try_native()
        if native is None:
            return None
        try:
            return native.gate_update(int(pi._get_address()), float(center_grace), bool(center_enabled))
        except Exception as exc:
            self._native_gate_failed = True
            logging.warning(
                'omnimovement: native input gate unavailable; Python gate fallback: '
                + repr(exc)
            )
            try:
                native.gate_reset()
            except Exception:
                pass
            return None

    def reset_gate(self):
        if self._native is not None:
            try:
                self._native.gate_reset()
            except Exception as exc:
                self._native_gate_failed = True
                logging.warning('omnimovement: native gate reset failed: ' + repr(exc))

    def reversal_observe(self, fwd, side, magnitude_sq, correction_active):
        """Return (needs_kinematics, candidate, released), or None for Python."""
        if self._native_reversal_failed:
            return None
        native = self._try_native()
        if native is None:
            return None
        try:
            return native.reversal_observe(float(fwd), float(side),
                                           float(magnitude_sq),
                                           bool(correction_active))
        except Exception as exc:
            self._native_reversal_failed = True
            logging.warning(
                'omnimovement: native reversal observation unavailable; '
                'using Python: ' + repr(exc)
            )
            try:
                native.reversal_reset()
            except Exception:
                pass
            return None

    def reversal_apply(self, direction, velocity, cap, dt, instant, candidate):
        """Return (True, velocity_or_none), or None on native failure.

        Python already checked sprint/skill/weapon/movement state. This native
        call only computes the blend; the common guarded boundary owns the write.
        """
        if self._native_reversal_failed:
            return None
        native = self._try_native()
        if native is None:
            return None
        try:
            dx, dy = direction
            vx, vy = velocity
            correction = native.reversal_apply(
                float(dx), float(dy), float(vx), float(vy),
                float(cap), float(dt), bool(instant), bool(candidate),
            )
            return (True, correction)
        except Exception as exc:
            self._native_reversal_failed = True
            logging.warning(
                'omnimovement: native reversal blend unavailable; '
                'resetting correction to Python: ' + repr(exc)
            )
            try:
                native.reversal_reset()
            except Exception:
                pass
            return None

    def center_begin(self, direction, origin, cap):
        """(True, (speed, vx, vy) | None), or None to fall back to Python."""
        if self._native_center_failed:
            return None
        native = self._try_native()
        if native is None:
            return None
        try:
            dx, dy = direction
            ox, oy = origin
            return (True, native.center_begin(
                float(dx), float(dy), float(ox), float(oy), float(cap)))
        except Exception as exc:
            self._native_center_failed = True
            logging.warning(
                'omnimovement: native center begin failed; Python fallback: '
                + repr(exc))
            return None

    def center_blend(self, origin, target, speed, cap, elapsed):
        """(True, (vx,vy,finished) | None), or None for Python fallback."""
        if self._native_center_failed:
            return None
        native = self._try_native()
        if native is None:
            return None
        try:
            ox, oy = origin
            tx, ty = target
            return (True, native.center_blend(
                float(ox), float(oy), float(tx), float(ty),
                float(speed), float(cap), float(elapsed)))
        except Exception as exc:
            self._native_center_failed = True
            logging.warning(
                'omnimovement: native center blend failed; Python fallback: '
                + repr(exc))
            return None

    def retry_preflight_blocked(self, pc, *, prefer_native=False):
        """Return True only for confirmed native sprint blockers, else False/None.

        False means the native preflight found no *known* blocker; Python must
        still run full interruption/context/CanSprint checks. None means the
        native preflight was unavailable, so the full Python path is used.
        """
        if (not prefer_native or self._native_preflight_failed
                or pc is None or self._intent_context(pc, pc.PlayerInput, 0) is None):
            return None
        native = self._try_native()
        if native is None:
            return None
        try:
            value = int(native.retry_preflight(int(pc._get_address())))
            return True if value == 1 else (False if value == 0 else None)
        except Exception as exc:
            self._native_preflight_failed = True
            logging.warning('omnimovement: native retry preflight unavailable; '
                            'using Python eligibility: ' + repr(exc))
            return None

    def assist_ready(self, kind, eligible, now=None, *, prefer_native=False):
        """Pure native readiness only. All gameplay predicates remain in Python.

        None requests the unchanged Python debounce/time-window fallback. Native
        state follows the selected movement backend independently of speed ownership.
        """
        if not prefer_native or self._native_assist_failed:
            return None
        native = self._try_native()
        if native is None:
            return None
        try:
            if kind == 'retry':
                return bool(native.assist_retry_ready(bool(eligible)))
            if kind == 'summon':
                return bool(native.assist_summon_ready(bool(eligible), float(now)))
            if kind == 'travel':
                return bool(native.assist_travel_ready(bool(eligible)))
            raise ValueError('unrecognized recovery kind: ' + str(kind))
        except Exception as exc:
            self._native_assist_failed = True
            logging.warning('omnimovement: native assist readiness unavailable; '
                            'using Python fallback: ' + repr(exc))
            try:
                native.assist_reset(0)
            except Exception:
                pass
            return None

    def reset_assist(self, kind='all'):
        """Reset native readiness without importing the module or game objects."""
        if self._native is None or self._native_assist_failed:
            return
        values = {'all': 0, 'retry': 1, 'summon': 2, 'travel': 3}
        if kind not in values:
            raise ValueError('unknown assist readiness kind: ' + str(kind))
        try:
            self._native.assist_reset(values[kind])
        except Exception as exc:
            self._native_assist_failed = True
            logging.warning('omnimovement: native assist reset failed; '
                            'using Python fallback: ' + repr(exc))

    def velocity_write(self, pc, pi, pawn, epoch, vx, vy):
        """True committed, False rejected context, None guarded Python fallback.

        This is only called after Python's existing gameplay/context gates.
        Native checks current objects and motion generation, without relying
        on Precision's separate SprintingPct owner. Rejection forbids fallback.
        A write-path failure does not quarantine Precision Sprint's speed owner.
        """
        if self._native_velocity_failed or self._native_motion_fusion_failed:
            return None
        context = self._intent_context(pc, pi, epoch)
        if context is None or pawn is None or int(pawn._get_address()) != context[2]:
            return False
        native = self._try_native()
        if native is None:
            return None
        try:
            result = native.velocity_write(*context, float(vx), float(vy))
            if type(result) is int and result == -1:
                # Native WrappedStruct writes are live per-component. When a
                # commit phase raises, X or Y may already be changed; never
                # apply another Python correction to the same frame.
                self._native_velocity_failed = True
                logging.warning(
                    'omnimovement: native velocity write uncertain; '
                    'skipping same-frame Python fallback and disabling '
                    'native velocity writes for this session')
                return False
            if result is None:
                # Explicit native pre-write error; no X/Y commit was attempted.
                self._native_velocity_failed = True
                logging.warning(
                    'omnimovement: native velocity pre-write unavailable; '
                    'using guarded Python fallback')
                return None
            if type(result) is not bool:
                # A malformed result has unknown write provenance. Do not
                # assume nothing was written and double-apply the velocity.
                raise RuntimeError('invalid native velocity write response')
            return result
        except Exception as exc:
            # Only an explicit native None is certified pre-write. An exception
            # escaping the call could be post-commit, so fail closed this frame.
            self._native_velocity_failed = True
            logging.warning(
                'omnimovement: native velocity result uncertain; '
                'suppressing same-frame Python writes: ' + repr(exc))
            return False

    def reset_reversal(self):
        if self._native is not None and not self._native_reversal_failed:
            try:
                self._native.reversal_reset()
            except Exception as exc:
                self._native_reversal_failed = True
                logging.warning(
                    'omnimovement: native reversal reset failed; '
                    'using Python: ' + repr(exc)
                )

    def restore(self):
        if self._native_active:
            self._restore_native()
        self._python.restore()

    def reset(self):
        pending = False
        if self._native is not None:
            try:
                pending = self._native.reset() is False
            except Exception as exc:
                pending = True
                logging.warning('omnimovement: native reset failed: ' + repr(exc))
        self._native_active = pending
        if pending:
            self._native_quarantined = True
        self._python.reset()
