"""Temporary in-pipeline recovery for held keys/sticks across UE3 travel.

A POST of WillowPlayerInput.PreProcessInput runs AFTER RawJoy snapshot but
BEFORE native PIProcessInput. WillowPlayerInput.PlayerInput PRE was too early.
The base helper hook is part of the normal input pipeline, not a late fix. The destination
may also lose native input-axis events, so its RawJoyUp/aBaseY cannot be used
as proof of physically held controller input. Windows XInput is an independent
read of the controller (never a write/injection into the OS).
"""
from __future__ import annotations

import math
import time


class HeldInputHandoff:
    def __init__(self, *, key_down=None, controller_forward=None):
        self.key_down = self._win_key_down() if key_down is None else key_down
        self.controller_forward = (
            self._xinput_forward() if controller_forward is None else controller_forward
        )
        self.reset()

    @staticmethod
    def _win_key_down():
        try:
            import ctypes
            import os
            if os.name != 'nt':
                return None
            api = ctypes.WinDLL('user32', use_last_error=True)
            key = api.GetAsyncKeyState
            key.argtypes = (ctypes.c_int,)
            key.restype = ctypes.c_short
            foreground = api.GetForegroundWindow
            foreground.argtypes = ()
            foreground.restype = ctypes.c_void_p
            get_pid = api.GetWindowThreadProcessId
            get_pid.argtypes = (ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong))
            get_pid.restype = ctypes.c_ulong

            process_id = os.getpid()

            def focused():
                hwnd = foreground()
                if not hwnd:
                    return False
                pid = ctypes.c_ulong()
                get_pid(hwnd, ctypes.byref(pid))
                return pid.value == process_id

            def read_keys(keys):
                if not focused():
                    return tuple(False for _ in keys)
                return tuple(bool(key(vk) & 0x8000) for vk in keys)

            def held(vk):
                return read_keys((vk,))[0]

            # One current focus/PID sample per movement observation. Custom
            # readers without this optional method retain the original calls.
            held.sample_movement = lambda: read_keys((0x57, 0x53))
            held.focused = focused
            return held
        except Exception:
            return None

    @staticmethod
    def _xinput_forward():
        """Read one unambiguous XInput controller's left Y without OS injection.

        Do not pick a different gamepad if multiple are connected. An optional
        secondary tool/controller must not accidentally drive player one.
        """
        try:
            import ctypes
            if not hasattr(ctypes, 'WinDLL'):
                return None
            class Gamepad(ctypes.Structure):
                _fields_ = [('buttons', ctypes.c_ushort),
                            ('left_trigger', ctypes.c_ubyte),
                            ('right_trigger', ctypes.c_ubyte),
                            ('left_x', ctypes.c_short),
                            ('left_y', ctypes.c_short),
                            ('right_x', ctypes.c_short),
                            ('right_y', ctypes.c_short)]
            class State(ctypes.Structure):
                _fields_ = [('packet', ctypes.c_ulong), ('pad', Gamepad)]
            get_state = None
            for dll in ('xinput1_4', 'xinput1_3', 'xinput9_1_0'):
                try:
                    lib = ctypes.WinDLL(dll, use_last_error=True)
                    get_state = lib.XInputGetState
                    get_state.argtypes = (ctypes.c_ulong, ctypes.POINTER(State))
                    get_state.restype = ctypes.c_ulong
                    break
                except (OSError, AttributeError):
                    continue
            if get_state is None:
                return None

            free_states = []

            def poll_axes():
                # Buffers are leased only for an invocation. Nested calls must
                # allocate/lease another buffer; all slots are still checked so
                # hotplug and multi-controller ambiguity remain observable.
                state = free_states.pop() if free_states else State()
                try:
                    value = None
                    for slot in range(4):
                        if get_state(slot, ctypes.byref(state)) == 0:
                            if value is not None:
                                return None
                            value = tuple(max(-1.0, min(1.0, float(axis) / 32767.0))
                                          for axis in (state.pad.left_y, state.pad.left_x))
                    if value is None:
                        return None
                    return value
                finally:
                    if len(free_states) < 4:
                        free_states.append(state)
            def poll():
                axes = poll_axes()
                return None if axes is None else axes[0]

            poll.sample_axes = poll_axes
            return poll
        except Exception:
            return None

    def reset(self):
        self.pi_address = 0
        self.bound = None
        self.synthetic = False
        self.synthetic_kind = None
        self.synthetic_value = 0.0
        # Each property is independent: (original, last value written).
        # No strong PlayerInput/UObject references are retained.
        self._axis_owners = {}
        self._pending_recovery = False
        self.last_sample_at = -1.0
        self.last_source = 'none'
        self.last_physical = 0.0
        self.last_error = ''

    def _w_bound(self, pi):
        if self.bound is None:
            try:
                binding = pi.GetBind('W')
                # UnrealSDK returns (command, out Key) for GetBind. An empty
                # command is still unresolved; the echoed key is not a bind.
                if isinstance(binding, (tuple, list)):
                    binding = binding[0] if binding else None
                s = '' if binding is None else str(binding).lower().strip()
                # Some UnrealSDK builds return an empty GetBind result for
                # config aliases. Treat an unresolvable binding as BL2's stock
                # W mapping, but never override an explicit non-forward bind.
                self.bound = (not s or 'moveforward' in s or 'abasey' in s)
            except Exception:
                # Default BL2 mapping; physical key and focused game still
                # required on every sample. Never fake a key press.
                self.bound = True
        return self.bound

    def _physical(self, pi):
        keys = None
        try:
            if self.key_down is not None:
                bound = self._w_bound(pi)
                sample = getattr(self.key_down, 'sample_movement', None)
                if sample is not None:
                    keys = sample()
                if bound and (keys[0] if keys is not None else self.key_down(0x57)):
                    return 'keyboard', 1.0
        except Exception as e:
            self.last_error = 'keyboard-' + type(e).__name__
        try:
            ly = None if self.controller_forward is None else self.controller_forward()
        except Exception as e:
            self.last_error = 'xinput-' + type(e).__name__
            ly = None
        if ly is not None:
            if ly > 0.40:
                backward = keys[1] if keys is not None else bool(
                    self.key_down and self.key_down(0x53))
                if not backward:
                    return 'controller', min(1.0, max(0.0, float(ly)))
            # Physical XInput is alive but NOT held forward; do not infer a
            # held stick from UE3's RawJoyUp (a processed, stale snapshot).
            return 'none', 0.0
        return 'none', 0.0

    @staticmethod
    def _axis(pi, prop):
        """Do not treat an unreadable reflected field as zero for ownership."""
        value = float(getattr(pi, prop))
        if not math.isfinite(value):
            raise ValueError("invalid axis value: " + prop)
        return value

    @staticmethod
    def _matches(left, right):
        return abs(left - right) < 0.025

    def _update_ownership(self):
        self.synthetic = bool(self._axis_owners)
        if not self.synthetic:
            self.synthetic_kind = None
            self.synthetic_value = 0.0
            self._pending_recovery = False

    def _release_owned(self, pi):
        """Restore only the individual fields still holding our exact value.

        A new/native value is never overwritten. Failed reads and writes retain
        the owning entry for a later release event, not a blind retry.
        """
        for prop in ("RawJoyUp", "RawJoyRight", "aBaseY", "aStrafe"):
            owner = self._axis_owners.get(prop)
            if owner is None:
                continue
            original, injected = owner
            try:
                current = self._axis(pi, prop)
            except Exception as exc:
                self.last_error = "axis-restore-read-" + type(exc).__name__
                continue
            if not self._matches(current, injected):
                # Another owner or the engine already replaced our write.
                self._axis_owners.pop(prop, None)
                continue
            try:
                setattr(pi, prop, original)
            except Exception as exc:
                self.last_error = "axis-restore-write-" + type(exc).__name__
            # A write may commit before raising. Only verified readback counts.
            try:
                after = self._axis(pi, prop)
            except Exception as exc:
                self.last_error = "axis-restore-check-" + type(exc).__name__
                continue
            if self._matches(after, original):
                self._axis_owners.pop(prop, None)
            elif not self._matches(after, injected):
                # A third party took ownership during restoration.
                self._axis_owners.pop(prop, None)
        self._update_ownership()
        return not self.synthetic

    def _sample_axis(self, pi, prop):
        current = self._axis(pi, prop)
        owner = self._axis_owners.get(prop)
        if owner is not None and not self._matches(current, owner[1]):
            self._axis_owners.pop(prop)
        return current

    def _write_axis(self, pi, prop, physical, current):
        owner = self._axis_owners.get(prop)
        if owner is not None and not self._matches(current, owner[1]):
            # The readback changed since our earlier write; it is not ours.
            self._axis_owners.pop(prop, None)
            owner = None
        if self._matches(current, physical):
            return  # Native input is already correct; no new ownership.
        if owner is None:
            if abs(current) > 0.1:
                raise ValueError("native input already owns " + prop)
            original = current
        else:
            original = owner[0]
        # Ownership must precede the reflected assignment: a setter can write
        # the field and THEN raise. Rollback will check the observed value.
        self._axis_owners[prop] = (original, physical)
        self.synthetic = True
        setattr(pi, prop, physical)
        if not self._matches(self._axis(pi, prop), physical):
            raise RuntimeError("axis write not confirmed: " + prop)

    def activation_gamepad_active(self, pi):
        """Check the game's local input mode and the same focus guard as travel."""
        focused = getattr(self.key_down, "focused", None)
        try:
            return bool(focused is not None and focused() and pi.bUsingGamepad)
        except Exception:
            return False

    def activation_stick_sample(self, pi):
        """Return a fresh independent controller sample only in gamepad mode."""
        sample = getattr(self.controller_forward, "sample_axes", None)
        try:
            return sample() if sample is not None and self.activation_gamepad_active(pi) else None
        except Exception:
            return None

    def bridge_activation_frame(self, pi, axes):
        """Fill missing movement axes for one frame before PIProcessInput."""
        if (self.synthetic or self._axis_owners or len(axes) != 2
                or not all(math.isfinite(axis) and abs(axis) <= 1.25 for axis in axes)):
            return False
        props = ("aBaseY", "aStrafe", "RawJoyUp", "RawJoyRight")
        try:
            before = dict((prop, self._axis(pi, prop)) for prop in props)
            missing = []
            live_axis = False
            for processed, raw, held in (("aBaseY", "RawJoyUp", axes[0]),
                                         ("aStrafe", "RawJoyRight", axes[1])):
                live = [before[prop] for prop in (processed, raw)
                        if abs(before[prop]) > 0.015]
                if live:
                    live_axis = True
                    # An actual input on either field owns this entire axis.
                    # If it disagrees with the captured stick, do not complete
                    # a diagonal the player may no longer be holding.
                    if any(value * held <= 0 or abs(value - held) > 0.25 for value in live):
                        return False
                else:
                    missing.append((processed, raw, held))
            writes = [(prop, held) for processed, raw, held in missing
                      if not live_axis or abs(held) >= 0.12 for prop in (processed, raw)]
            if not writes:
                return False
            addr = pi._get_address()
            if addr != self.pi_address:
                self.pi_address, self.bound = addr, None
            for prop, value in writes:
                original = before[prop]
                if self._axis(pi, prop) != original:
                    raise RuntimeError("native input changed during activation handoff")
                self._write_axis(pi, prop, value, original)
        except Exception as exc:
            self.last_error = "activation-axis-write-" + type(exc).__name__
            self._pending_recovery = not self._release_owned(pi)
            return False
        # The normal input pipeline consumes these per-frame scalars after
        # this hook and repopulates them on the next frame.
        self._axis_owners.clear()
        self._update_ownership()
        return True

    def apply_pre(self, pi, ready, *, keyboard_only=False):
        """Run POST WillowPlayerInput.PreProcessInput, before native PIProcessInput."""
        addr = pi._get_address()
        if addr != self.pi_address:
            # Never try to restore a stale generation through a new PlayerInput.
            self.pi_address = addr
            self.bound = None
            self._axis_owners.clear()
            self._pending_recovery = False
            self._update_ownership()
        self.last_sample_at = time.monotonic()
        source, physical = self._physical(pi) if ready else ('none', 0.0)
        if keyboard_only:
            try:
                if source != 'keyboard' or self.key_down is None or self.key_down(0x53):
                    source, physical = 'none', 0.0
            except Exception:
                source, physical = 'none', 0.0
        self.last_source = source
        self.last_physical = physical

        # Release independently on physical key/stick release, menu, vehicle
        # entry, or when an earlier write transaction is not fully recovered.
        if not ready or not physical or self._pending_recovery:
            clean = self._release_owned(pi)
            self._pending_recovery = not clean
            if not ready or not physical or not clean:
                return False

        try:
            base = self._sample_axis(pi, 'aBaseY')
            raw = self._sample_axis(pi, 'RawJoyUp')
        except Exception as exc:
            self.last_error = 'axis-read-' + type(exc).__name__
            return False
        finally:
            self._update_ownership()

        # Native movement takes precedence. In particular, never replace a
        # live RawJoyUp value which cannot be proven to be ours.
        if ((abs(base) > 0.1 and 'aBaseY' not in self._axis_owners)
                or (abs(raw) > 0.1 and 'RawJoyUp' not in self._axis_owners)):
            # Native movement owns one field. Do not inject into it, but retain
            # a still-owned value on the other axis until physical release.
            # Dropping both ledgers here would orphan a synthetic RawJoyUp.
            return False

        if self._matches(base, physical) and self._matches(raw, physical):
            return self.synthetic

        try:
            self._write_axis(pi, 'aBaseY', physical, base)
            # The first reflected setter can change the other axis.
            self._write_axis(pi, 'RawJoyUp', physical, self._axis(pi, 'RawJoyUp'))
        except Exception as exc:
            self.last_error = 'axis-write-' + type(exc).__name__
            self._pending_recovery = not self._release_owned(pi)
            return False

        self._update_ownership()
        self.synthetic_kind = source if self.synthetic else None
        self.synthetic_value = physical if self.synthetic else 0.0
        return self.synthetic
