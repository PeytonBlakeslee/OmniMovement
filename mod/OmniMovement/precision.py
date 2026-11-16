"""Opt-in controller sprint response. No writes to input/deadzone definitions.

This is intentionally a small, reversible experimental *native speed-scale*
intervention, not a claimed fix for acceleration or stopping distance. It
piggybacks Omni's existing InputAllowsSprint callback; no extra hot hook.
"""
from __future__ import annotations

import math

import unrealsdk
from unrealsdk import logging


class PrecisionSprint:
    def __init__(self):
        self._pawn = None
        self._original = None
        self._last_write = None
        self._pawn_address = 0
        self._blocked_address = 0
        self._warned = False

    def restore(self):
        pawn = None if self._pawn is None else self._pawn()
        if pawn is not None and self._last_write is not None:
            try:
                # Never undo a subsequent change made by the game or another mod.
                if math.isclose(float(pawn.SprintingPct), self._last_write,
                                rel_tol=0.0, abs_tol=0.0001):
                    pawn.SprintingPct = self._original
            except Exception:
                pass
        self._pawn = None
        self._original = None
        self._last_write = None
        self._pawn_address = 0

    def reset(self):
        self.restore()
        self._blocked_address = 0
        self._warned = False

    def update(self, pc, pi, *, enabled: bool, strength: float):
        if not enabled or pc is None or pi is None:
            self.restore()
            return
        try:
            # Check the cheap input/sprint gates before resolving the pawn.
            # In menus, on KBM, and when not sprinting, avoid all pawn reads.
            if not bool(pi.bUsingGamepad) or not pc.bInSprintState:
                self.restore()
                return
            pawn = pc.Pawn
            if (pawn is None or not pawn.bIsSprinting
                    or int(pawn.Physics) != 1 or pawn.bIsCrouched
                    or pc.bCinematicMode or pc.bIgnoreMoveInput):
                self.restore()
                return
            addr = pawn._get_address()
            if addr == self._blocked_address:
                return
            # Engine.PlayerInput snapshots these two analog components. The
            # current radial backend transforms them in Willow.PreProcessInput;
            # the native/axial paths keep their own filtering upstream. Read the
            # effective pair rather than the separate Deadzone Control mod.
            x = float(pi.RawJoyRight)
            y = float(pi.RawJoyUp)
            if not (math.isfinite(x) and math.isfinite(y)):
                self.restore()
                return
            radius = min(1.0, math.hypot(x, y))
            if radius >= 0.998 or strength <= 0.0:
                self.restore()
                return
            # Do not alternate the native speed scale between 100% and the
            # precision floor when an unfiltered/no-deadzone stick jitters
            # between exactly zero and a tiny nonzero value. While sprint is
            # still active, zero input belongs on the same continuous curve.
            # EndSprint and every context exit still restore the original.
            if radius < 0.005:
                radius = 0.0
            # Pawn ownership is established on transitions, rather than
            # resolving a WeakPointer on every hot input callback. A weak
            # reference remains the only handle used for restoration.
            if self._pawn_address != addr:
                self.restore()
                original = float(pawn.SprintingPct)
                if not 0.05 <= original <= 5.0:
                    return
                self._pawn = unrealsdk.unreal.WeakPointer(pawn)
                self._pawn_address = addr
                self._original = original

            # Keep exactly the v0.15.32/33 response curve and write tolerance.
            shaped = 0.35 + 0.65 * radius ** 1.5
            target = self._original * (1.0 - strength * (1.0 - shaped))
            if self._last_write is None or abs(target - self._last_write) >= 0.005:
                # Read the reflected value only when intending to write.
                # Otherwise leave it untouched. Every write remains protected
                # by the ownership check; restore() independently checks the
                # current value and won't overwrite another mod's edit.
                if self._last_write is not None and not math.isclose(
                    float(pawn.SprintingPct), self._last_write,
                    rel_tol=0.0, abs_tol=0.0001
                ):
                    self.restore()
                    self._blocked_address = addr
                    if not self._warned:
                        self._warned = True
                        logging.warning('omnimovement: precision suspended: SprintingPct ownership changed')
                    return
                pawn.SprintingPct = target
                self._last_write = target
        except Exception:
            # Fail closed and restore when native state cannot be reflected.
            self.restore()
