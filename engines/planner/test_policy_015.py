"""Regression tests for supported-platform convergence in kernel-backport 0.1.5."""
import unittest

import test_kernel_backport_planner as _legacy_tests

kbp = _legacy_tests.kbp


class ConvergencePolicy015Tests(unittest.TestCase):
    def planner(self):
        return _legacy_tests.AdvisorTests().minimal_planner()

    def test_policy_and_prompt_version_are_new(self):
        self.assertEqual(kbp.ADVISOR_PROMPT_VERSION, "36")
        text = self.planner().advisor_system_prompt()
        for required in (
            "upstream convergence within RHEL-supported architectures",
            "a separate downstream bug report or quantified benefit is NOT required",
            "SKIP when there is POSITIVE evidence",
            "REVIEW when support for a board/SoC cannot be established",
            "original\n  in-scope change and the later fix(es)",
            "FAVOR_IN_SCOPE_CONVERGENCE",
            "Fixes: tag is a historical regression/fix relationship",
        ):
            with self.subTest(required=required):
                self.assertIn(required, text)

    def test_direct_in_scope_shared_code_favors_convergence_across_architectures(self):
        for arch in ("arm64", "x86", "powerpc", "s390"):
            with self.subTest(arch=arch):
                p = self.planner()
                p.paths = [f"arch/{arch}/"]
                p.active_architectures = {arch}
                node = kbp.CommitNode("a" * 40, "shared arch change", direct=True,
                                      files=[f"arch/{arch}/kernel/foo.c"])
                p.nodes = {node.sha: node}
                p.subject = lambda sha: node.subject
                p.changed_files = lambda sha: node.files
                p.patch_excerpt = lambda sha, limit: "+some shared code"
                p.body = lambda sha: node.subject
                packet = p.advisor_packet(node.sha)
                self.assertEqual(packet["commit"]["default_policy_bias"],
                                 "FAVOR_IN_SCOPE_CONVERGENCE")
                self.assertIn("Upstream convergence", packet["planning_context"]["policy"])

    def test_unmatched_board_policy_stays_authoritative(self):
        helpers = _legacy_tests.ScopeAndFallback0431Tests()
        p = helpers.planner(unmatched="REVIEW",
                            path="arch/arm64/boot/dts/vendor/unlisted-board.dts")
        packet = {"commit": {"platform_relevance": p.platform_relevance("a" * 40),
                             "scope": {}, "actual_inbound_take_prerequisite_count": 0}}
        self.assertEqual(p.advisor_policy_override("a" * 40, packet)["decision"], "REVIEW")
        p.platform_profile = helpers.profile(unmatched_board="TAKE")
        self.assertIsNone(p.advisor_policy_override("a" * 40, packet))

    def test_excluded_board_is_skipped_without_verified_inbound_need(self):
        helpers = _legacy_tests.ScopeAndFallback0431Tests()
        p = helpers.planner(excluded=[helpers.excluded_board()],
                            path="arch/arm64/boot/dts/vendor/other-micro.dts")
        packet = {"commit": {"platform_relevance": p.platform_relevance("a" * 40),
                             "scope": {}, "actual_inbound_take_prerequisite_count": 0}}
        self.assertEqual(p.advisor_policy_override("a" * 40, packet)["decision"], "SKIP")
        packet["commit"]["actual_inbound_take_prerequisite_count"] = 1
        self.assertIsNone(p.advisor_policy_override("a" * 40, packet))

    def test_fully_excluded_driver_path_is_skipped_without_dts_requirement(self):
        helpers = _legacy_tests.ScopeAndFallback0431Tests()
        p = helpers.planner(excluded=[{
            "name": "Unsupported peripheral driver", "architecture": "arm64",
            "paths": ["drivers/firmware/unsupported/*.c"],
        }], path="drivers/firmware/unsupported/device.c")
        relevance = p.platform_relevance("a" * 40)
        self.assertEqual(relevance["status"], "EXCLUDED_PLATFORM_ONLY")
        packet = {"commit": {"platform_relevance": relevance,
                             "scope": {}, "actual_inbound_take_prerequisite_count": 0}}
        self.assertEqual(p.advisor_policy_override("a" * 40, packet)["decision"], "SKIP")
        packet["commit"]["actual_inbound_take_prerequisite_count"] = 1
        self.assertIsNone(p.advisor_policy_override("a" * 40, packet))

    def test_partially_excluded_driver_plus_shared_code_not_skipped(self):
        helpers = _legacy_tests.ScopeAndFallback0431Tests()
        p = helpers.planner(excluded=[{
            "name": "Unsupported peripheral driver", "architecture": "arm64",
            "paths": ["drivers/firmware/unsupported/*.c"],
        }], path="drivers/firmware/unsupported/device.c")
        p.nodes["a" * 40].files = [
            "drivers/firmware/unsupported/device.c", "kernel/sched/core.c",
        ]
        p._platform_match_cache.clear()
        p._platform_excluded_match_cache.clear()
        relevance = p.platform_relevance("a" * 40)
        self.assertEqual(relevance["status"], "SHARED_OR_UNKNOWN")
        packet = {"commit": {"platform_relevance": relevance,
                             "scope": {}, "actual_inbound_take_prerequisite_count": 0}}
        self.assertIsNone(p.advisor_policy_override("a" * 40, packet))

    def test_unsupported_architecture_still_excluded_deterministically(self):
        p = self.planner()
        result = p.advisor_policy_override("a" * 40, {
            "commit": {"scope": {"unsupported_architecture_only": True,
                                   "architecture_dirs": ["loongarch"]},
                       "actual_inbound_take_prerequisite_count": 0},
            "planning_context": {"product": {"product": "RHEL 10"}},
        })
        self.assertEqual(result["decision"], "SKIP")

    def test_fixes_links_retain_status_not_hard_dependency(self):
        p = self.planner()
        original = kbp.CommitNode("a" * 40, "original", direct=True)
        fix = kbp.CommitNode("b" * 40, "later fix", direct=True)
        original.fixes_referenced_by.add(fix.sha)
        fix.fixes.add(original.sha)
        p.nodes = {original.sha: original, fix.sha: fix}
        p.subject = lambda sha: {original.sha: "original", fix.sha: "later fix"}[sha]
        details = p.advisor_fixes_link(original.sha, "fixes-reference")
        self.assertEqual(details["downstream_status"], "missing")
        self.assertTrue(details["selected_for_evaluation"])
        self.assertFalse(details["hard_prerequisite"])
        self.assertEqual(original.required_by, set())
        original.match = kbp.Match(status="present", method="patch-id")
        self.assertEqual(p.advisor_fixes_link(original.sha, "fixes-reference")[
            "downstream_status"], "present")
        p.nodes.pop(original.sha)
        self.assertEqual(p.advisor_fixes_link(original.sha, "fixes-reference")[
            "downstream_status"], "unknown")

    def test_fixes_reference_context_not_mistaken_for_actionable_commit(self):
        p = self.planner()
        original = kbp.CommitNode("a" * 40, "original")
        original.add_reason("Fixes-target", "b" * 40, tentative=True)
        p.nodes = {original.sha: original}
        p.subject = lambda sha: original.subject
        self.assertFalse(p.is_actionable_node(original))
        evidence = p.advisor_fixes_link(original.sha, "fixes-reference")
        self.assertFalse(evidence["selected_for_evaluation"])
        self.assertFalse(evidence["hard_prerequisite"])
        self.assertEqual(evidence["downstream_status"], "missing")


if __name__ == "__main__":
    unittest.main()
