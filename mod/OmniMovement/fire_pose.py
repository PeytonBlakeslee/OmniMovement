"""Owned first-person sprint/fire posture changes with no permanent tick."""
import unrealsdk

DUAL_FIRE_RELEASE_DELAY = 0.10


# A deployed/ongoing action skill is not equivalent to a casting animation.
# Weapon/movement interruption state is checked by the caller, rather than
# excluding every character with an active ability or naming one skill class.

def on_foot(pawn, player_input):
    context = str(player_input.CurrentInputContextName)
    if context == "DefaultOnFoot":
        return True
    if context != "DeathTrap":
        return False
    # Keep the existing special on-foot input context, without requiring one
    # concrete action-skill class. Actual cast/firing guards remain upstream.
    return pawn.MyActionSkill is not None


def firing_pose_context(pc, pawn, player_input):
    """Allow stock class-defined action-skill input contexts for local fire pose.

    A deployed ability can keep the player's ordinary weapon usable while its
    input selector holds a custom context for the entire skill lifetime. For
    example, SoldierInputContextSelector returns the *class-declared* Scorpio
    context while the deployable is active. Context name alone is insufficient:
    require an active owned action skill and a matching PlayerClass declaration.

    This check is intentionally only for first-person firing presentation.
    Do not widen the on_foot() predicate used by movement/sprint eligibility.
    """
    if player_input is None or pawn is None:
        return False
    if on_foot(pawn, player_input):
        return True
    try:
        if pawn.MyActionSkill is None:
            return False
        context = str(player_input.CurrentInputContextName)
        player_class = None if pc is None else pc.PlayerClass
        if not context or player_class is None:
            return False
        return any(str(item.ContextName) == context for item in player_class.InputContexts)
    except Exception:
        # Fail closed for missing metadata, vehicles, unrecognized or damaged
        # input contexts. No character-name exceptions or global state edits.
        return False


def _same_object(left, right):
    """Compare wrapped UObjects without depending on wrapper identity."""
    if left is right:
        return True
    if left is None or right is None:
        return False
    try:
        return left._path_name() == right._path_name()
    except Exception:
        return left == right


def _same_live_instance(left, right, *, right_address=None):
    """A replaced tree node may reuse the same Unreal object path."""
    if left is right:
        return True
    if left is None or right is None:
        return False
    try:
        return int(left._get_address()) == int(
            right._get_address() if right_address is None else right_address)
    except Exception:
        # If a wrapper cannot expose identity, retain the former conservative
        # wrapper/path check rather than dereferencing an unknown game pointer.
        return _same_object(left, right)


