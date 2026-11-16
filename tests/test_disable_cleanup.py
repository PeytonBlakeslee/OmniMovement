"""BUG-10: stale reflected fire-pose reads cannot interrupt Omni disable cleanup.

Executes production FirePose/AdaptiveFirePose and the real on_disable function
against simulated UE objects. This does not assert live BL2 engine behavior.
"""
import ast
import contextlib
from pathlib import Path
from types import SimpleNamespace as NS
import unittest

from test_salvador_pose import Obj, PoseTreeTests

ROOT=Path(__file__).resolve().parents[1]
RUNTIME=ROOT/"mod"/"OmniMovement"/"__init__.py"


class BadAccessor(Obj):
    """Instrument a live-shaped pose node after its graph has been built."""
    def __getattribute__(self,field):
        if field == object.__getattribute__(self,"__dict__").get("fail_read"):
            raise RuntimeError("stale reflected read: "+field)
        return super().__getattribute__(field)

    def __setattr__(self,field,value):
        if field == self.__dict__.get("fail_write"):
            raise RuntimeError("stale reflected write: "+field)
        super().__setattr__(field,value)


def make_pose_graph(two_branches=False):
    fixture=PoseTreeTests("test_normal_single_node_path_unmodified")
    fixture.setUp()
    node, falling, normal, sprint=fixture.attach_branch("main")
    extra=fixture.attach_branch("offhand") if two_branches else None
    return fixture,node,falling,normal,sprint,extra


def disable_harness():
    tree=ast.parse(RUNTIME.read_text(encoding="utf-8-sig"))
    node=next(n for n in tree.body
              if isinstance(n,ast.FunctionDef) and n.name=="on_disable")
    logs=[]
    calls=[]
    def step(name):
        def invoke(*args):
            calls.append((name,args))
        return invoke

    names={
        "_stop_sprint_fire_entry_probe",
        "_stop_crosshair_resync", "_release_crosshair_visibility",
        "_set_native_hipfire", "_sync_fire_hooks", "_sync_weapon_firing_end_hook",
        "_restore_baseline", "_stop_travel_recovery", "_clear_assist",
        "_reset_runtime_motion", "_clear_crosshair_widget",
        "_remove_weapon_state_hooks", "_remove_all_hooks", "_cache_local",
    }
    pc=NS(Pawn=NS(),bInSprintState=True,EndSprint=step("end-sprint"))
    ns={name:step(name) for name in names}
    ns.update({
        "_pc":lambda:pc,
        "_fire_pose":NS(restore=step("fire-pose")),
        "_precision":NS(reset=step("precision-reset")),
        "logging":NS(warning=logs.append),
        "unrealsdk":NS(hooks=NS(prevent_hooking_direct_calls=contextlib.nullcontext)),
        "_active":True,
        "_requested_fire_entry":True,
        "_sprint_depth":5,
        "_crosshair_weapon_address":123,
        "_gate_sprinting":True,
        "_crosshair_modal_open":True,
        "_deathtrap_skill_active":True,
        "_deathtrap_skill_ref":object(),
        "_deathtrap_context_seen":True,
        "_coyote_ready":True,
        "_coyote_attempt":object(),
        "_coyote_traveling":True,
    })
    exec(compile(ast.fix_missing_locations(ast.Module(body=[node],type_ignores=[])),
                 str(RUNTIME),"exec"),ns)
    return ns,pc,calls,logs


class FirePoseReflectedReadTests(unittest.TestCase):
    def test_stale_reflected_read_or_write_does_not_prevent_alias_restore(self):
        for field,operation in (
            ("TargetWeight","read"),("ActiveChildIndex","read"),
            ("TargetWeight","write"),("BlendTimeToGo","write")
        ):
            with self.subTest(field=field,operation=operation):
                fx,node,falling,normal,sprint,_=make_pose_graph()
                pose=fx.ns["FirePose"]()
                pose.node=lambda:node
                pose.target=0
                node.TargetWeight=[1.0,0.0,0.0]
                self.assertTrue(pose._ensure_aliases(fx.pawn,node))
                self.assertIs(node.Children[2].Anim,normal)
                node.__class__=BadAccessor
                setattr(node,"fail_read" if operation=="read" else "fail_write",field)
                pose.restore()
                self.assertIs(node.Children[2].Anim,sprint)
                self.assertFalse(pose.aliases_active)
                self.assertFalse(pose._alias_links)
                self.assertIsNone(pose.node)
                self.assertIsNone(pose.weapon)
                self.assertIsNone(pose.pawn)
                self.assertIsNone(pose.target)

    def test_bad_first_branch_does_not_prevent_other_branch_restoration(self):
        fx,node,falling,normal,sprint,other=make_pose_graph(two_branches=True)
        off,offfall,offnormal,offsprint=other
        pose=fx.pose()
        pose.update(fx.pc,eligible=True,firing=True,now=1.)
        self.assertTrue(pose.aliases_active)
        node.__class__=BadAccessor
        node.fail_read="ActiveChildIndex"
        pose.restore()
        self.assertIs(node.Children[2].Anim,sprint)
        self.assertIs(off.Children[2].Anim,offsprint)
        self.assertFalse(pose.aliases_active)
        self.assertEqual(pose._branches,())

    def test_failed_node_weak_pointer_does_not_leave_pose_bound(self):
        fx,node,falling,normal,sprint,_=make_pose_graph()
        pose=fx.ns["FirePose"]()
        pose.node=lambda: (_ for _ in ()).throw(RuntimeError("retired weak owner"))
        pose.target=0
        pose.restore()
        self.assertIsNone(pose.node)
        self.assertIsNone(pose.target)


