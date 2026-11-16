"""BUG-09: recover individual first-person animation aliases after failed writes.

Runs the production FirePose and AdaptiveFirePose via existing UnrealSDK stubs.
No claims about actual UE3 struct setter atomicity or in-game animation quality.
"""
from test_salvador_pose import Obj, PoseTreeTests


class FaultyAnimLink:
    """UE reflected child which can raise before or after a single assignment."""
    def __init__(self, name, anim):
        self.Name = name
        self._anim = anim
        self.fail_before = 0
        self.fail_after = 0
        self.write_attempts = 0

    @property
    def Anim(self):
        return self._anim

    @Anim.setter
    def Anim(self, value):
        self.write_attempts += 1
        if self.fail_before:
            self.fail_before -= 1
            raise RuntimeError("field rejected before write")
        self._anim = value
        if self.fail_after:
            self.fail_after -= 1
            raise RuntimeError("field raised after write")


class AliasOwnershipTests(PoseTreeTests):
    # Inherit all existing pose behavior tests as an additional compatibility
    # gate; the new tests below focus on ownership/failure injection.

    def setup_links(self):
        node, falling, normal, sprint = self.attach_branch("main")
        original = [sprint] + [falling.Children[i].Anim for i in (1, 2, 3)]
        node.Children[2] = FaultyAnimLink("Sprinting", sprint)
        for index in (1, 2, 3):
            falling.Children[index] = FaultyAnimLink(
                ("", "Start", "Idle", "End")[index], original[index]
            )
        links = [node.Children[2]] + [falling.Children[i] for i in (1, 2, 3)]
        return node, falling, normal, original, links

    def test_each_initial_link_failure_rolls_back_all_prior_successes(self):
        for failure in ("fail_before", "fail_after"):
            for index in range(4):
                with self.subTest(failure=failure, link=index):
                    self.setUp()
                    node, falling, normal, original, links = self.setup_links()
                    pose = self.ns["FirePose"]()
                    setattr(links[index], failure, 1)
                    self.assertFalse(pose._ensure_aliases(self.pawn, node))
                    self.assertFalse(pose.aliases_active)
                    self.assertEqual(
                        [link.Anim for link in links], original)
                    self.assertFalse(pose._alias_links)
                    # A recoverable one-shot exception must not disable a
                    # later legitimate, verified firing animation redirect.
                    self.assertTrue(pose._ensure_aliases(self.pawn, node))
                    self.assertEqual(
                        [link.Anim for link in links],
                        [normal, node, node, node])
                    pose._release_aliases(node)
                    self.assertEqual([link.Anim for link in links], original)

    def test_each_restore_link_failure_retains_only_one_weak_owner(self):
        for index in range(4):
            with self.subTest(link=index):
                self.setUp()
                node, falling, normal, original, links = self.setup_links()
                pose = self.ns["FirePose"]()
                self.assertTrue(pose._ensure_aliases(self.pawn, node))
                links[index].fail_before = 1
                pose._release_aliases(node)
                self.assertFalse(pose.aliases_active)
                self.assertEqual(len(pose._alias_links), 1)
                self.assertIs(links[index].Anim, normal if index == 0 else node)
                for i, child in enumerate(links):
                    if i != index:
                        self.assertIs(child.Anim, original[i])
                pose._release_aliases(node)
                self.assertEqual([link.Anim for link in links], original)
                self.assertFalse(pose._alias_links)

    def test_restore_write_then_throw_counts_as_verified_success(self):
        node, falling, normal, original, links = self.setup_links()
        pose = self.ns["FirePose"]()
        self.assertTrue(pose._ensure_aliases(self.pawn, node))
        links[2].fail_after = 1
        pose._release_aliases(node)
        self.assertEqual([link.Anim for link in links], original)
        self.assertEqual(pose._alias_links, [])

    def test_partial_apply_with_failed_rollback_remains_retryable(self):
        node, falling, normal, original, links = self.setup_links()
        pose = self.ns["FirePose"]()
        links[2].fail_before = 1  # Third application fails after two writes.
        # One of those earlier writes then fails on rollback.
        orig_setter = type(links[1]).Anim.fset
        calls = []
        # Rather than monkeypatch a class-level property, use an independent
        # setter that fails only when restoring this original.
        class RestoreFault(FaultyAnimLink):
            @FaultyAnimLink.Anim.setter
            def Anim(self, value):
                if value is original[1] and not calls:
                    calls.append("failed")
                    raise RuntimeError("restore interrupted")
                orig_setter(self, value)
        links[1] = falling.Children[1] = RestoreFault("Start", original[1])
        self.assertFalse(pose._ensure_aliases(self.pawn, node))
        self.assertEqual(len(pose._alias_links), 1)
        self.assertIs(links[1].Anim, node)
        # Refuse to install another layer while cleanup of this graph is pending.
        # The next attempt may retry restoration and then apply cleanly.
        self.assertTrue(pose._ensure_aliases(self.pawn, node))
        self.assertTrue(pose.aliases_active)
        pose._release_aliases(node)
        self.assertEqual([link.Anim for link in links], original)
        self.assertFalse(pose._alias_links)

    def test_foreign_link_replacement_remains_untouched_across_future_updates(self):
        node, falling, normal, original, links = self.setup_links()
        pose = self.ns["FirePose"]()
        self.assertTrue(pose._ensure_aliases(self.pawn, node))
        foreign = Obj("other-mod-source")
        links[2].Anim = foreign
        pose._release_aliases(node)
        self.assertIs(links[2].Anim, foreign)
        self.assertFalse(pose._alias_links)
        self.assertFalse(pose._ensure_aliases(self.pawn, node))
        self.assertIs(links[2].Anim, foreign)
        for i in (0, 1, 3):
            self.assertIs(links[i].Anim, original[i])

    def test_weak_owner_expiration_relinquishes_without_writing_stale_graph(self):
        node, falling, normal, original, links = self.setup_links()
        pose = self.ns["FirePose"]()
        self.assertTrue(pose._ensure_aliases(self.pawn, node))
        # Simulate UnrealSDK WeakPointer returning None after UObject retirement.
        # No dereference/write should occur on that link.
        stale = next(r for r in pose._alias_links if r["index"] == 2
                     and r["parent_address"] == node.addr)
        stale["parent"] = lambda: None
        pose._release_aliases(node)
        self.assertIs(links[0].Anim, normal)
        self.assertEqual([link.Anim for link in links[1:]], original[1:])
        self.assertEqual(pose._alias_links, [])

    def test_same_path_replacement_with_new_address_is_not_treated_as_owner(self):
        node, falling, normal, original, links = self.setup_links()
        pose = self.ns["FirePose"]()
        self.assertTrue(pose._ensure_aliases(self.pawn, node))
        new_parent = Obj("replacement", "AnimNodeBlendByPosture", path=node.path)
        new_parent.Children = list(node.Children)
        old = next(r for r in pose._alias_links if r["index"] == 2
                   and r["parent_address"] == node.addr)
        old["parent"] = lambda: new_parent
        pose._release_aliases(node)
        self.assertIs(new_parent.Children[2].Anim, normal)
        self.assertIs(node.Children[2].Anim, normal)
        self.assertFalse(pose._alias_links)

    def test_persistent_failure_has_bounded_attempts_and_retains_weak_evidence(self):
        node, falling, normal, original, links = self.setup_links()
        pose = self.ns["FirePose"]()
        self.assertTrue(pose._ensure_aliases(self.pawn, node))
        links[1].fail_before = 15
        for _ in range(8):
            pose._release_aliases(node)
        self.assertEqual(len(pose._alias_links), 1)
        self.assertEqual(pose._alias_links[0]["attempts"], 3)
        # Three restoration attempts maximum. No ongoing high-frequency retry.
        self.assertEqual(links[1].write_attempts, 4)  # 1 install + 3 restore.
        self.assertIs(links[1].Anim, node)

    def test_retired_foreign_modified_branch_is_not_realiased_on_next_event(self):
        node, falling, normal, original, links = self.setup_links()
        off, off_falling, off_normal, off_sprint = self.attach_branch("offhand")
        pose = self.pose()
        pose.update(self.pc, eligible=True, firing=True, now=1.0)
        links[1].fail_before = 1
        pose.restore()
        self.assertEqual(len(pose._retired_aliases), 1)
        foreign = Obj("foreign-jump-alias")
        links[1].Anim = foreign
        pose.restore()  # A later reconciliation notices and relinquishes it.
        self.assertEqual(len(pose._retired_aliases), 1)
        self.assertIs(links[1].Anim, foreign)
        self.assertFalse(pose._retired_aliases[0]._alias_links)
        self.assertEqual(
            pose._retired_aliases[0]._alias_blocked_node_address, node.addr)
        pose.update(self.pc, eligible=True, firing=True, now=2.0)
        self.assertIn("foreign-alias-owner", pose.fallback_status)
        self.assertIs(links[1].Anim, foreign)
        self.assertIs(node.Children[2].Anim, original[0])
        self.assertIs(off.Children[2].Anim, off_sprint)

    def test_foreign_quarantine_expires_when_weak_root_retires(self):
        node, falling, normal, original, links = self.setup_links()
        off, off_falling, off_normal, off_sprint = self.attach_branch("offhand")
        pose = self.pose()
        pose.update(self.pc, eligible=True, firing=True, now=1.0)
        links[1].fail_before = 1
        pose.restore()
        links[1].Anim = Obj("new-external-owner")
        pose.restore()
        self.assertEqual(len(pose._retired_aliases), 1)
        pose._retired_aliases[0]._alias_root = lambda: None
        pose._drain_retired_aliases()
        self.assertEqual(pose._retired_aliases, ())

    def test_retired_adaptive_branch_survives_failed_restore_for_later_retry(self):
        node, falling, normal, original, links = self.setup_links()
        off, off_falling, off_normal, off_sprint = self.attach_branch("offhand")
        pose = self.pose()
        pose.update(self.pc, eligible=True, firing=True, now=1.0)
        self.assertEqual(len(pose._branches), 2)
        links[1].fail_before = 1
        pose.restore()
        self.assertFalse(pose.aliases_active)
        self.assertEqual(len(pose._retired_aliases), 1)
        self.assertIs(links[1].Anim, node)
        pose.restore()
        self.assertEqual(len(pose._retired_aliases), 0)
        self.assertEqual([link.Anim for link in links], original)
        self.assertIs(off.Children[2].Anim, off_sprint)


if __name__ == "__main__":
    import unittest
    unittest.main()