class FirePose:
    def __init__(self):
        self.node = self.weapon = self.pawn = None
        self.target = None
        self.last_fire = -1.0

        # v0.15.26: while sprint-fire owns the local first-person presentation,
        # redirect only this pawn's arms-tree branches which can replace the
        # standing/fire posture. Native sprint/falling state remains untouched.
        # A structural alias is one individually owned parent/child link. Store
        # weak pointers plus exact addresses, never a strong animation graph.
        # Failed restores retain only their unresolved records.
        self._alias_links = []
        self._alias_root = None
        self._alias_blocked_node_address = None
        self.aliases_active = False

    @staticmethod
    def _alias_address(obj):
        """Require exact UObject instance addresses for structural writes."""
        try:
            address = int(obj._get_address())
            return address if address > 0 else None
        except Exception:
            return None

    def _set_alias_link(self, parent, index, original, replacement):
        """Record ownership *before* setting a potentially write-then-throw field."""
        addresses = tuple(self._alias_address(obj)
                          for obj in (parent, original, replacement))
        if any(address is None for address in addresses):
            raise ValueError("unverifiable animation alias identity")
        if self._alias_address(parent.Children[index].Anim) != addresses[1]:
            raise ValueError("animation alias changed before redirect")
        record = {
            "parent": unrealsdk.unreal.WeakPointer(parent),
            "parent_address": addresses[0],
            "index": index,
            "original": unrealsdk.unreal.WeakPointer(original),
            "original_address": addresses[1],
            "replacement": unrealsdk.unreal.WeakPointer(replacement),
            "replacement_address": addresses[2],
            "attempts": 0,
        }
        self._alias_links.append(record)
        parent.Children[index].Anim = replacement
        if self._alias_address(parent.Children[index].Anim) != addresses[2]:
            raise ValueError("animation alias write failed verification")

    def _release_aliases(self, node=None):
        """Restore proven-owned links independently, with bounded retry on failure."""
        self.aliases_active = False
        if not self._alias_links:
            return
        if node is None:
            node = None if self.node is None else self.node()
        if node is None and self._alias_root is not None:
            node = self._alias_root()
        pending = []
        changed_foreign = False
        # Reverse the write order. A failed link cannot discard other records.
        for record in reversed(self._alias_links):
            try:
                parent = record["parent"]()
                original = record["original"]()
                replacement = record["replacement"]()
            except Exception:
                parent = original = replacement = None
            if (parent is None or original is None or replacement is None):
                continue  # Retired owner or target, no valid write is possible.
            if (self._alias_address(parent) != record["parent_address"]
                    or self._alias_address(original) != record["original_address"]
                    or self._alias_address(replacement) != record["replacement_address"]):
                continue  # Stale/replaced UObject instance; never follow a reused path.
            try:
                child = parent.Children[record["index"]]
                current_address = self._alias_address(child.Anim)
            except Exception:
                current_address = None
                child = None
            if current_address is not None and current_address != record["replacement_address"]:
                if current_address != record["original_address"]:
                    changed_foreign = True  # A newer writer now owns this graph.
                continue
            if record["attempts"] >= 3:
                pending.append(record)
                continue  # Keep ownership evidence but stop automatic writes.
            # An unreadable getter is uncertain, not proof that the link changed.
            if child is not None and current_address == record["replacement_address"]:
                try:
                    child.Anim = original
                except Exception:
                    pass  # May have written before raising: verify via readback.
            try:
                after_address = self._alias_address(parent.Children[record["index"]].Anim)
            except Exception:
                after_address = None
            if after_address == record["original_address"]:
                continue  # Confirmed restored, drop only this ledger entry.
            if after_address is not None and after_address != record["replacement_address"]:
                changed_foreign = True
                continue  # Foreign edit wins; never overwrite it on a later event.
            record["attempts"] += 1
            if record["attempts"] == 3:
                try:
                    unrealsdk.logging.warning(
                        "omnimovement: animation alias restoration deferred after "
                        "three failed attempts; retaining weak ownership record"
                    )
                except Exception:
                    pass
            pending.append(record)
        self._alias_links = list(reversed(pending))
        if changed_foreign and node is not None:
            self._alias_blocked_node_address = self._alias_address(node)

    def restore(self):
        """Release structural ownership even when stale pose properties are unreadable."""
        node = None
        try:
            node = None if self.node is None else self.node()
        except Exception as exc:
            try:
                unrealsdk.logging.warning(
                    "omnimovement: animation node retired during restore: " + repr(exc)
                )
            except Exception:
                pass
        target = self.target

        # Releasing owned alias links is independent of the cosmetic weight
        # reset. Never allow a bad reflected getter to prevent other teardown.
        try:
            self._release_aliases(node)
        except Exception as exc:
            try:
                unrealsdk.logging.warning(
                    "omnimovement: animation alias cleanup interrupted: " + repr(exc)
                )
            except Exception:
                pass
        finally:
            self.node = self.weapon = self.pawn = None
            self.target = None
            self.last_fire = -1.0

        if node is None or target != 0:
            return
        try:
            # Only restore weights when they still match our last target.
            if list(node.TargetWeight) != [1.0, 0.0, 0.0]:
                return
            index = int(node.ActiveChildIndex)
            node.TargetWeight = [float(i == index) for i in range(3)]
            node.BlendTimeToGo = 0.20
        except Exception as exc:
            # The game may have retired the tree between these reflected reads.
            # The independent alias ledger above has already been processed.
            try:
                unrealsdk.logging.warning(
                    "omnimovement: animation weight restore skipped: " + repr(exc)
                )
            except Exception:
                pass

    def _resolve(self, pawn, weapon):
        node = None if self.node is None else self.node()
        prior_weapon = None if self.weapon is None else self.weapon()
        prior_pawn = None if self.pawn is None else self.pawn()

        if node is not None and (prior_weapon != weapon or prior_pawn != pawn):
            self.restore()
            node = None

        arms = pawn.Arms
        animations = None if arms is None else arms.Animations
        if node is not None:
            # Action-skill equip/put-down and pawn animation refresh may replace
            # the live arms tree without changing pawn or held weapon. A weak
            # node can remain alive yet no longer be part of the selected tree.
            # Validate only on a firing/presentation event, never each frame.
            live = False
            try:
                if animations is not None:
                    try:
                        node_address = int(node._get_address())
                    except Exception:
                        node_address = None
                    live = any(
                        _same_live_instance(candidate, node, right_address=node_address)
                        for candidate in animations.AnimTickArray
                    )
            except Exception:
                pass
            if live:
                return node
            self.restore()

        if animations is None:
            return None

        node = None
        posture_classes = {}
        for candidate in animations.AnimTickArray:
            node_class = candidate.Class
            class_address = node_class._get_address()
            matches = posture_classes.get(class_address)
            if matches is None:
                matches = str(node_class.Name) == "AnimNodeBlendByPosture"
                posture_classes[class_address] = matches
            if matches:
                if node is not None:
                    return None  # Preserve the unique-node requirement.
                node = candidate
        if node is None:
            return None
        if (
            not node._path_name().startswith(pawn._path_name() + ".")
            or [str(c.Name) for c in node.Children]
            != ["Standing", "Crouched", "Sprinting"]
        ):
            return None

        native = node.Children[2].Anim
        normal = node.Children[0].Anim
        if (
            native is None
            or normal is None
            or str(getattr(native, "AnimSeqName", "")) != "Sprint"
        ):
            return None

        self.node = unrealsdk.unreal.WeakPointer(node)
        self.weapon = unrealsdk.unreal.WeakPointer(weapon)
        self.pawn = unrealsdk.unreal.WeakPointer(pawn)
        return node

    def _ensure_aliases(self, pawn, node):
        """Make native sprint/falling selectors resolve to the firing-safe base.

        Probe v0.15.25 proved two independent native selectors overwrite the
        old TargetWeight-only workaround:
          * WillowAnimNode_Falling selects Jump_Start/Idle/End while airborne.
          * when it returns to Pass Thru after landing, AnimNodeBlendByPosture
            re-selects its native Sprint child because ActiveChildIndex remains 2.

        Rather than racing those native animation ticks, redirect only the local
        pawn's child links while held-fire owns the pose. Native selectors can
        keep changing weights/indices normally; every affected selection resolves
        to the same Standing/Running subtree. All links are conditionally restored.
        """
        if self.aliases_active:
            return True
        node_address = self._alias_address(node)
        if self._alias_blocked_node_address is not None:
            if node_address == self._alias_blocked_node_address:
                return False  # The old graph has a new owner; never re-alias it.
            self._alias_blocked_node_address = None
        if self._alias_links:
            self._release_aliases(node)
            if self._alias_links:
                return False  # An old link still needs verified restoration.

        arms = pawn.Arms
        if arms is None or arms.Animations is None:
            return False

        normal = node.Children[0].Anim
        native_sprint = node.Children[2].Anim
        if normal is None or native_sprint is None:
            return False

        falling_candidates = []
        for candidate in arms.Animations.AnimTickArray:
            if str(candidate.Class.Name) != "WillowAnimNode_Falling":
                continue
            try:
                if (
                    len(candidate.Children) == 4
                    and [str(c.Name) for c in candidate.Children]
                    == ["Pass Thru", "Start", "Idle", "End"]
                    and _same_live_instance(candidate.Children[0].Anim, node)
                ):
                    falling_candidates.append(candidate)
            except Exception:
                continue

        # The hip-fire path is non-ADS, so exactly one Falling node should feed
        # this posture node. If the tree is unusual, do not make a broad guess.
        if len(falling_candidates) != 1:
            return False

        falling = falling_candidates[0]
        try:
            originals = tuple(falling.Children[index].Anim for index in (1, 2, 3))
            if any(original is None for original in originals):
                return False
            # Remember the root weakly even if a later failed restore outlives
            # self.node; a foreign edit on a retired branch must still block reuse.
            self._alias_root = unrealsdk.unreal.WeakPointer(node)
            # Each successful (or write-then-throw) assignment has a distinct
            # weak owner. A failed later child cannot orphan earlier edits.
            self._set_alias_link(node, 2, native_sprint, normal)
            for index, original in zip((1, 2, 3), originals):
                self._set_alias_link(falling, index, original, node)
        except Exception:
            self._release_aliases(node)
            return False
        self.aliases_active = True
        return True

    def update(self, pc, *, eligible, firing, now):
        """Update only when a fire event/release boundary needs posture work."""
        pawn = None if pc is None else pc.Pawn
        weapon = None if pawn is None else pawn.Weapon
        if not eligible or pawn is None or weapon is None:
            self.restore()
            return

        node = self._resolve(pawn, weapon)
        if node is None:
            return

        if firing:
            self.last_fire = now

        dual = getattr(pawn, "OffHandWeapon", None) is not None
        release_delay = DUAL_FIRE_RELEASE_DELAY if dual else 0.18
        target = 0 if firing or now - self.last_fire < release_delay else 2
        if int(node.ActiveChildIndex) != 2:
            self._release_aliases(node)
            return

        if target == 0:
            # This is the important v0.15.26 change. It survives native
            # animation evaluation instead of trying to overwrite it afterwards.
            self._ensure_aliases(pawn, node)
        else:
            self._release_aliases(node)

        if target != self.target:
            node.TargetWeight = [float(i == target) for i in range(3)]
            if dual:
                node.BlendTimeToGo = 0.10 if target == 0 else 0.14
            else:
                node.BlendTimeToGo = 0.08 if target == 0 else 0.20
            self.target = target


