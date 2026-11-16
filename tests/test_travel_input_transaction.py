"""BUG-11: transactional synthetic travel input on the real production class.

Runs travel_input.HeldInputHandoff with a reflected PlayerInput fake whose
getters/setters can raise before or after writing. Not a BL2 gameplay test.
"""
import ast
import math
from pathlib import Path
from types import SimpleNamespace as NS
import time
import unittest

ROOT=Path(__file__).resolve().parents[1]/"mod"/"OmniMovement"
SOURCE=ROOT/"travel_input.py"


def load_handoff():
    tree=ast.parse(SOURCE.read_text(encoding="utf-8-sig"),filename=str(SOURCE))
    cls=next(n for n in tree.body
             if isinstance(n,ast.ClassDef) and n.name=="HeldInputHandoff")
    scope={"math":math,"time":time}
    exec(compile(ast.fix_missing_locations(ast.Module(body=[cls],type_ignores=[])),
                 str(SOURCE),"exec"),scope)
    return scope["HeldInputHandoff"]


class PlayerInput:
    def __init__(self, address=123, base=0.0, raw=0.0):
        self.address=address
        self.axis={"aBaseY":base,"RawJoyUp":raw}
        self.before={}
        self.after={}
        self.read_fail={}
        self.writes=[]
        self.reads=[]

    def _get_address(self):
        return self.address

    def GetBind(self,key):
        return ("MoveForward","W")

    def read(self,axis):
        self.reads.append(axis)
        count=self.read_fail.get(axis,0)
        if count:
            self.read_fail[axis]=count-1
            raise RuntimeError("reflected read unavailable: "+axis)
        return self.axis[axis]

    def write(self,axis,value):
        key=(axis,float(value))
        self.writes.append(key)
        count=self.before.get(key,0)
        if count:
            self.before[key]=count-1
            raise RuntimeError("rejected before write: "+axis)
        self.axis[axis]=float(value)
        count=self.after.get(key,0)
        if count:
            self.after[key]=count-1
            raise RuntimeError("committed then raised: "+axis)

    @property
    def aBaseY(self):
        return self.read("aBaseY")

    @aBaseY.setter
    def aBaseY(self,value):
        self.write("aBaseY",value)

    @property
    def RawJoyUp(self):
        return self.read("RawJoyUp")

    @RawJoyUp.setter
    def RawJoyUp(self,value):
        self.write("RawJoyUp",value)


