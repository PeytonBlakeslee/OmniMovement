import ast, math, unittest
from pathlib import Path
from types import SimpleNamespace as N
P=Path(__file__).resolve().parents[1] / 'mod' / 'OmniMovement' / '__init__.py'
def load(names, ns):
 t=ast.parse(P.read_text(encoding='utf-8')); nodes=[n for n in t.body if isinstance(n,ast.FunctionDef) and n.name in names]; assert {n.name for n in nodes}==set(names); exec(compile(ast.Module(body=nodes,type_ignores=[]),str(P),'exec'),ns)
class SprintExitTests(unittest.TestCase):
 def setUp(self):
  self.pi=N(aBaseY=0.0,aStrafe=0.0)
  self.pawn=N(Physics=1,DrivenVehicle=None,bIsCrouched=False,bWantsToCrouch=False,bForceRMVelocity=False,bForceRegularVelocity=False,bRMForceStop=False,Weapon=N(),OffHandWeapon=None)
  self.pc=N(PlayerInput=self.pi,Pawn=self.pawn,bDuck=False,bPressedJump=False,bInSprintState=True,_get_address=lambda:55)
  self.ns=dict(_active=True,_cfg_omni=True,_cfg_assist=True,_armed=True,_basic_context=lambda *a,**k:True,_knockback_until=0.0,_interrupted=lambda pc:False,_fast_zoomed=lambda pc,w:False,_ads_sprint_edge=None,math=math)
  load({'_input_sprint_exit_skid'},self.ns)
 def eligible(self): return self.ns['_input_sprint_exit_skid'](self.pc,self.pawn,self.pawn.Weapon,10.0)
 def test_neutral_and_ads_only(self):
  self.assertTrue(self.eligible()); self.pi.aStrafe=1; self.assertFalse(self.eligible())
  self.ns['_ads_sprint_edge']=(55,10.2); self.assertTrue(self.eligible())
  self.ns['_ads_sprint_edge']=(55,9.9); self.assertFalse(self.eligible())
  self.ns['_fast_zoomed']=lambda *a:True; self.assertTrue(self.eligible())
 def test_safety_guards(self):
  self.ns['_ads_sprint_edge']=(55,10.1)
  for obj,field,value in [(self.pawn,'Physics',2),(self.pawn,'bForceRMVelocity',True),(self.pc,'bDuck',True),(self.pc,'bPressedJump',True),(self.pawn,'DrivenVehicle',object())]:
   before=getattr(obj,field);setattr(obj,field,value);self.assertFalse(self.eligible(),field);setattr(obj,field,before)
  self.ns['_knockback_until']=10.1;self.assertFalse(self.eligible());self.ns['_knockback_until']=0
  self.ns['_interrupted']=lambda pc:True;self.assertFalse(self.eligible())
  self.ns['_interrupted']=lambda pc:False;self.ns['_armed']=False;self.assertFalse(self.eligible())
 def test_alt_fire_marks_ads_before_native_call_but_not_gunzerking(self):
  self.ns.update(_local_pc=lambda obj:self.pc,_combat_fire_edge=lambda *a:None,time=N(perf_counter=lambda:10),_fire_requested=False,_pose_offhand_requested=False,_pose_release_at=0,_hip_hooks_until=0)
  load({'_start_fire'},self.ns)
  self.ns['_start_fire'](self.pc,N(FireModeNum=1),None,None)
  self.assertEqual(self.ns['_ads_sprint_edge'],(55,10.25))
  self.ns['_ads_sprint_edge']=None;self.pawn.OffHandWeapon=N(); self.ns['_cfg_hip_fire']=False
  self.ns['_start_fire'](self.pc,N(FireModeNum=1),None,None)
  self.assertIsNone(self.ns['_ads_sprint_edge'])
if __name__=='__main__':unittest.main()
