"""BUG-07: mandatory OmniMovement sprint-hook ownership and enable rollback.

Executes selected production methods against the documented mods_base hook and
enable semantics. Offline tests do not establish in-game sprint correctness.
"""
import ast
import builtins
from pathlib import Path
from types import SimpleNamespace as NS
import unittest

SOURCE = Path(__file__).resolve().parents[1] / "mod" / "OmniMovement" / "__init__.py"
METHODS = {"_set_hook", "_set_optional_hook", "_remove_all_hooks",
           "_sync_feature_hooks", "_option_changed"}


class FakeHooks:
    def __init__(self):
        self.active = {}
        self.refuse = None
        self.add_then_raise = None
        self.take_during_add = None
        self.install_but_return_false = None

    def has_hook(self, *key):
        return tuple(key) in self.active

    def add_hook(self, path, kind, ident, callback):
        key = (path, kind, ident)
        if key == self.take_during_add:
            self.active[key] = "foreign"
            return False
        if key == self.refuse or key in self.active:
            return False
        self.active[key] = callback
        if key == self.install_but_return_false:
            return False
        if key == self.add_then_raise:
            raise RuntimeError("hook installed before exception")
        return True

    def remove_hook(self, path, kind, ident):
        self.active.pop((path, kind, ident), None)
        return True


class SDKMod:
    """Model the installed mods_base ordering: Enabled before on_enable."""
    def __init__(self, on_enable, on_disable):
        self.on_enable = on_enable
        self.on_disable = on_disable
        self.is_enabled = False
        self.saved = []

    def enable(self):
        if self.is_enabled:
            return
        self.is_enabled = True
        self.on_enable()
        self.saved.append(True)

    def disable(self, dont_update_setting=False):
        if not self.is_enabled:
            return
        self.is_enabled = False
        self.on_disable()
        if not dont_update_setting:
            self.saved.append(False)


def make_runtime(*, omni=True, assist=False):
    tree = ast.parse(SOURCE.read_text(encoding="utf-8-sig"), filename=str(SOURCE))
    functions = [node for node in tree.body if isinstance(node, ast.FunctionDef)
                 and node.name in METHODS]
    assert len(functions) == len(METHODS)
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef)
               and node.name == "OmniMovementMod")
    enable = next(node for node in cls.body if isinstance(node, ast.FunctionDef)
                  and node.name == "enable")
    fake_cls = ast.ClassDef(name="TestOmniMod", bases=[ast.Name(id="Mod", ctx=ast.Load())],
                            keywords=[],body=[enable],decorator_list=[])
    module = ast.fix_missing_locations(ast.Module(body=functions+[fake_cls],type_ignores=[]))

    logs = []
    hooks = FakeHooks()
    ty = NS(PRE=0, POST=1, POST_UNCONDITIONAL=2)
    namespace = dict(
        Mod=SDKMod,
        logging=NS(warning=lambda msg:logs.append(str(msg))),
        unrealsdk=NS(hooks=hooks),
        Type=ty,
        _hooks={},
        _pose_reload_addresses=set(),
        _hook_id=lambda tag:"OmniMovement/"+tag,
        _function_exists=lambda _:False,  # Optional endpoints may be absent.
        _active=False,
        _cfg_omni=omni,
        _cfg_assist=assist,
        _cfg_hip_fire=False,
        _cfg_crosshair=False,
        _cfg_baseline=False,
        BASELINE_AVAILABLE=False,
        _cfg_direction="vanilla",
        _cfg_starting=False,
        _coyote_enabled=lambda:False,
        _sync_coyote_hooks=lambda:None,
        _sync_weapon_firing_end_hook=lambda *_:None,
        _sync_sprint_hooks=lambda:None,
        _install_weapon_state_hooks=lambda:None,
        _remove_weapon_state_hooks=lambda:None,
        _clear_assist=lambda:None,
        _restore_baseline=lambda:None,
        _clear_direction=lambda:None,
        _clear_center_transition=lambda:None,
        _reset_gate_grace=lambda:None,
        _sync_config=lambda overrides:namespace.update({
            "_cfg_omni":bool(overrides.get("omni",namespace["_cfg_omni"])),
            "_cfg_assist":bool(overrides.get("assist",namespace["_cfg_assist"]))}),
    )
    # Handler functions and engine path constants are irrelevant to this test,
    # but the production synchronization function must execute in full.
    required = {node.id for node in ast.walk(module)
                if isinstance(node, ast.Name) and isinstance(node.ctx,ast.Load)}
    for key in required - set(namespace) - set(dir(builtins)):
        if key.isupper():
            namespace[key] = key
        elif key.startswith("_"):
            namespace[key] = lambda *args, **kwargs:True
    exec(compile(module,str(SOURCE),"exec"),namespace)
    def start():
        namespace["_active"] = True
        namespace["_sync_feature_hooks"]()
    def stop():
        namespace["_active"] = False
        namespace["_remove_all_hooks"]()
    mod = namespace["TestOmniMod"](on_enable=start,on_disable=stop)
    namespace["mod"] = mod
    return namespace,mod,hooks,logs


def target(ns, tag, path):
    return (ns[path],ns["Type"].PRE,"OmniMovement/"+tag)