class DisableIsolationTests(unittest.TestCase):
    @staticmethod
    def fail(message):
        def fault(*args):
            raise RuntimeError(message)
        return fault

    def assert_released(self,ns,calls):
        names=[name for name,_ in calls]
        for mandatory in ("_set_native_hipfire","_sync_fire_hooks",
                          "_sync_weapon_firing_end_hook","_restore_baseline",
                          "_stop_travel_recovery","_clear_assist",
                          "_reset_runtime_motion","_remove_weapon_state_hooks",
                          "_remove_all_hooks","_cache_local"):
            self.assertIn(mandatory,names)
        self.assertFalse(ns["_active"])
        self.assertEqual(ns["_sprint_depth"],0)
        self.assertFalse(ns["_requested_fire_entry"])
        self.assertEqual(ns["_crosshair_weapon_address"],0)
        self.assertFalse(ns["_coyote_ready"])
        self.assertFalse(ns["_gate_sprinting"])
        self.assertIsNone(ns["_deathtrap_skill_ref"])
        self.assertFalse(ns["_deathtrap_skill_active"])

    def test_normal_teardown_uses_existing_cleanup_boundaries(self):
        ns,pc,calls,logs=disable_harness()
        ns["on_disable"]()
        self.assert_released(ns,calls)
        self.assertIn(("fire-pose",()),calls)
        self.assertIn(("end-sprint",()),calls)
        self.assertEqual(logs,[])
        self.assertNotIn(("precision-reset",()),calls)

    def test_failing_fire_pose_restore_does_not_skip_unrelated_cleanup(self):
        ns,pc,calls,logs=disable_harness()
        ns["_fire_pose"].restore=self.fail("animation reflected getter")
        ns["on_disable"]()
        self.assert_released(ns,calls)
        self.assertIn(("end-sprint",()),calls)
        self.assertTrue(any("fire-pose" in s for s in logs))

    def test_early_cleanup_failure_does_not_block_native_or_hook_cleanup(self):
        ns,pc,calls,logs=disable_harness()
        ns["_stop_sprint_fire_entry_probe"]=self.fail("stale SDK hook")
        ns["on_disable"]()
        self.assert_released(ns,calls)
        self.assertTrue(any("sprint-fire-entry" in s for s in logs))

    def test_motion_cleanup_failure_has_independent_native_reset_fallback(self):
        ns,pc,calls,logs=disable_harness()
        ns["_reset_runtime_motion"]=self.fail("old pawn state")
        ns["on_disable"]()
        self.assertIn(("precision-reset",()),calls)
        self.assertIn(("_remove_all_hooks",()),calls)
        self.assertFalse(ns["_active"])
        self.assertTrue(any("runtime-motion" in s for s in logs))

    def test_reflected_end_sprint_failure_does_not_skip_final_flags_or_cache(self):
        ns,pc,calls,logs=disable_harness()
        class BrokenPC:
            bInSprintState=True
            @property
            def Pawn(self):
                raise RuntimeError("stale pawn")
        ns["_pc"]=lambda:BrokenPC()
        ns["on_disable"]()
        self.assert_released(ns,calls)
        self.assertTrue(any("local-sprint" in s for s in logs))

    def test_controller_lookup_failure_does_not_skip_hook_cleanup(self):
        ns,pc,calls,logs=disable_harness()
        ns["_pc"]=self.fail("old controller")
        ns["on_disable"]()
        self.assert_released(ns,calls)
        self.assertTrue(any("controller lookup" in s for s in logs))

    def test_failed_hook_removal_still_leaves_runtime_marked_inactive(self):
        ns,pc,calls,logs=disable_harness()
        ns["_remove_all_hooks"]=self.fail("SDK refused removal")
        ns["on_disable"]()
        self.assertFalse(ns["_active"])
        self.assertEqual(ns["_sprint_depth"],0)
        self.assertIn(("_cache_local",(None,)),calls)
        self.assertTrue(any("remaining-hooks" in s for s in logs))


if __name__=="__main__":
    unittest.main()
