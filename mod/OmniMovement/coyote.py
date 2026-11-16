"""One ledge departure, one immediate jump opportunity; no input queue or tick."""


class CoyoteTime:
    __slots__ = ('owner', 'grounded', 'left_at', 'jumping')

    def __init__(self):
        self.owner = None
        self.invalidate()

    def invalidate(self):
        self.grounded = False
        self.left_at = None
        self.jumping = False

    def bind(self, owner):
        if owner != self.owner:
            self.invalidate()
            self.owner = owner

    def ground(self, owner):
        self.bind(owner)
        self.invalidate()
        self.grounded = True

    def leave(self, owner, now):
        self.bind(owner)
        if not self.grounded or self.jumping:
            return False
        self.grounded = False
        self.left_at = now
        return True

    def begin_jump(self, owner):
        # This PRE boundary precedes SetPhysics/Falling, so an intentional jump
        # can never arm another jump while its native body is still executing.
        self.bind(owner)
        self.invalidate()
        self.jumping = True

    def end_jump(self, owner, succeeded, walking):
        self.bind(owner)
        self.invalidate()
        if not succeeded and walking:
            self.grounded = True

    def eligible(self, owner, now, window):
        self.bind(owner)
        if self.left_at is None:
            return False
        if window <= 0.0 or not self.left_at <= now < self.left_at + window:
            self.left_at = None
            return False
        return True

    def take(self, owner, now, window):
        if not self.eligible(owner, now, window):
            return None
        deadline = self.left_at + window
        # Consume before calling native code, including failed/blocked attempts.
        # Reentrant commands cannot obtain a second opportunity in this fall.
        self.invalidate()
        return deadline
