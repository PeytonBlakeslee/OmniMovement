"""Regression tests for dual first-person arm-tree sprint/fire pose routing.

These execute the real FirePose and AdaptiveFirePose implementations with
simulated UE3 animation graphs. They do NOT claim to represent Salvador's
exact live asset tree, which must be confirmed in game.
"""
import ast
from pathlib import Path
from types import SimpleNamespace as NS
import unittest

SOURCE = (Path(__file__).resolve().parents[1] /
          "mod" / "OmniMovement" / "fire_pose.py").read_text(encoding="utf-8-sig")
TREE = ast.parse(SOURCE)


class Obj:
    next_address = 1000

    def __init__(self, name, class_name=None, path=None):
        Obj.next_address += 1
        self.addr = Obj.next_address
        self.Name = name
        self.Class = NS(Name=class_name or name,
                        _get_address=lambda: hash(class_name or name))
        self.path = path or name

    def _get_address(self):
        return self.addr

    def _path_name(self):
        return self.path


class PoseTreeTests(unittest.TestCase):
    def setUp(self):
        funcs = ("_same_object", "_same_live_instance")
        classes = ("FirePose", "_BoundFirePose", "AdaptiveFirePose")
        nodes = [node for node in TREE.body
                 if (isinstance(node, ast.FunctionDef) and node.name in funcs)
                 or (isinstance(node, ast.ClassDef) and node.name in classes)]
        self.ns = {"unrealsdk": NS(unreal=NS(WeakPointer=lambda obj: lambda: obj))}
        exec(compile(ast.Module(body=nodes, type_ignores=[]),
                     "<pose-test>", "exec"), self.ns)
        self.pawn = Obj("Salvador", path="Pawn_Mercenary")
        self.weapon = Obj("weapon")
        self.pawn.Weapon = self.weapon
        self.pawn.Arms = NS(Animations=NS(AnimTickArray=[]))
        self.pc = NS(Pawn=self.pawn)
        self.tree = self.pawn.Arms.Animations.AnimTickArray

    def attach_branch(self, suffix):
        node = Obj(suffix, "AnimNodeBlendByPosture",
                   path="Pawn_Mercenary." + suffix)
        normal = Obj("normal" + suffix, "AnimTree")
        sprint = Obj("sprint" + suffix, "AnimNodeSequence")
        sprint.AnimSeqName = "Sprint"
        node.Children = [
            NS(Name="Standing", Anim=normal),
            NS(Name="Crouched", Anim=Obj("crouch" + suffix)),
            NS(Name="Sprinting", Anim=sprint),
        ]
        node.TargetWeight = [0.0, 0.0, 1.0]
        node.ActiveChildIndex = 2
        node.BlendTimeToGo = 0.0
        falling = Obj("fall" + suffix, "WillowAnimNode_Falling",
                      path="Pawn_Mercenary.fall" + suffix)
        falling.Children = [
            NS(Name="Pass Thru", Anim=node),
            NS(Name="Start", Anim=Obj("start" + suffix)),
            NS(Name="Idle", Anim=Obj("idle" + suffix)),
            NS(Name="End", Anim=Obj("end" + suffix)),
        ]
        self.tree.extend([node, falling])
        return node, falling, normal, sprint

    def pose(self):
        return self.ns["AdaptiveFirePose"]()

    def test_normal_single_node_path_unmodified(self):
        node, falling, normal, sprint = self.attach_branch("main")
        obj = self.pose()
        obj.update(self.pc, eligible=True, firing=True, now=1.0)
        self.assertEqual(len(obj._branches), 0)
        self.assertEqual(obj.fallback_status, "stable-single")
        self.assertTrue(obj.aliases_active)
        self.assertIs(node.Children[2].Anim, normal)
        obj.restore()
        self.assertIs(node.Children[2].Anim, sprint)
        self.assertFalse(obj.aliases_active)

    def test_two_verified_weapon_arm_branches_corrected(self):
        a, af, an, asp = self.attach_branch("main")
        b, bf, bn, bsp = self.attach_branch("offhand")
        originals_a = tuple(af.Children[i].Anim for i in (1, 2, 3))
        originals_b = tuple(bf.Children[i].Anim for i in (1, 2, 3))
        obj = self.pose()
        obj.update(self.pc, eligible=True, firing=True, now=1.0)
        self.assertEqual(len(obj._branches), 2)
        self.assertTrue(obj.aliases_active)
        self.assertIn("aliases-active", obj.fallback_status)
        self.assertIs(a.Children[2].Anim, an)
        self.assertIs(b.Children[2].Anim, bn)
        self.assertTrue(all(af.Children[i].Anim is a for i in (1, 2, 3)))
        self.assertTrue(all(bf.Children[i].Anim is b for i in (1, 2, 3)))
        # Repeated events should reuse the exact same graph ownership.
        ids = tuple(id(x) for x in obj._branches)
        obj.update(self.pc, eligible=True, firing=True, now=1.05)
        self.assertEqual(ids, tuple(id(x) for x in obj._branches))
        obj.restore()
        self.assertIs(a.Children[2].Anim, asp)
        self.assertIs(b.Children[2].Anim, bsp)
        self.assertEqual(tuple(af.Children[i].Anim for i in (1, 2, 3)), originals_a)
        self.assertEqual(tuple(bf.Children[i].Anim for i in (1, 2, 3)), originals_b)

    def test_when_firing_stops_aliases_restore(self):
        a, _, an, asp = self.attach_branch("main")
        b, _, bn, bsp = self.attach_branch("offhand")
        obj = self.pose()
        obj.update(self.pc, eligible=True, firing=True, now=1.0)
        obj.update(self.pc, eligible=True, firing=False, now=2.0)
        self.assertIs(a.Children[2].Anim, asp)
        self.assertIs(b.Children[2].Anim, bsp)
        self.assertFalse(obj.aliases_active)

    def test_replaced_tree_fails_closed_and_restores_owned_links(self):
        a, af, an, asp = self.attach_branch("main")
        b, bf, bn, bsp = self.attach_branch("offhand")
        obj = self.pose()
        obj.update(self.pc, eligible=True, firing=True, now=1.0)
        self.pawn.Arms.Animations = NS(AnimTickArray=[])
        obj.update(self.pc, eligible=True, firing=True, now=2.0)
        self.assertFalse(obj.aliases_active)
        self.assertEqual(obj.fallback_status, "missing-arms-tree" if
                         self.pawn.Arms.Animations is None else "posture=0;verified=0")
        self.assertIs(a.Children[2].Anim, asp)
        self.assertIs(b.Children[2].Anim, bsp)

    def test_unrecognized_three_or_unlinked_nodes_are_not_modified(self):
        a, _, an, asp = self.attach_branch("main")
        b, _, bn, bsp = self.attach_branch("offhand")
        c, _, cn, csp = self.attach_branch("third")
        obj = self.pose()
        obj.update(self.pc, eligible=True, firing=True, now=1.0)
        self.assertFalse(obj.aliases_active)
        self.assertIn("verified=3", obj.fallback_status)
        for node, sprint in ((a, asp), (b, bsp), (c, csp)):
            self.assertIs(node.Children[2].Anim, sprint)
        self.tree.remove(c)
        self.tree.remove(next(item for item in self.tree if item.Name == "fallthird"))
        self.tree.remove(next(item for item in self.tree if item.Name == "falloffhand"))
        obj.update(self.pc, eligible=True, firing=True, now=2.0)
        # Exactly one linked branch remains. Only that verified branch may
        # change; the unlinked offhand node must remain completely native.
        self.assertTrue(obj.aliases_active)
        self.assertIs(a.Children[2].Anim, an)
        self.assertIs(b.Children[2].Anim, bsp)
        obj.restore()
        self.assertIs(a.Children[2].Anim, asp)

    def test_unknown_posture_does_not_rewrite_nodes(self):
        a, af, an, asp = self.attach_branch("main")
        b, bf, bn, bsp = self.attach_branch("offhand")
        af.Children[0].Anim = Obj("different-parent")
        bf.Children[0].Anim = Obj("another-parent")
        b.Children[2].Anim.AnimSeqName = "GunzerkRun"
        obj = self.pose()
        obj.update(self.pc, eligible=True, firing=True, now=1.0)
        self.assertFalse(obj.aliases_active)
        self.assertIs(a.Children[2].Anim, asp)
        self.assertIs(b.Children[2].Anim, bsp)
        self.assertFalse(hasattr(obj, "describe_unresolved"))

    def attach_observed_merc_tree(self, *, gunzerking=False):
        """Replay the exact topology reported by the v1.1.9 user probe.

        Normal: posture#9 is active 2, posture#10 active 0.
        Gunzerking: both posture nodes are active 2.
        The unique Falling links have already been validated by the probe.
        """
        regular, regular_falling, regular_stand, regular_sprint = (
            self.attach_branch("MercNormal"))
        dual, dual_falling, dual_stand, dual_sprint = (
            self.attach_branch("MercDual"))
        regular_stand.Class.Name = "AnimNodeBlendBySpeed"
        dual_stand.Class.Name = "AnimNodeBlendBySpeed"
        regular.Children[1].Anim = regular_stand
        dual.Children[1].Anim = dual_stand
        regular_sprint.Class.Name = "WillowAnimNode_Condition"
        regular_sprint.AnimSeqName = ""
        dual_sprint.Class.Name = "WillowAnimNodeSequence"
        dual_sprint.AnimSeqName = "Sprint_DualWield"
        dual.ActiveChildIndex = 2 if gunzerking else 0
        return regular, regular_falling, regular_stand, regular_sprint, dual, dual_falling, dual_stand, dual_sprint

    def test_live_salvador_normal_weapon_pose_and_cleanup(self):
        normal, fall, standing, condition, dual, dualfall, dualstand, dualsprint = (
            self.attach_observed_merc_tree())
        obj = self.pose()
        obj.update(self.pc, eligible=True, firing=True, now=1.0)
        self.assertEqual(len(obj._branches), 2)
        self.assertIn("mercenary-paired", obj.fallback_status)
        self.assertTrue(obj.aliases_active)
        self.assertIs(normal.Children[2].Anim, standing)
        # While not gunzerking, the secondary branch must retain its native
        # Sprint_DualWield sequence: native DualWieldSwitch still owns selection.
        self.assertIs(dual.Children[2].Anim, dualsprint)
        self.assertIs(fall.Children[1].Anim, normal)
        obj.update(self.pc, eligible=True, firing=False, now=2.0)
        self.assertIs(normal.Children[2].Anim, condition)
        self.assertIs(dual.Children[2].Anim, dualsprint)
        self.assertFalse(obj.aliases_active)

    def test_live_salvador_gunzerking_both_branches_and_retry(self):
        normal, fall, standing, condition, dual, dualfall, dualstand, dualsprint = (
            self.attach_observed_merc_tree(gunzerking=True))
        fall_sources = tuple(fall.Children[i].Anim for i in (1, 2, 3))
        dual_sources = tuple(dualfall.Children[i].Anim for i in (1, 2, 3))
        obj = self.pose()
        obj.update(self.pc, eligible=True, firing=True, now=1.0)
        self.assertTrue(obj.aliases_active)
        self.assertIs(normal.Children[2].Anim, standing)
        self.assertIs(dual.Children[2].Anim, dualstand)
        ids = tuple(id(branch) for branch in obj._branches)
        obj.update(self.pc, eligible=True, firing=True, now=1.08)
        self.assertEqual(ids, tuple(id(branch) for branch in obj._branches))
        obj.restore()
        self.assertIs(normal.Children[2].Anim, condition)
        self.assertIs(dual.Children[2].Anim, dualsprint)
        self.assertEqual(tuple(fall.Children[i].Anim for i in (1, 2, 3)), fall_sources)
        self.assertEqual(tuple(dualfall.Children[i].Anim for i in (1, 2, 3)), dual_sources)

    def test_live_salvador_switch_active_branch_does_not_break_dual_wield(self):
        normal, fall, standing, condition, dual, dualfall, dualstand, dualsprint = (
            self.attach_observed_merc_tree())
        obj = self.pose()
        obj.update(self.pc, eligible=True, firing=True, now=1.0)
        self.assertIs(dual.Children[2].Anim, dualsprint)
        dual.ActiveChildIndex = 2
        obj.update(self.pc, eligible=True, firing=True, now=1.1)
        self.assertIs(dual.Children[2].Anim, dualstand)
        dual.ActiveChildIndex = 0
        obj.update(self.pc, eligible=True, firing=True, now=1.2)
        self.assertIs(dual.Children[2].Anim, dualsprint)
        self.assertIs(normal.Children[2].Anim, standing)
        obj.restore()
        self.assertIs(normal.Children[2].Anim, condition)

    def test_mercenary_pair_rejects_missing_partner_or_mismatched_node(self):
        for scenario in ("missing-dual", "wrong-dual", "wrong-condition",
                         "third-posture", "missing-falling"):
            with self.subTest(scenario=scenario):
                self.setUp()
                normal, fall, standing, condition, dual, dualfall, dualstand, dualsprint = (
                    self.attach_observed_merc_tree(gunzerking=True))
                if scenario == "missing-dual":
                    self.tree.remove(dual)
                    self.tree.remove(dualfall)
                elif scenario == "wrong-dual":
                    dualsprint.AnimSeqName = "Sprint_Custom"
                elif scenario == "wrong-condition":
                    condition.Class.Name = "UnknownCondition"
                elif scenario == "third-posture":
                    self.attach_branch("third")
                elif scenario == "missing-falling":
                    dualfall.Children[0].Anim = Obj("other")
                obj = self.pose()
                obj.update(self.pc, eligible=True, firing=True, now=1.0)
                self.assertFalse(obj.aliases_active)
                self.assertIs(normal.Children[2].Anim, condition)
                self.assertIs(dual.Children[2].Anim, dualsprint)
                self.assertNotIn("mercenary-paired", obj.fallback_status)

    def test_adaptive_class_is_safely_reversible(self):
        a, af, an, asp = self.attach_branch("main")
        b, bf, bn, bsp = self.attach_branch("offhand")
        obj = self.pose()
        obj.update(self.pc, eligible=True, firing=True, now=1.0)
        # New game/posture owner edits must not be blindly clobbered at cleanup.
        injected = Obj("replacement")
        a.Children[2].Anim = injected
        obj.restore()
        self.assertIs(a.Children[2].Anim, injected)
        self.assertIs(b.Children[2].Anim, bsp)


if __name__ == "__main__":
    unittest.main()
