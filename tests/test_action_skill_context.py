"""Acceptance-level structural regressions for class-defined BL2 skill contexts.

Compiles the actual source predicates without loading SDK objects. Class input
context names come from the BL2 exported PlayerClassDefinition assets:
Axton/Scorpio, Maya/Phaselock, Salvador/Gunzerking,
Zer0/VisionMode and Execute, Krieg/Buzzaxe, Gaige/DeathTrap.
"""
import ast
from pathlib import Path
from types import SimpleNamespace as NS
import unittest

MOD = Path(__file__).resolve().parents[1] / "mod" / "OmniMovement"
OMNI = (MOD / "__init__.py").read_text(encoding="utf-8-sig")
POSE = (MOD / "fire_pose.py").read_text(encoding="utf-8-sig")


def function(tree, name):
    return next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name)


def load():
    scope = {"_cfg_assist": True, "_deathtrap_skill_active": False,
             "_same": lambda a, b: a is b}
    for source, names in (
        (POSE, ("on_foot", "firing_pose_context")),
        (OMNI, ("_basic_context", "_skill_assist_context", "_assist_context")),
    ):
        tree = ast.parse(source)
        nodes = [function(tree, name) for name in names]
        exec(compile(ast.Module(body=nodes, type_ignores=[]), "<skill-assist>", "exec"),
             scope)
    return scope


class SkillAssistContextTests(unittest.TestCase):
    def setUp(self):
        self.scope = load()
        self.pc = NS()
        self.pc.PlayerInput = NS(CurrentInputContextName="DefaultOnFoot")
        self.pc.Pawn = NS(Controller=self.pc, DrivenVehicle=None, Physics=1,
                          bIsInjured=False, bIsDead=False, MyActionSkill=object())
        self.pc.bCinematicMode = False
        self.pc.bIgnoreMoveInput = False
        self.running = True
        self.pc.Pawn.IsActionSkillRunning = lambda: self.running
        self.pc.PlayerClass = NS(InputContexts=[])
        self.context = self.scope["_assist_context"]
        self.skill = self.scope["_skill_assist_context"]

    def configure(self, context):
        self.pc.PlayerInput.CurrentInputContextName = context
        self.pc.PlayerClass.InputContexts = [NS(ContextName=context)]

    def test_all_six_playable_character_skill_contexts(self):
        for char, context in (
            ("Axton", "Scorpio"),
            ("Zer0", "VisionMode"),
            ("Zer0 executing", "Execute"),
            ("Maya", "Phaselock"),
            ("Salvador", "Gunzerking"),
            ("Krieg", "Buzzaxe"),
        ):
            with self.subTest(char=char):
                self.configure(context)
                self.assertTrue(self.context(self.pc),
                                f"{char} must not lose Sprint Assist in {context}")
        # Gaige's DeathTrap is already accepted by the existing on_foot path.
        self.configure("DeathTrap")
        self.assertTrue(self.context(self.pc))

    def test_accepts_only_live_class_declared_skill_contexts(self):
        self.configure("Scorpio")
        self.assertTrue(self.skill(self.pc))
        self.running = False
        self.assertFalse(self.skill(self.pc))
        self.running = True
        self.pc.Pawn.MyActionSkill = None
        self.assertFalse(self.skill(self.pc))
        self.pc.Pawn.MyActionSkill = object()
        self.pc.PlayerInput.CurrentInputContextName = "CustomInjectedContext"
        self.assertFalse(self.skill(self.pc))
        self.pc.PlayerClass.InputContexts = []
        self.assertFalse(self.skill(self.pc))

    def test_special_context_guard_checks_current_local_owner(self):
        self.configure("Gunzerking")
        self.assertTrue(self.skill(self.pc))
        other_pi = NS(CurrentInputContextName="Gunzerking")
        self.assertFalse(self.skill(self.pc, pi=other_pi))
        other_pawn = NS(**vars(self.pc.Pawn))
        self.assertFalse(self.skill(self.pc, pawn=other_pawn))
        self.pc.Pawn.Controller = object()
        self.assertFalse(self.skill(self.pc))

    def test_vehicle_cinematic_injury_and_physics_rejected(self):
        self.configure("Buzzaxe")
        self.pc.Pawn.DrivenVehicle = object()
        self.assertFalse(self.context(self.pc))
        self.pc.Pawn.DrivenVehicle = None
        self.pc.Pawn.Physics = 6
        self.assertFalse(self.context(self.pc))
        self.pc.Pawn.Physics = 1
        self.pc.Pawn.bIsInjured = True
        self.assertFalse(self.context(self.pc))
        self.pc.Pawn.bIsInjured = False
        self.pc.bCinematicMode = True
        self.assertFalse(self.context(self.pc))
        self.pc.bCinematicMode = False
        self.pc.bIgnoreMoveInput = True
        self.assertFalse(self.context(self.pc))
        self.pc.bIgnoreMoveInput = False
        self.scope["_deathtrap_skill_active"] = True
        self.assertFalse(self.context(self.pc))

    def test_default_context_does_not_require_skill(self):
        self.pc.Pawn.MyActionSkill = None
        self.running = False
        self.assertTrue(self.context(self.pc))
        self.pc.PlayerInput.CurrentInputContextName = "FakeContext"
        self.assertFalse(self.context(self.pc))

    def test_native_sprint_veto_and_general_gate_unchanged(self):
        tree = ast.parse(OMNI)
        def body(name):
            return ast.get_source_segment(OMNI, function(tree, name))
        self.assertIn("pawn.CanSprint()", body("_assist_can_retry"))
        self.assertIn("_assist_context(", body("_assist_recovery_tick"))
        self.assertIn("_assist_context(", body("_pressed"))
        self.assertNotIn("_assist_context(", body("_allows"))
        self.assertNotIn("_skill_assist_context(", body("_allows"))
        self.assertNotIn("_skill_assist_context(", body("_basic_context"))
        self.assertNotIn("_skill_assist_context(", body("_write_reversal_velocity"))
        self.assertNotIn("PLAYER_TICK", body("_skill_assist_context"))
        self.assertNotIn("Scorpio", body("_skill_assist_context").split("return bool(",1)[-1])


if __name__ == "__main__":
    unittest.main()