class RequiredSprintHookTests(unittest.TestCase):
    def test_unavailable_allows_sprint_aborts_enabled_state_and_has_no_owned_hooks(self):
        ns,mod,hooks,logs=make_runtime(omni=True,assist=True)
        hooks.refuse=target(ns,"allows-sprint","INPUT_ALLOWS_SPRINT")
        mod.enable()
        self.assertFalse(mod.is_enabled)
        self.assertFalse(ns["_active"])
        self.assertEqual(hooks.active,{})
        self.assertEqual(ns["_hooks"],{})
        self.assertEqual(mod.saved,[])
        self.assertTrue(any("InputAllowsSprint" in msg for msg in logs))

    def test_unavailable_sprint_press_rolls_back_earlier_owned_gate(self):
        ns,mod,hooks,logs=make_runtime(omni=True,assist=True)
        hooks.refuse=target(ns,"sprint-pressed","PRESSED")
        mod.enable()
        self.assertFalse(mod.is_enabled)
        self.assertFalse(ns["_active"])
        self.assertEqual(hooks.active,{})
        self.assertEqual(ns["_hooks"],{})
        self.assertEqual(mod.saved,[])

    def test_assist_alone_requires_sprint_press_but_not_omni_gate(self):
        ns,mod,hooks,_=make_runtime(omni=False,assist=True)
        hooks.refuse=target(ns,"sprint-pressed","PRESSED")
        mod.enable()
        self.assertFalse(mod.is_enabled)
        self.assertFalse(hooks.active)
        self.assertFalse(ns["_hooks"])

    def test_optional_hooks_missing_do_not_disable_valid_sprint_features(self):
        ns,mod,hooks,_=make_runtime(omni=True,assist=True)
        mod.enable()
        self.assertTrue(mod.is_enabled)
        self.assertTrue(ns["_active"])
        self.assertIn("allows-sprint",ns["_hooks"])
        self.assertIn("sprint-pressed",ns["_hooks"])
        self.assertEqual(mod.saved,[True])
        mod.disable(dont_update_setting=True)
        self.assertEqual(hooks.active,{})
        self.assertEqual(ns["_hooks"],{})

    def test_post_registration_exception_still_cleans_owned_hook(self):
        ns,mod,hooks,_=make_runtime(omni=True)
        hooks.add_then_raise=target(ns,"sprint-pressed","PRESSED")
        hooks.refuse=target(ns,"assist-released","RELEASED")
        # Add-then-raise for mandatory SprintPressed must be recorded and
        # ultimately removed by a later explicit disable.
        mod.enable()
        self.assertIn("sprint-pressed",ns["_hooks"])
        mod.disable(dont_update_setting=True)
        self.assertFalse(hooks.active)

    def test_sdk_false_after_successful_install_remains_supported(self):
        ns, mod, hooks, _ = make_runtime(omni=True)
        key = target(ns, "sprint-pressed", "PRESSED")
        hooks.install_but_return_false = key
        mod.enable()
        self.assertTrue(mod.is_enabled)
        self.assertIn("sprint-pressed", ns["_hooks"])
        self.assertNotEqual(hooks.active[key], "foreign")
        mod.disable(dont_update_setting=True)
        self.assertNotIn(key, hooks.active)

    def test_production_hook_ids_are_distinct_across_attempts_and_instances(self):
        tree = ast.parse(SOURCE.read_text(encoding="utf-8-sig"))
        fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_hook_id")
        module = ast.Module(body=[fn], type_ignores=[])
        first = {"_hook_instance_nonce": "a" * 32, "_hook_sequence": 0}
        second = {"_hook_instance_nonce": "b" * 32, "_hook_sequence": 0}
        exec(compile(module, str(SOURCE), "exec"), first)
        exec(compile(module, str(SOURCE), "exec"), second)
        self.assertEqual(len({first["_hook_id"]("sprint-pressed"),
                              first["_hook_id"]("sprint-pressed"),
                              second["_hook_id"]("sprint-pressed")}), 3)

    # Maintainer-accepted, pinned-SDK ownership ambiguity. This test still runs;
    # any unexpected success or other test failure remains a CI failure.
    @unittest.expectedFailure
    def test_add_false_after_foreign_race_is_never_claimed_or_removed(self):
        ns,mod,hooks,_=make_runtime(omni=True)
        key=target(ns,"sprint-pressed","PRESSED")
        hooks.take_during_add=key
        mod.enable()
        self.assertFalse(mod.is_enabled)
        self.assertEqual(hooks.active[key],"foreign")
        self.assertNotIn("sprint-pressed",ns["_hooks"])

    def test_stale_previous_registration_does_not_report_success(self):
        ns,mod,hooks,_=make_runtime(omni=True)
        key=target(ns,"allows-sprint","INPUT_ALLOWS_SPRINT")
        ns["_hooks"]["allows-sprint"]=key
        hooks.refuse=key
        mod.enable()
        self.assertFalse(mod.is_enabled)
        self.assertNotIn("allows-sprint",ns["_hooks"])

    def test_option_change_failure_disables_instead_of_leaving_partial_feature(self):
        ns,mod,hooks,_=make_runtime(omni=False,assist=False)
        mod.enable()
        self.assertTrue(mod.is_enabled)
        hooks.refuse=target(ns,"allows-sprint","INPUT_ALLOWS_SPRINT")
        ns["_option_changed"](NS(identifier="omni"),True)
        self.assertFalse(mod.is_enabled)
        self.assertFalse(ns["_active"])
        self.assertEqual(hooks.active,{})
        self.assertEqual(ns["_hooks"],{})
        self.assertEqual(mod.saved,[True])


if __name__=="__main__":
    unittest.main()