class _BoundFirePose(FirePose):
    """One exact arm-tree posture branch, never an arbitrary named global node."""

    def __init__(self, candidate):
        super().__init__()
        self._candidate = unrealsdk.unreal.WeakPointer(candidate)

    def _resolve(self, pawn, weapon):
        node = self._candidate()
        if node is None:
            self.restore()
            return None
        arms = pawn.Arms
        animations = None if arms is None else arms.Animations
        if animations is None:
            self.restore()
            return None
        try:
            # A previous arm-tree generation must not be edited just because it
            # has the same path name as the current one.
            if not any(_same_live_instance(entry, node)
                       for entry in animations.AnimTickArray):
                self.restore()
                return None
        except Exception:
            self.restore()
            return None

        cached_pawn = None if self.pawn is None else self.pawn()
        cached_weapon = None if self.weapon is None else self.weapon()
        if (self.node is not None
                and (not _same_live_instance(cached_pawn, pawn)
                     or not _same_live_instance(cached_weapon, weapon))):
            self.restore()
        self.node = unrealsdk.unreal.WeakPointer(node)
        self.pawn = unrealsdk.unreal.WeakPointer(pawn)
        self.weapon = unrealsdk.unreal.WeakPointer(weapon)
        return node


class AdaptiveFirePose:
    """Preserve the stable single-node path; safely cover parallel arm branches.

    BL2's two-weapon first-person animation trees may contain more than one
    posture node. The historic unique-node resolver intentionally fails closed
    in that case, leaving native Sprint animations to play over firing. Only
    when the stable resolver finds no node, independently prove each candidate
    has the exact known posture signature, its own Sprint sequence, and one
    linked Falling node before redirecting any links.

    No permanent tick, character-name allowlist, native sprint write, weapon
    transform, or asset import is involved. A missing/unknown topology still
    fails closed without modifying unknown animation trees.
    """

    def __init__(self):
        self._primary = FirePose()
        self._branches = ()
        self._retired_aliases = ()  # Weak-link cleanup survivors from old branches.
        self._keys = ()
        self._last_context = None
        self.fallback_status = "not-needed"

    @property
    def aliases_active(self):
        return (self._primary.aliases_active
                or any(branch.aliases_active for branch in self._branches))

    @property
    def node(self):
        if self._primary.node is not None:
            return self._primary.node
        for branch in self._branches:
            if branch.node is not None:
                return branch.node
        return None

    @staticmethod
    def _retired_branch_needs_tracking(branch):
        if branch._alias_links:
            return True
        blocked = branch._alias_blocked_node_address
        if blocked is None:
            return False
        # Retain only a weakly live exact owner; never permanently blacklist
        # an old path after an animation-tree replacement.
        owner = None if branch._alias_root is None else branch._alias_root()
        return FirePose._alias_address(owner) == blocked

    def _drain_retired_aliases(self):
        if not self._retired_aliases:
            return
        pending = []
        for branch in self._retired_aliases:
            branch._release_aliases()
            if self._retired_branch_needs_tracking(branch):
                pending.append(branch)
        self._retired_aliases = tuple(pending)

    def _clear_branches(self):
        self._drain_retired_aliases()
        retired = list(self._retired_aliases)
        for branch in self._branches:
            branch.restore()
            if self._retired_branch_needs_tracking(branch):
                retired.append(branch)
        self._retired_aliases = tuple(retired)
        self._branches = ()
        self._keys = ()

    def restore(self):
        self._primary.restore()
        self._clear_branches()
        self._last_context = None
        self.fallback_status = "restored"

    def _candidate_nodes(self, pawn):
        arms = pawn.Arms
        animations = None if arms is None else arms.Animations
        if animations is None:
            return (), "missing-arms-tree"
        try:
            ticks = tuple(animations.AnimTickArray)
        except Exception:
            return (), "unreadable-arms-tree"
        posture_count = 0
        valid = []
        profiles = []
        falling_children = None
        for candidate in ticks:
            try:
                if str(candidate.Class.Name) != "AnimNodeBlendByPosture":
                    continue
                posture_count += 1
                if (not candidate._path_name().startswith(pawn._path_name() + ".")
                        or len(candidate.Children) != 3
                        or [str(child.Name) for child in candidate.Children]
                        != ["Standing", "Crouched", "Sprinting"]):
                    continue
                normal = candidate.Children[0].Anim
                sprint = candidate.Children[2].Anim
                if normal is None or sprint is None:
                    continue

                # Most characters have one ordinary Sprint sequence. The
                # Salvador/Mercenary first-person tree observed in BL2 has a
                # *paired* arrangement instead:
                #   posture #9  : WillowAnimNode_Condition (single-hand)
                #   posture #10 : Sprint_DualWield (dual-hand)
                # The project source also confirms its dedicated MercHands tree
                # and DualWieldSwitch. Never accept either unusual leaf alone.
                profile = None
                if str(getattr(sprint, "AnimSeqName", "")) == "Sprint":
                    profile = "stock"
                elif (str(normal.Class.Name) == "AnimNodeBlendBySpeed"
                      and _same_live_instance(candidate.Children[1].Anim, normal)):
                    kind = str(sprint.Class.Name)
                    if kind == "WillowAnimNode_Condition":
                        profile = "mercenary-condition"
                    elif (kind == "WillowAnimNodeSequence"
                          and str(getattr(sprint, "AnimSeqName", ""))
                          == "Sprint_DualWield"):
                        profile = "mercenary-dual"
                if profile is None:
                    continue

                # Only classify falling nodes if a posture branch qualifies.
                # Keep duplicate entries, since two matches are ambiguous.
                if falling_children is None:
                    children = []
                    for parent in ticks:
                        if str(parent.Class.Name) != "WillowAnimNode_Falling":
                            continue
                        links = parent.Children
                        if (len(links) == 4
                                and [str(child.Name) for child in links]
                                == ["Pass Thru", "Start", "Idle", "End"]):
                            children.append(links[0].Anim)
                    falling_children = children
                if sum(_same_live_instance(child, candidate)
                       for child in falling_children) == 1:
                    valid.append(candidate)
                    profiles.append(profile)
            except Exception:
                continue

        status = "posture=%d;verified=%d" % (posture_count, len(valid))
        if len(valid) not in (1, 2):
            return (), status
        if any(profile != "stock" for profile in profiles):
            # This exception is intentionally *pair-validated*. Salvador's
            # custom condition node could guard skill/movement states and must
            # not be altered in an unknown or only partially loaded tree.
            if (posture_count != 2 or len(valid) != 2
                    or set(profiles) != {"mercenary-condition", "mercenary-dual"}):
                return (), status + ";mercenary-incomplete"
            return tuple(valid), status + ";mercenary-paired"
        if len(valid) == 1 and posture_count == 1:
            # Preserve the old unique-node requirement; the primary resolver
            # is authoritative for ordinary one-posture trees.
            return (), "single-posture-unresolved"
        return tuple(valid), status


    def update(self, pc, *, eligible, firing, now):
        self._drain_retired_aliases()
        # The two-handed arms graph can be replaced on skill entry/exit without
        # changing the main-hand weapon. A context boundary is an event-time
        # opportunity to relinquish the old graph before selecting a new one.
        try:
            context = str(pc.PlayerInput.CurrentInputContextName)
        except Exception:
            context = None
        if self._last_context is not None and context != self._last_context:
            self.restore()
        self._last_context = context
        if not eligible:
            self.restore()
            self._last_context = context
            self.fallback_status = "ineligible"
            return

        pawn = None if pc is None else pc.Pawn
        weapon = None if pawn is None else pawn.Weapon
        # A live fallback has already redirected the Sprint links to Standing.
        # Re-running discovery against those owned edits would see zero original
        # Sprint nodes and immediately undo the fix on the second firing event.
        if self._branches:
            try:
                animations = pawn.Arms.Animations
                live = animations.AnimTickArray
                keys_ok = (int(pawn._get_address()), int(weapon._get_address())) == self._keys[:2]
                branches_ok = keys_ok and all(
                    branch._candidate() is not None
                    and any(_same_live_instance(node, branch._candidate()) for node in live)
                    for branch in self._branches
                )
            except Exception:
                branches_ok = False
            if branches_ok:
                for branch in self._branches:
                    branch.update(pc, eligible=True, firing=firing, now=now)
                self.fallback_status = (
                    "parallel-branches-cached;aliases-active"
                    if self.aliases_active else "parallel-branches-cached;aliases-inactive"
                )
                return
            self._clear_branches()

        self._primary.update(pc, eligible=eligible, firing=firing, now=now)
        if self._primary.node is not None:
            self._clear_branches()
            self.fallback_status = "stable-single"
            return
        if pawn is None or weapon is None:
            self._clear_branches()
            self.fallback_status = "missing-pawn-or-weapon"
            return
        if self._primary._alias_links or any(
                branch._alias_links for branch in self._retired_aliases):
            self.fallback_status = "pending-alias-restoration"
            return  # Do not layer new graph edits over unresolved old ownership.
        candidates, status = self._candidate_nodes(pawn)
        self.fallback_status = status
        blocked = {branch._alias_blocked_node_address
                   for branch in self._retired_aliases
                   if branch._alias_blocked_node_address is not None}
        if any(FirePose._alias_address(candidate) in blocked
               for candidate in candidates):
            self.fallback_status += ";foreign-alias-owner"
            return  # Preserve an external edit on this exact live branch.
        if not candidates:
            self._clear_branches()
            return
        try:
            keys = (int(pawn._get_address()), int(weapon._get_address()),
                    *(int(item._get_address()) for item in candidates))
        except Exception:
            self._clear_branches()
            self.fallback_status = "invalid-branch-identity"
            return
        if keys != self._keys:
            self._clear_branches()
            self._branches = tuple(_BoundFirePose(node) for node in candidates)
            self._keys = keys
        for branch in self._branches:
            branch.update(pc, eligible=True, firing=firing, now=now)
        if self.aliases_active:
            self.fallback_status += ";aliases-active"
        else:
            self.fallback_status += ";aliases-unavailable"
