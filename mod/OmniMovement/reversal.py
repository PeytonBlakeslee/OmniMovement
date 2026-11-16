"""Lazy horizontal reversal correction, independent of the SDK."""
import math

VANILLA = "Vanilla"
RESPONSIVE = "Responsive"
INSTANT = "Near-instant"


class Reversal:
    """Track raw stick input cheaply; request kinematics only for a real reversal."""

    def __init__(self):
        self.reset()

    def reset(self):
        # previous is kept in raw stick-local space so the steady-state observer
        # needs no sqrt/division/normalization. previous_strength stores |input|^2.
        self.previous_fwd = None
        self.previous_side = None
        self.previous_strength = None
        self.origin = None
        self.elapsed = 0.0
        self.speed = 0.0
        self.target = None
        self.candidate = False

    def observe_raw(self, fwd, side, magnitude_sq, profile):
        """Return True only when an opposing input needs kinematic data."""
        if profile == VANILLA:
            self.reset()
            return False

        # Engine input is already numeric. NaN also fails `> 0.0`, so this one
        # comparison safely rejects zero/invalid magnitude without three C math
        # calls on every hot observation.
        if not magnitude_sq > 0.0:
            self.reset()
            return False

        previous_fwd = self.previous_fwd
        previous_side = self.previous_side
        old_magnitude_sq = self.previous_strength

        # The old observer treated a >50% magnitude drop as a release. Squared
        # magnitudes preserve the same threshold without sqrt/division each frame.
        release = (
            old_magnitude_sq is not None
            and magnitude_sq < old_magnitude_sq * 0.25
        )

        opposite = False
        if previous_fwd is not None and old_magnitude_sq is not None and old_magnitude_sq > 0.0:
            dot = fwd * previous_fwd + side * previous_side
            # Equivalent to normalized dot < -0.1, but avoids normalization:
            # dot < 0 and dot^2 > 0.01 * |a|^2 * |b|^2.
            opposite = (
                dot < 0.0
                and dot * dot > 0.01 * magnitude_sq * old_magnitude_sq
            )

        self.previous_fwd = fwd
        self.previous_side = side
        self.previous_strength = magnitude_sq

        if release:
            self.origin = None
            self.target = None
            self.candidate = False
            return False

        self.candidate = bool(opposite)
        return self.origin is not None or self.candidate

    def apply(self, direction, velocity, cap, dt, profile):
        """Apply/continue a reversal after the caller has fetched kinematic data."""
        if (
            direction is None
            or profile == VANILLA
            or dt <= 0.0
            or dt > 0.1
            or cap <= 0.0
        ):
            self.origin = None
            self.target = None
            self.candidate = False
            return None

        dx, dy = direction
        vx, vy = velocity
        if not (
            math.isfinite(dx)
            and math.isfinite(dy)
            and math.isfinite(vx)
            and math.isfinite(vy)
            and math.isfinite(cap)
            and math.isfinite(dt)
        ):
            self.origin = None
            self.target = None
            self.candidate = False
            return None

        speed = math.hypot(vx, vy)
        if speed > cap * 1.01:
            self.origin = None
            self.target = None
            self.candidate = False
            return None

        if self.candidate:
            self.candidate = False
            if speed > 1.0 and dx * vx + dy * vy < -speed * 0.1:
                self.origin = (vx, vy)
                self.speed = min(speed, cap)
                self.elapsed = 0.0
                self.target = (dx, dy)

        if self.origin is None:
            return None

        tx, ty = self.target
        if dx * tx + dy * ty < 0.8:
            self.origin = None
            self.target = None
            return None

        self.elapsed += dt
        blend = 1.0 if profile == INSTANT else min(1.0, self.elapsed / 0.120)
        target_speed = min(self.speed, cap)
        ox, oy = self.origin
        inv = 1.0 - blend
        rx = inv * ox + blend * dx * target_speed
        ry = inv * oy + blend * dy * target_speed

        magnitude = math.hypot(rx, ry)
        if magnitude > cap:
            scale = cap / magnitude
            rx *= scale
            ry *= scale

        if blend == 1.0:
            self.origin = None
            self.target = None

        return rx, ry