class TransactionTests(unittest.TestCase):
    def setUp(self):
        self.down=True
        self.Handoff=load_handoff()
        self.h=self.Handoff(key_down=lambda vk:self.down and vk==0x57,
                            controller_forward=lambda:None)
        self.pi=PlayerInput()

    def assert_clean(self):
        self.assertFalse(self.h.synthetic)
        self.assertEqual(self.h._axis_owners,{})
        self.assertFalse(self.h._pending_recovery)

    def stop_handoff(self):
        """Execute the real module hook-retirement function with a fake owner."""
        tree=ast.parse((ROOT/"__init__.py").read_text(encoding="utf-8-sig"))
        func=next(n for n in tree.body if isinstance(n,ast.FunctionDef)
                  and n.name=="_stop_travel_input_handoff")
        hooks=[]
        warnings=[]
        resets=[]
        original=self.h.reset
        def reset():
            resets.append(True)
            original()
        self.h.reset=reset
        env={"_travel_input":self.h,
             "_pc":lambda:NS(PlayerInput=self.pi),
             "_set_hook":lambda *args:hooks.append(args),
             "logging":NS(warning=warnings.append),
             "INPUT_FRAME":"PreProcessInput",
             "Type":NS(POST_UNCONDITIONAL="post"),
             "_travel_input_frame":lambda *args:None}
        exec(compile(ast.fix_missing_locations(ast.Module(body=[func],
                          type_ignores=[])),str(ROOT/"__init__.py"),"exec"),env)
        env["_stop_travel_input_handoff"]()
        return hooks,warnings,resets

    def test_hook_retirement_restores_owned_fields_before_forgetting_them(self):
        self.assertTrue(self.h.apply_pre(self.pi,True))
        hooks,warnings,resets=self.stop_handoff()
        self.assertEqual(self.pi.axis,{"aBaseY":0.0,"RawJoyUp":0.0})
        self.assert_clean()
        self.assertEqual(len(resets),1)
        self.assertTrue(hooks)
        self.assertEqual(warnings,[])

    def test_hook_retirement_keeps_unresolved_ledger_when_restore_fails(self):
        self.assertTrue(self.h.apply_pre(self.pi,True))
        self.pi.before[("aBaseY",0.0)]=1
        hooks,warnings,resets=self.stop_handoff()
        self.assertTrue(self.h.synthetic)
        self.assertIn("aBaseY",self.h._axis_owners)
        self.assertEqual(resets,[])
        self.assertTrue(any("unverified" in item for item in warnings))
        self.assertTrue(hooks)
        hooks,warnings,resets=self.stop_handoff()
        self.assert_clean()
        self.assertEqual(self.pi.axis,{"aBaseY":0.0,"RawJoyUp":0.0})
        self.assertEqual(len(resets),1)

    def test_successful_keyboard_handoff_remains_responsive_and_cleans_on_release(self):
        self.assertTrue(self.h.apply_pre(self.pi,True))
        self.assertEqual(self.pi.axis,{"aBaseY":1.0,"RawJoyUp":1.0})
        self.assertEqual(set(self.h._axis_owners),{"aBaseY","RawJoyUp"})
        self.assertTrue(self.h.apply_pre(self.pi,True))
        self.assertEqual(set(self.h._axis_owners), {"aBaseY", "RawJoyUp"})
        self.down=False
        self.assertFalse(self.h.apply_pre(self.pi,True))
        self.assertEqual(self.pi.axis,{"aBaseY":0.0,"RawJoyUp":0.0})
        self.assert_clean()

    def test_held_input_samples_each_owned_axis_once(self):
        self.assertTrue(self.h.apply_pre(self.pi, True))
        self.assertEqual(len(self.pi.reads), 5)
        self.pi.reads.clear()
        self.pi.writes.clear()
        self.assertTrue(self.h.apply_pre(self.pi, True))
        self.assertEqual(self.pi.reads, ["aBaseY", "RawJoyUp"])
        self.assertEqual(self.pi.writes, [])

    def test_second_axis_is_resampled_after_first_setter_changes_it(self):
        write = self.pi.write

        def write_with_foreign_change(axis, value):
            write(axis, value)
            if (axis, value) == ("aBaseY", 1.0):
                self.pi.axis["RawJoyUp"] = -0.7

        self.pi.write = write_with_foreign_change
        self.assertFalse(self.h.apply_pre(self.pi, True))
        self.assertEqual(self.pi.axis, {"aBaseY": 0.0, "RawJoyUp": -0.7})
        self.assertFalse(any(axis == "RawJoyUp" for axis, _ in self.pi.writes))
        self.assert_clean()

    def test_failed_held_read_preserves_unverified_axis_without_writing(self):
        self.assertTrue(self.h.apply_pre(self.pi, True))
        self.pi.axis["aBaseY"] = -0.6
        self.pi.read_fail["RawJoyUp"] = 1
        self.pi.writes.clear()
        self.assertFalse(self.h.apply_pre(self.pi, True))
        self.assertEqual(set(self.h._axis_owners), {"RawJoyUp"})
        self.assertEqual(self.pi.writes, [])
        self.down = False
        self.assertFalse(self.h.apply_pre(self.pi, True))
        self.assertEqual(self.pi.axis, {"aBaseY": -0.6, "RawJoyUp": 0.0})
        self.assert_clean()

    def test_second_write_fails_before_mutation_and_rolls_back_first(self):
        self.pi.before[("RawJoyUp",1.0)]=1
        self.assertFalse(self.h.apply_pre(self.pi,True))
        self.assertEqual(self.pi.axis,{"aBaseY":0.0,"RawJoyUp":0.0})
        self.assert_clean()
        self.assertTrue(self.h.apply_pre(self.pi,True))
        self.down=False
        self.h.apply_pre(self.pi,False)
        self.assert_clean()

    def test_second_write_fails_after_mutation_then_both_are_restored(self):
        self.pi.after[("RawJoyUp",1.0)]=1
        self.assertFalse(self.h.apply_pre(self.pi,True))
        self.assertEqual(self.pi.axis,{"aBaseY":0.0,"RawJoyUp":0.0})
        self.assert_clean()

    def test_partial_rollback_failure_retains_first_axis_until_not_ready(self):
        self.pi.before[("RawJoyUp",1.0)]=1
        self.pi.before[("aBaseY",0.0)]=1
        self.assertFalse(self.h.apply_pre(self.pi,True))
        self.assertEqual(self.pi.axis["aBaseY"],1.0)
        self.assertEqual(set(self.h._axis_owners),{"aBaseY"})
        self.assertTrue(self.h.synthetic)
        self.assertTrue(self.h._pending_recovery)
        self.assertFalse(self.h.apply_pre(self.pi,False))
        self.assertEqual(self.pi.axis,{"aBaseY":0.0,"RawJoyUp":0.0})
        self.assert_clean()

    def test_release_restores_second_owned_axis_even_if_first_restore_fails(self):
        self.assertTrue(self.h.apply_pre(self.pi,True))
        self.pi.before[("RawJoyUp",0.0)]=1
        self.down=False
        self.assertFalse(self.h.apply_pre(self.pi,True))
        self.assertEqual(self.pi.axis["aBaseY"],0.0)
        self.assertEqual(self.pi.axis["RawJoyUp"],1.0)
        self.assertEqual(set(self.h._axis_owners),{"RawJoyUp"})
        self.assertTrue(self.h.synthetic)
        self.assertFalse(self.h.apply_pre(self.pi,False))
        self.assertEqual(self.pi.axis["RawJoyUp"],0.0)
        self.assert_clean()

    def test_foreign_edit_after_partial_failure_cannot_be_overwritten(self):
        self.pi.before[("RawJoyUp",1.0)]=1
        self.pi.before[("aBaseY",0.0)]=1
        self.assertFalse(self.h.apply_pre(self.pi,True))
        self.assertTrue(self.h.synthetic)
        self.pi.axis["aBaseY"]=-0.7
        self.down=False
        self.assertFalse(self.h.apply_pre(self.pi,True))
        self.assertEqual(self.pi.axis["aBaseY"],-0.7)
        self.assert_clean()

    def test_external_edit_to_one_field_preserves_it_and_restores_other(self):
        self.assertTrue(self.h.apply_pre(self.pi,True))
        self.pi.axis["RawJoyUp"]=-0.7
        self.down=False
        self.assertFalse(self.h.apply_pre(self.pi,True))
        self.assertEqual(self.pi.axis,{"aBaseY":0.0,"RawJoyUp":-0.7})
        self.assert_clean()

    def test_undecipherable_restoration_retains_owner_without_guessing(self):
        self.assertTrue(self.h.apply_pre(self.pi,True))
        self.down=False
        self.pi.read_fail["aBaseY"]=2
        self.assertFalse(self.h.apply_pre(self.pi,False))
        self.assertTrue(self.h.synthetic)
        self.assertIn("aBaseY",self.h._axis_owners)
        self.assertFalse(self.h.apply_pre(self.pi,False))
        self.assertIn("aBaseY",self.h._axis_owners)
        self.assertFalse(self.h.apply_pre(self.pi,False))
        self.assert_clean()

    def test_original_near_zero_offsets_are_restored_not_blind_zeroed(self):
        self.pi=PlayerInput(base=0.08,raw=0.05)
        self.assertTrue(self.h.apply_pre(self.pi,True))
        self.down=False
        self.assertFalse(self.h.apply_pre(self.pi,False))
        self.assertEqual(self.pi.axis,{"aBaseY":0.08,"RawJoyUp":0.05})
        self.assert_clean()

    def test_native_input_takes_priority_and_is_never_replaced(self):
        for field in ("aBaseY","RawJoyUp"):
            with self.subTest(field=field):
                pi=PlayerInput()
                pi.axis[field]=-0.8
                self.assertFalse(self.h.apply_pre(pi,True))
                self.assertEqual(pi.axis[field],-0.8)
                self.assertEqual(pi.writes,[])
                self.assert_clean()
                self.h.reset()

    def test_native_axis_change_retains_other_fields_cleanup_record(self):
        self.assertTrue(self.h.apply_pre(self.pi,True))
        self.pi.axis["aBaseY"] = -0.6
        self.assertFalse(self.h.apply_pre(self.pi,True))
        self.assertEqual(self.pi.axis["aBaseY"], -0.6)
        self.assertTrue(self.h.synthetic)
        self.assertEqual(set(self.h._axis_owners), {"RawJoyUp"})
        self.down = False
        self.assertFalse(self.h.apply_pre(self.pi,False))
        self.assertEqual(self.pi.axis["aBaseY"], -0.6)
        self.assertEqual(self.pi.axis["RawJoyUp"], 0.0)
        self.assert_clean()

    def test_new_player_input_does_not_touch_retired_generation(self):
        self.pi.before[("RawJoyUp",1.0)]=1
        self.pi.before[("aBaseY",0.0)]=1
        self.assertFalse(self.h.apply_pre(self.pi,True))
        self.assertTrue(self.h.synthetic)
        newcomer=PlayerInput(address=999)
        self.down=False
        self.assertFalse(self.h.apply_pre(newcomer,True))
        self.assertEqual(newcomer.writes,[])
        self.assert_clean()

    def test_engine_consumes_base_then_held_key_reinjects_without_losing_raw_owner(self):
        self.assertTrue(self.h.apply_pre(self.pi,True))
        self.pi.axis["aBaseY"]=0.0
        self.assertTrue(self.h.apply_pre(self.pi,True))
        self.assertEqual(self.pi.axis,{"aBaseY":1.0,"RawJoyUp":1.0})
        self.down=False
        self.assertFalse(self.h.apply_pre(self.pi,True))
        self.assertEqual(self.pi.axis,{"aBaseY":0.0,"RawJoyUp":0.0})
        self.assert_clean()

    def test_physical_controller_held_handoff_and_fractional_changes(self):
        speed=[0.75]
        h=self.Handoff(key_down=lambda _:False,
                       controller_forward=lambda:speed[0])
        pi=PlayerInput()
        self.assertTrue(h.apply_pre(pi,True))
        self.assertEqual(pi.axis,{"aBaseY":0.75,"RawJoyUp":0.75})
        speed[0]=0.85
        self.assertTrue(h.apply_pre(pi,True))
        self.assertEqual(pi.axis,{"aBaseY":0.85,"RawJoyUp":0.85})
        speed[0]=0.0
        self.assertFalse(h.apply_pre(pi,True))
        self.assertEqual(pi.axis,{"aBaseY":0.0,"RawJoyUp":0.0})
        self.assertFalse(h.synthetic)


if __name__=="__main__":
    unittest.main()
