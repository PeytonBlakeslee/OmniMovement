"""Action-state helpers and conditional ownership of action-exit skid timing."""
import unrealsdk


def action_interrupted(
    pc,
    *,
    weapon_transitioning=False,
    check_equipping_fallback=False,
    check_weapon_transition=True,
):
    """Cheap hot-path guard; only call IsEquipping if state hooks were unavailable."""
    pawn = None if pc is None else pc.Pawn
    if pawn is None:
        return True

    if (
        pc.bThrowingGrenade
        or pc.bPerformingSharedWeaponAction
        or pc.bPerformingMeleeAttack
    ):
        return True
    if not check_weapon_transition:
        return False
    weapon = pawn.Weapon
    if weapon_transitioning or (weapon is not None and weapon.bWeaponPutDown):
        return True
    if not check_equipping_fallback or weapon is None:
        return False

    try:
        return bool(weapon.IsEquipping())
    except Exception:
        return False


class ActionSkid:
    """Expire only the local action-interruption skid with conditional ownership."""

    def __init__(self):
        self.owner = None
        self.original = self.written = self.expires = None

    def restore(self):
        pc = None if self.owner is None else self.owner()
        if pc is not None and float(pc.CurrentLurch.StartTime) == self.written:
            pc.CurrentLurch.StartTime = self.original
        self.owner = None
        self.original = self.written = self.expires = None

    def update(self, pc):
        if self.owner is None:
            return
        owner = self.owner()
        if owner is None:
            self.restore()
        elif (
            pc is None
            or owner._get_address() != pc._get_address()
            or float(owner.CurrentLurch.StartTime) != self.written
        ):
            self.restore()
        elif float(owner.WorldInfo.TimeSeconds) >= self.expires:
            self.restore()

    def cancel(self, pc):
        self.restore()
        now = float(pc.WorldInfo.TimeSeconds)
        start = float(pc.CurrentLurch.StartTime)
        duration = float(pc.CurrentLurch.Duration)
        if duration <= 0.0 or not start <= now < start + duration:
            return
        self.owner = unrealsdk.unreal.WeakPointer(pc)
        self.original = start
        self.expires = start + duration
        pc.CurrentLurch.StartTime = start - duration - 1.0
        self.written = float(pc.CurrentLurch.StartTime)
