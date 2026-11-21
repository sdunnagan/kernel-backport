"""Regression tests: Fixes: is regression provenance, not a hard prerequisite."""
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from test_kernel_backport_planner import kbp


def git(repo, *args):
    return subprocess.check_output(["git", *args], cwd=repo, text=True,
                                   stderr=subprocess.PIPE).strip()


class FixesRelationshipTests(unittest.TestCase):
    def setUp(self):
        context = tempfile.TemporaryDirectory()
        self.addCleanup(context.cleanup)
        self.repo = Path(context.name)
        git(self.repo, "init", "-q", "-b", "main")
        git(self.repo, "config", "user.name", "Fixes regression")
        git(self.repo, "config", "user.email", "test@example.invalid")
        (self.repo / "README").write_text("base\n")
        git(self.repo, "add", "README")
        git(self.repo, "commit", "-qm", "base")
        self.base = git(self.repo, "rev-parse", "HEAD")
        (self.repo / "arch/arm64").mkdir(parents=True)
        (self.repo / "arch/arm64/feature.c").write_text("original change\n")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-qm", "original: optimization")
        self.original = git(self.repo, "rev-parse", "HEAD")
        (self.repo / "arch/arm64/fix.c").write_text("fix code\n")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-qm", "fix: avoid an issue\n\nFixes: " + self.original[:12] + " (original)")
        self.fix = git(self.repo, "rev-parse", "HEAD")
        git(self.repo, "branch", "downstream", self.base)
        args = kbp.parse_args(["--repo", str(self.repo), "--base", self.base,
                               "--target", self.fix, "--downstream-ref", "downstream",
                               "--path", "arch/arm64/", "--no-patch-id"])
        self.planner = kbp.Planner(args)
        self.planner.build_downstream_index()

    def test_fixes_target_is_only_an_informational_candidate(self):
        fix = self.planner.ensure_node(self.fix, direct=True, reason="focus-path")
        self.assertIn(self.original, fix.fixes)
        self.assertEqual(self.planner.add_explicit_fixes_closure([fix]), 0)
        orig = self.planner.nodes[self.original]
        self.assertEqual(orig.reasons, {"Fixes-target"})
        self.assertEqual(orig.required_by, set())
        self.assertEqual(orig.triggered_by, {self.fix})
        self.assertEqual(orig.fixes_referenced_by, {self.fix})
        self.assertEqual(self.planner.dependency_kind(orig), "dependency-candidate")
        self.assertFalse(self.planner.is_actionable_node(orig))
        self.assertTrue(self.planner.is_actionable_node(fix))
        worksheet = self.planner.report_text()
        self.assertIn("fixes-reference: " + self.original[:12], worksheet)
        self.assertIn("NOT a hard prerequisite", worksheet)
        self.assertNotIn(self.original[:12] + " [DEPENDENCY]", worksheet)
        data = json.loads(self.planner.report_json())
        self.assertEqual(data["commits"][self.original]["required_by"], [])
        self.assertEqual(data["commits"][self.original]["fixes_referenced_by"], [self.fix])
        self.assertFalse(data["commits"][self.original]["selected"])
        self.assertEqual(data["commits"][self.fix]["fixes"], [self.original])

    def test_real_dependency_proof_still_promotes_fixes_target(self):
        fix = self.planner.ensure_node(self.fix, direct=True, reason="focus-path")
        self.planner.add_explicit_fixes_closure([fix])
        orig = self.planner.nodes[self.original]
        orig.add_reason("hunk-preimage:3", self.fix)
        self.assertEqual(orig.required_by, {self.fix})
        self.assertTrue(self.planner.is_actionable_node(orig))
        self.assertEqual(self.planner.dependency_kind(orig), "dependency")
        self.assertEqual(orig.fixes_referenced_by, {self.fix})

    def test_later_fix_is_information_not_obligatory_selection(self):
        orig = self.planner.ensure_node(self.original, direct=True, reason="focus-path")
        self.planner.add_explicit_fixes_closure([orig])
        later = self.planner.nodes[self.fix]
        self.assertEqual(later.reasons, {"fixed-by"})
        self.assertEqual(later.required_by, set())
        self.assertEqual(later.triggered_by, {self.original})
        self.assertFalse(self.planner.is_actionable_node(later))
        self.assertIn(self.fix, orig.fixes_referenced_by)
        worksheet = self.planner.report_text()
        self.assertIn("fixed-by (informational; NOT a hard prerequisite): " + self.fix[:12], worksheet)

    def test_selected_original_promotes_later_fix_to_independent_advisor_review(self):
        orig = self.planner.ensure_node(self.original, direct=True, reason="focus-path")
        orig.advisor_decision = "TAKE"
        self.planner.add_explicit_fixes_closure([orig])
        later = self.planner.nodes[self.fix]
        self.assertFalse(self.planner.is_actionable_node(later))
        seen = []
        def evaluate(_client, _prompt, sha, **kwargs):
            seen.append(sha)
            self.planner.nodes[sha].advisor_decision = "REVIEW"
            self.planner.nodes[sha].advisor_rationale = "needs inspection"
        self.planner._advisor_evaluate_one = evaluate
        self.planner.args.context_deps = False
        self.planner.args.symbol_deps = False
        self.planner.args.max_dependency_rounds = 3
        self.assertEqual(self.planner._advisor_evaluate_take_fixes(None, "prompt"), 1)
        self.assertEqual(seen, [self.fix])
        self.assertEqual(later.advisor_decision, "REVIEW")
        self.assertTrue(self.planner.is_actionable_node(later))
        self.assertIn("later-fix-evaluation", later.reasons)
        self.assertEqual(later.required_by, set())
        self.assertEqual(later.triggered_by, {self.original})
        self.assertEqual(self.planner._advisor_evaluate_take_fixes(None, "prompt"), 0)

    def test_later_fix_of_a_selected_fix_is_also_evaluated(self):
        # A second correction to the first fix must be discovered recursively,
        # not merely the single first-order fixed-by relationship.
        (self.repo / "arch/arm64/fix2.c").write_text("followup\n")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-qm", "fix followup\n\nFixes: " + self.fix[:12] + " (fix)")
        fix2 = git(self.repo, "rev-parse", "HEAD")
        self.planner.target = fix2
        self.planner._fixes_children = None
        original = self.planner.ensure_node(self.original, direct=True, reason="focus-path")
        original.advisor_decision = "TAKE"
        self.planner.add_explicit_fixes_closure([original])
        self.planner.args.context_deps = False
        self.planner.args.symbol_deps = False
        self.planner.args.max_dependency_rounds = 3
        observed = []
        def evaluate(_client, _prompt, sha, **kwargs):
            observed.append(sha)
            self.planner.nodes[sha].advisor_decision = "TAKE"
            self.planner.nodes[sha].advisor_rationale = "needed correction"
        self.planner._advisor_evaluate_one = evaluate
        self.assertEqual(self.planner._advisor_evaluate_take_fixes(None, "prompt"), 2)
        self.assertEqual(observed, [self.fix, fix2])
        self.assertIn(fix2, self.planner.nodes[self.fix].fixes_referenced_by)
        self.assertEqual(self.planner.nodes[fix2].required_by, set())

    def test_selected_fix_evaluates_missing_arm64_original(self):
        fix = self.planner.ensure_node(self.fix, direct=True, reason="focus-path")
        fix.advisor_decision = "TAKE"
        self.planner.add_explicit_fixes_closure([fix])
        original = self.planner.nodes[self.original]
        self.assertFalse(self.planner.is_actionable_node(original))
        observed = []
        def evaluate(_client, _prompt, sha, **kwargs):
            observed.append((sha, kwargs))
            self.planner.nodes[sha].advisor_decision = "REVIEW"
        self.planner._advisor_evaluate_one = evaluate
        self.assertEqual(self.planner._advisor_evaluate_take_originals(None, "prompt"), 1)
        self.assertEqual(len(observed), 1)
        self.assertEqual(observed[0][0], self.original)
        self.assertEqual(observed[0][1]["reverse_fixes_context"][0]["sha"], self.fix)
        self.assertEqual(original.required_by, set())
        self.assertTrue(self.planner.is_actionable_node(original))
        self.assertIn("original-fixes-evaluation", original.reasons)
        self.assertEqual(self.planner._advisor_evaluate_take_originals(None, "prompt"), 0)

    def test_review_fix_triggers_missing_original_evaluation(self):
        # Run 02: aes-neonbs is REVIEW because the FPSIMD original was never
        # evaluated. An earlier original must be evaluated without requiring
        # the later fix to have been TAKE already.
        fix = self.planner.ensure_node(self.fix, direct=True, reason="focus-path")
        fix.advisor_decision = "REVIEW"
        self.planner.add_explicit_fixes_closure([fix])
        checked = []
        def evaluate(_client, _prompt, sha, **kwargs):
            checked.append((sha, kwargs))
            self.planner.nodes[sha].advisor_decision = "TAKE"
        self.planner._advisor_evaluate_one = evaluate
        self.planner.args.context_deps = False
        self.planner.args.symbol_deps = False
        self.assertEqual(self.planner._advisor_evaluate_take_originals(None, "prompt"), 1)
        self.assertEqual([x[0] for x in checked], [self.original])
        self.assertEqual(checked[0][1]["reverse_fixes_context"][0]["decision"], "REVIEW")
        original = self.planner.nodes[self.original]
        self.assertEqual(original.advisor_decision, "TAKE")
        self.assertEqual(original.required_by, set())
        self.assertEqual(fix.required_by, set())

    def test_review_fix_reconsidered_after_original_becomes_take(self):
        fix = self.planner.ensure_node(self.fix, direct=True, reason="focus-path")
        fix.advisor_decision = "REVIEW"
        self.planner.add_explicit_fixes_closure([fix])
        self.planner.args.context_deps = False
        self.planner.args.symbol_deps = False
        self.planner.args.max_dependency_rounds = 2
        reviewed = []
        def evaluate(_client, _prompt, sha, **kwargs):
            reviewed.append((sha, kwargs))
            if sha == self.original:
                self.assertTrue(kwargs.get("reverse_fixes_context"))
                self.planner.nodes[sha].advisor_decision = "TAKE"
            elif sha == self.fix:
                self.assertEqual(kwargs["selected_originals_context"][0]["sha"], self.original)
                self.assertTrue(kwargs["reconsideration"])
                self.planner.nodes[sha].advisor_decision = "TAKE"
        self.planner._advisor_evaluate_one = evaluate
        self.planner._advisor_complete_fixes_relationships(None, "prompt")
        self.assertEqual([x[0] for x in reviewed], [self.original, self.fix])
        self.assertEqual(fix.advisor_decision, "TAKE")
        self.assertEqual(self.planner.nodes[self.original].advisor_decision, "TAKE")
        self.assertEqual(fix.required_by, set())
        self.assertEqual(self.planner.nodes[self.original].required_by, set())
        self.assertEqual(self.planner._advisor_evaluate_take_originals(None, "prompt"), 0)
        self.assertEqual(self.planner._advisor_evaluate_take_fixes(None, "prompt"), 0)

    def test_review_fix_out_of_scope_not_expanded(self):
        fix = self.planner.ensure_node(self.fix, direct=True, reason="focus-path")
        fix.advisor_decision = "REVIEW"
        self.planner.platform_relevance = lambda sha: (
            {"status": "UNMATCHED_SOC"} if sha == self.fix else {})
        calls = []
        self.planner._advisor_evaluate_one = lambda *_a, **_k: calls.append(_a[2])
        self.assertEqual(self.planner._advisor_evaluate_take_originals(None, "prompt"), 0)
        self.assertEqual(calls, [])

    def test_reverse_fixes_does_not_reassess_already_take_fix(self):
        fix = self.planner.ensure_node(self.fix, direct=True, reason="focus-path")
        fix.advisor_decision = "TAKE"
        self.planner.args.context_deps = False
        self.planner.args.symbol_deps = False
        assessed = []
        def evaluate(_client, _prompt, sha, **kwargs):
            assessed.append(sha)
            self.planner.nodes[sha].advisor_decision = "TAKE"
        self.planner._advisor_evaluate_one = evaluate
        self.planner._advisor_complete_fixes_relationships(None, "prompt")
        self.assertEqual(assessed.count(self.original), 1)
        self.assertNotIn(self.fix, assessed)
        self.assertEqual(self.planner.nodes[self.fix].advisor_decision, "TAKE")
        self.assertEqual(self.planner.nodes[self.original].required_by, set())

    def test_reverse_skips_foreign_architecture_only_original(self):
        (self.repo / "arch/x86").mkdir(parents=True)
        (self.repo / "arch/x86/original.c").write_text("foreign architecture\n")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-qm", "foreign original")
        foreign = git(self.repo, "rev-parse", "HEAD")
        (self.repo / "arch/arm64/fix.c").write_text("fixed common code\n")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-qm", "shared fix\n\nFixes: " + foreign[:12])
        fix_sha = git(self.repo, "rev-parse", "HEAD")
        self.planner.target = fix_sha
        self.planner.active_architectures = {"arm64"}
        fix = self.planner.ensure_node(fix_sha, direct=True, reason="focus-path")
        fix.advisor_decision = "TAKE"
        assessed = []
        self.planner._advisor_evaluate_one = lambda *a, **k: assessed.append(a[2])
        self.assertEqual(self.planner._advisor_evaluate_take_originals(None, "prompt"), 0)
        self.assertEqual(assessed, [])
        self.assertEqual(self.planner.nodes[foreign].required_by, set())
        self.assertFalse(self.planner.is_actionable_node(self.planner.nodes[foreign]))

    def test_reverse_fixes_with_new_take_triggers_later_fix_closure(self):
        # TAKE fix2 -> TAKE original fix1 -> discover *another* later fix of
        # original. The Fixes relationship must remain informational.
        (self.repo / "arch/arm64/fix2.c").write_text("other fix\n")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-qm", "second independent fix\n\nFixes: " + self.original[:12])
        sibling = git(self.repo, "rev-parse", "HEAD")
        self.planner.target = sibling
        self.planner._fixes_children = None
        fix = self.planner.ensure_node(self.fix, direct=True, reason="focus-path")
        fix.advisor_decision = "TAKE"
        self.planner.args.context_deps = False
        self.planner.args.symbol_deps = False
        assessed = []
        def evaluate(_client, _prompt, sha, **kwargs):
            assessed.append(sha)
            self.planner.nodes[sha].advisor_decision = "TAKE"
        self.planner._advisor_evaluate_one = evaluate
        self.planner._advisor_complete_fixes_relationships(None, "prompt")
        self.assertIn(self.original, assessed)
        self.assertIn(sibling, assessed)
        self.assertEqual(self.planner.nodes[sibling].required_by, set())

    def make_prebase_fix(self):
        """One original before --base, one fix within base..target."""
        git(self.repo, "switch", "-q", "-c", "pre-base-fixes", self.original)
        (self.repo / "range-marker.txt").write_text("start of target range\n")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-qm", "range boundary")
        older_range_base = git(self.repo, "rev-parse", "HEAD")
        (self.repo / "arch/arm64/historical_fix.c").write_text("corrected\n")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-qm", "fix: previous architecture optimization\n\nFixes: " + self.original[:12])
        new_fix = git(self.repo, "rev-parse", "HEAD")
        args = kbp.parse_args([
            "--repo", str(self.repo), "--base", older_range_base,
            "--target", new_fix, "--downstream-ref", "downstream",
            "--architecture", "arm64", "--path", "arch/arm64/",
            "--no-patch-id",
        ])
        self.planner = kbp.Planner(args)
        self.planner.build_downstream_index()
        self.planner.args.context_deps = False
        self.planner.args.symbol_deps = False
        return new_fix

    def test_prebase_original_in_later_fixes_index(self):
        fix_sha = self.make_prebase_fix()
        self.assertTrue(self.planner.git.is_ancestor(self.original, self.planner.base))
        children = self.planner.build_fixes_children()
        self.assertEqual(children.get(self.original), {fix_sha})
        original = self.planner.ensure_node(self.original, reason="Fixes-target",
                                            required_by=fix_sha)
        original.advisor_decision = "TAKE"
        self.planner.add_explicit_fixes_closure([original])
        self.assertEqual(original.fixes_referenced_by, {fix_sha})
        self.assertEqual(self.planner.nodes[fix_sha].required_by, set())
        # Also ensure the pre-base TAKE can appear in the application order.
        original.reasons.add("original-fixes-evaluation")
        self.assertIn(self.original, self.planner.ordered_missing())

    def test_selected_historical_original_older_than_replay_merge_base_is_ordered(self):
        # A downstream revert can make an original missing even if it predates
        # both the planning base and the Git merge-base of downstream/upstream.
        # It must not disappear from the generated application order.
        fix_sha = self.make_prebase_fix()
        original = self.planner.ensure_node(
            self.original, reason="original-fixes-evaluation")
        original.advisor_decision = "TAKE"
        # Simulate a downstream revert/absence of an otherwise ancestral patch.
        original.match.status = "missing"
        self.planner.replay_start = self.original
        ordered = self.planner.ordered_missing()
        self.assertIn(self.original, ordered)
        # A normal in-range selected fix remains after its older original.
        fix = self.planner.ensure_node(fix_sha, direct=True, reason="focus-path")
        fix.advisor_decision = "TAKE"
        ordered = self.planner.ordered_missing()
        self.assertLess(ordered.index(self.original), ordered.index(fix_sha))

    def test_prebase_review_fix_reconsidered_when_original_selected(self):
        # This reproduces the 0.1.14 run-04 symptom: reverse evaluation
        # selects an original older than the requested --base; its in-range
        # REVIEW correction must be re-evaluated with that new information.
        fix_sha = self.make_prebase_fix()
        fix = self.planner.ensure_node(fix_sha, direct=True, reason="focus-path")
        fix.advisor_decision = "REVIEW"
        fix.advisor_rationale = "Original is missing and not selected"
        assessed = []
        def evaluate(_client, _prompt, sha, **kwargs):
            assessed.append((sha, kwargs))
            node = self.planner.nodes[sha]
            if sha == self.original:
                self.assertTrue(kwargs.get("reverse_fixes_context"))
                node.advisor_decision = "TAKE"
                node.advisor_rationale = "Relevant original ARM64 optimization"
            elif sha == fix_sha:
                self.assertTrue(kwargs.get("reconsideration"))
                self.assertEqual(kwargs["selected_originals_context"][0]["sha"],
                                 self.original)
                node.advisor_decision = "TAKE"
                node.advisor_rationale = "Selected original makes correction applicable"
        self.planner._advisor_evaluate_one = evaluate
        self.planner._advisor_complete_fixes_relationships(None, "prompt")
        self.assertEqual([sha for sha, _ in assessed], [self.original, fix_sha])
        self.assertEqual(self.planner.nodes[self.original].advisor_decision, "TAKE")
        self.assertEqual(fix.advisor_decision, "TAKE")
        self.assertNotIn("missing", fix.advisor_rationale)
        self.assertEqual(self.planner.nodes[self.original].required_by, set())
        self.assertEqual(fix.required_by, set())
        self.assertEqual(self.planner._advisor_complete_fixes_relationships(None, "prompt"), None)
        self.assertEqual(len(assessed), 2)

    def test_prebase_original_take_reconsiders_review_fix_without_reverse_pass(self):
        fix_sha = self.make_prebase_fix()
        orig = self.planner.ensure_node(self.original, reason="original-fixes-evaluation")
        orig.advisor_decision = "TAKE"
        fix = self.planner.ensure_node(fix_sha, direct=True, reason="focus-path")
        fix.advisor_decision = "REVIEW"
        seen = []
        def evaluate(_client, _prompt, sha, **kwargs):
            seen.append((sha, kwargs))
            self.planner.nodes[sha].advisor_decision = "TAKE"
        self.planner._advisor_evaluate_one = evaluate
        self.planner._advisor_complete_fixes_relationships(None, "prompt")
        self.assertEqual([sha for sha, _ in seen], [fix_sha])
        self.assertEqual(seen[0][1]["selected_originals_context"][0]["sha"], self.original)
        self.assertEqual(self.planner._advisor_complete_fixes_relationships(None, "prompt"), None)
        self.assertEqual(len(seen), 1)

    def test_prebase_selected_original_reconsiders_skipped_fix_once(self):
        # A fix skipped for "original missing" cannot retain that stale
        # rationale once the referenced old original has become TAKE.
        fix_sha = self.make_prebase_fix()
        original = self.planner.ensure_node(
            self.original, reason="original-fixes-evaluation")
        original.advisor_decision = "TAKE"
        fix = self.planner.ensure_node(fix_sha, direct=True, reason="focus-path")
        fix.advisor_decision = "SKIP"
        fix.advisor_rationale = "Original missing"
        observed = []
        def evaluate(_client, _prompt, sha, **kw):
            observed.append((sha, kw))
            self.planner.nodes[sha].advisor_decision = "REVIEW"
            self.planner.nodes[sha].advisor_rationale = "Reassessed with original"
        self.planner._advisor_evaluate_one = evaluate
        self.planner._advisor_complete_fixes_relationships(None, "prompt")
        self.assertEqual([sha for sha, kw in observed], [fix_sha])
        self.assertEqual(observed[0][1]["selected_originals_context"][0]["sha"],
                         self.original)
        self.assertEqual(fix.advisor_decision, "REVIEW")
        self.planner._advisor_complete_fixes_relationships(None, "prompt")
        self.assertEqual(len(observed), 1)
        self.assertEqual(fix.required_by, set())

    def test_prebase_unrelated_arch_original_not_evaluated(self):
        # The wider time horizon must not open the door to irrelevant x86
        # originals merely because a shared ARM64 fix mentions them.
        git(self.repo, "switch", "-q", "-c", "historical-x86", self.original)
        (self.repo / "arch/x86").mkdir(parents=True)
        (self.repo / "arch/x86/historical.c").write_text("old x86 patch\n")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-qm", "x86-only optimization")
        foreign = git(self.repo, "rev-parse", "HEAD")
        (self.repo / "boundary").write_text("marker\n")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-qm", "begin range")
        range_base = git(self.repo, "rev-parse", "HEAD")
        (self.repo / "arch/arm64/fix.c").write_text("shared correction\n")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-qm", "fix old x86 code\n\nFixes: " + foreign[:12])
        fix_sha = git(self.repo, "rev-parse", "HEAD")
        args = kbp.parse_args(["--repo", str(self.repo), "--base", range_base,
                               "--target", fix_sha, "--downstream-ref", "downstream",
                               "--architecture", "arm64", "--path", "arch/arm64/",
                               "--no-patch-id"])
        self.planner = kbp.Planner(args)
        self.planner.build_downstream_index()
        self.assertIn(fix_sha, self.planner.build_fixes_children().get(foreign, set()))
        fix = self.planner.ensure_node(fix_sha, direct=True, reason="focus-path")
        fix.advisor_decision = "REVIEW"
        observed = []
        self.planner._advisor_evaluate_one = lambda _c, _p, sha, **kw: observed.append(sha)
        self.planner._advisor_complete_fixes_relationships(None, "prompt")
        self.assertEqual(observed, [])
        self.assertFalse(self.planner.is_actionable_node(self.planner.nodes[foreign]))

    def test_prompt_disclaims_fixes_as_dependency(self):
        prompt = self.planner.advisor_system_prompt()
        self.assertIn("Fixes: tag is a historical regression/fix relationship", prompt)
        self.assertIn("NOT a verified", prompt)
        self.assertEqual(kbp.ADVISOR_PROMPT_VERSION, "36")


if __name__ == "__main__":
    unittest.main()
