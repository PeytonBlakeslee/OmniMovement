"""Bounded menu-aware recovery clock; no SDK access or gameplay writes."""
from __future__ import annotations


class RecoveryBudget:
    __slots__ = ("active", "started", "last", "eligible", "ready", "result")
    ACTIVE_LIMIT = 24.0
    WALL_LIMIT = 120.0

    def __init__(self):
        self.active = False
        self.started = self.last = self.eligible = 0.0
        self.ready = False
        self.result = "not-started"

    def start(self, now):
        if self.active:
            return False
        self.active = True
        self.started = self.last = float(now)
        self.eligible = 0.0
        self.ready = False
        self.result = "running"
        return True

    def sample(self, now, ready):
        if not self.active:
            return self.clock
        now = float(now)
        # A gap whose endpoints do not both prove readiness is not gameplay.
        # UI edges explicitly suspend this clock even when PlayerTick stops.
        if ready and self.ready:
            self.eligible += max(0.0, now - self.last)
        self.last = max(now, self.last)
        self.ready = bool(ready)
        return self.clock

    @property
    def clock(self):
        # Existing retry state uses zero as an unset timestamp.
        return 1.0 + self.eligible

    def expiration(self, now):
        if not self.active:
            return None
        if float(now) - self.started >= self.WALL_LIMIT:
            return "overall-timeout"
        if self.eligible >= self.ACTIVE_LIMIT:
            return "eligible-time-exhausted"
        return None

    def finish(self, reason):
        if self.active:
            self.result = reason
        self.active = self.ready = False

    def status(self):
        return {"active": self.active, "result": self.result,
                "eligible_seconds": self.eligible, "active_limit": self.ACTIVE_LIMIT,
                "wall_limit": self.WALL_LIMIT}
