"""Regression coverage for the 0.4.37 pre-integration safety gates."""
import json
import subprocess
import tempfile
import types
import unittest
from pathlib import Path
from test_kernel_backport_planner import kbp


def git(repo, *args):
    return subprocess.check_output(["git", *args], cwd=repo,
                                   stderr=subprocess.PIPE, text=True).strip()


def write_commit(repo, filename, content, title):
    path = repo / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    git(repo, "add", "--", filename)
    git(repo, "commit", "-qm", title)
    return git(repo, "rev-parse", "HEAD")


class Safety0437Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name)
        git(self.repo, "init", "-q", "-b", "main")
        git(self.repo, "config", "user.name", "Planner Test")
        git(self.repo, "config", "user.email", "planner@example.invalid")
        self.base = write_commit(self.repo, "README", "original\n", "base")

    def args(self, target, **kwargs):
        options = ["--repo", str(self.repo), "--base", self.base,
                   "--target", target, "--downstream-ref", "downstream",
                   "--path", "arch/arm64/", "--no-patch-id"]
        if kwargs.get("advisor", False):
            options += ["--advisor-model", "test-model"]
        if kwargs.get("platforms"):
            options += ["--platforms", str(kwargs["platforms"])]
        if kwargs.get("architecture"):
            options += ["--architecture", kwargs["architecture"]]
        return kbp.parse_args(options)

    def planner(self, target, **kwargs):
        return kbp.Planner(self.args(target, **kwargs))

    def test_explicit_downstream_revert_becomes_actionable(self):
        git(self.repo, "switch", "-qc", "upstream")
        upstream = write_commit(self.repo, "arch/arm64/foo.c", "enabled\n", "feature")
        git(self.repo, "switch", "-qc", "downstream", self.base)
        git(self.repo, "cherry-pick", "-x", upstream)
        cherry = git(self.repo, "rev-parse", "HEAD")
        git(self.repo, "revert", "--no-edit", cherry)
        revert_sha = git(self.repo, "rev-parse", "HEAD")
        planner = self.planner(upstream)
        planner.build_downstream_index()
        result = planner.match(upstream)
        self.assertEqual(result.status, "missing")
        self.assertEqual(result.method, "reverted-downstream")
        self.assertEqual(result.revert_sha, revert_sha)
        self.assertEqual(result.revert_status, "reverted")
        node = planner.ensure_node(upstream, direct=True, reason="focus-path")
        planner.advisor_consistency_issues = []
        planner.advisor_model = "test-model"
        self.assertIn("REVERTED_DOWNSTREAM_IDENTITY",
                      [x["kind"] for x in planner._integration_blockers()])
        self.assertEqual(node.match.status, "missing")

    def test_revert_of_revert_restores_present_state(self):
        git(self.repo, "switch", "-qc", "upstream")
        upstream = write_commit(self.repo, "arch/arm64/foo.c", "enabled\n", "feature")
        git(self.repo, "switch", "-qc", "downstream", self.base)
        git(self.repo, "cherry-pick", "-x", upstream)
        cherry = git(self.repo, "rev-parse", "HEAD")
        git(self.repo, "revert", "--no-edit", cherry)
        first_revert = git(self.repo, "rev-parse", "HEAD")
        git(self.repo, "revert", "--no-edit", first_revert)
        planner = self.planner(upstream)
        planner.build_downstream_index()
        self.assertEqual(planner.match(upstream).status, "present")

    def test_subject_only_revert_is_not_treated_as_present(self):
        git(self.repo, "switch", "-qc", "upstream")
        upstream = write_commit(self.repo, "arch/arm64/foo.c", "enabled\n", "feature")
        git(self.repo, "switch", "-qc", "downstream", self.base)
        git(self.repo, "cherry-pick", "-x", upstream)
        git(self.repo, "rm", "arch/arm64/foo.c")
        git(self.repo, "commit", "-qm", 'Revert "feature"')
        planner = self.planner(upstream)
        planner.build_downstream_index()
        result = planner.match(upstream)
        self.assertEqual(result.status, "missing")
        self.assertEqual(result.revert_status, "ambiguous")
        self.assertTrue(planner.downstream_index.unresolved_reverts)

    def test_platform_discovery_is_architecture_scoped(self):
        git(self.repo, "switch", "-qc", "upstream")
        arm = write_commit(self.repo, "drivers/arm64/one.c", "arm\n", "arm driver")
        x86 = write_commit(self.repo, "drivers/x86/two.c", "x86\n", "x86 driver")
        git(self.repo, "branch", "downstream", self.base)
        profile = self.repo / "profile.json"
        profile.write_text(json.dumps({
            "version": 1, "name": "mixed-architectures", "platforms": [
                {"name": "arm-device", "architecture": "arm64",
                 "paths": ["drivers/arm64/*"]},
                {"name": "x86-device", "architecture": "x86",
                 "paths": ["drivers/x86/*"]},
            ],
        }))
        planner = self.planner(x86, advisor=True, platforms=profile)
        self.assertEqual(planner.active_architectures, {"arm64"})
        self.assertEqual(planner.platform_direct_candidates(), [arm])
        other = self.planner(x86, advisor=True, platforms=profile,
                             architecture="x86_64")
        self.assertEqual(other.active_architectures, {"x86"})
        self.assertEqual(other.platform_direct_candidates(), [x86])

    def test_dependency_budget_exposes_remaining_frontier(self):
        planner = object.__new__(kbp.Planner)
        planner.args = types.SimpleNamespace(max_dependency_rounds=1)
        first, second = "a" * 40, "b" * 40
        planner.nodes = {first: kbp.CommitNode(first, "first", direct=True)}
        planner._dependency_expanded = set()
        planner.info = lambda _: None
        planner.add_explicit_fixes_closure = lambda _frontier: 0
        planner.add_dt_include_dependencies = lambda _frontier: 0
        def add(frontier):
            if frontier[0].sha == first:
                planner.nodes[second] = kbp.CommitNode(second, "second", direct=True)
                return 1
            return 0
        planner.add_static_dependencies = add
        planner.dependency_closure()
        self.assertEqual(planner.dependency_closure_incomplete, [second])
        planner.dependency_closure()
        self.assertEqual(planner.dependency_closure_incomplete, [])

    def _integration_setup(self, upstream_file_b="B\n", downstream_file_b=None):
        git(self.repo, "switch", "-qc", "upstream")
        skip = write_commit(self.repo, "arch/arm64/skip.c", "SKIP\n", "skip")
        take = write_commit(self.repo, "arch/arm64/take.c", upstream_file_b, "take")
        git(self.repo, "switch", "-qc", "downstream", self.base)
        if downstream_file_b is not None:
            write_commit(self.repo, "arch/arm64/take.c", downstream_file_b,
                         "downstream independent implementation")
        p = self.planner(take, advisor=True)
        p.ensure_node(skip, direct=True, reason="focus-path").advisor_decision = "SKIP"
        p.ensure_node(take, direct=True, reason="focus-path").advisor_decision = "TAKE"
        p.advisor_consistency_issues = []
        return p, skip, take

    def test_final_replay_selects_take_only_and_preserves_original_branch(self):
        p, skip, take = self._integration_setup()
        p._advisor_execution_blockers = lambda: []
        before = git(self.repo, "rev-parse", "downstream")
        result = p.run_take_integration()
        self.assertEqual(result["order"], [take])
        self.assertEqual(result["status"], "ready")
        self.assertEqual([x["status"] for x in result["commits"]], ["clean"])
        self.assertEqual(git(self.repo, "rev-parse", "downstream"), before)
        self.assertEqual(p.nodes[skip].replay, "not-run")

    def test_final_replay_identifies_redundant_take(self):
        p, skip, take = self._integration_setup(downstream_file_b="B\n")
        p._advisor_execution_blockers = lambda: []
        result = p.run_take_integration()
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["commits"][0]["status"], "empty")
        self.assertIn("TAKE_REPLAY_EMPTY", [x["kind"] for x in result["blockers"]])

    def test_integration_replay_conflict_reports_stopped_followups(self):
        git(self.repo, "switch", "-qc", "upstream")
        first = write_commit(self.repo, "README", "upstream\n", "conflicting change")
        later = write_commit(self.repo, "arch/arm64/later.c", "later\n", "later")
        git(self.repo, "switch", "-qc", "downstream", self.base)
        write_commit(self.repo, "README", "downstream\n", "other change")
        planner = self.planner(later, advisor=True)
        planner.ensure_node(first, direct=True, reason="focus-path").advisor_decision = "TAKE"
        planner.ensure_node(later, direct=True, reason="focus-path").advisor_decision = "TAKE"
        planner.advisor_consistency_issues = []
        planner._advisor_execution_blockers = lambda: []
        result = planner.run_take_integration()
        self.assertEqual(result["status"], "blocked")
        self.assertEqual([x["status"] for x in result["commits"]],
                         ["conflict", "not-tested"])
        self.assertIn("TAKE_REPLAY_CONFLICT", [x["kind"] for x in result["blockers"]])

    def test_remote_head_divergence_requires_explicit_acknowledgment(self):
        git(self.repo, "update-ref", "refs/remotes/cs10/main", self.base)
        write_commit(self.repo, "README", "local development\n", "local change")
        arguments = ["--repo", str(self.repo), "--base", self.base,
                     "--target", self.base, "--downstream-ref", "HEAD",
                     "--downstream", "cs10", "--path", "arch/arm64/"]
        planner = kbp.Planner(kbp.parse_args(arguments))
        self.assertFalse(planner.baseline_warning["acknowledged"])
        self.assertIn("BASELINE_DIVERGENCE",
                      [x["kind"] for x in planner._integration_blockers()])
        accepted = kbp.Planner(kbp.parse_args(
            arguments + ["--allow-divergent-downstream"]))
        self.assertTrue(accepted.baseline_warning["acknowledged"])
        self.assertNotIn("BASELINE_DIVERGENCE",
                         [x["kind"] for x in accepted._integration_blockers()])

    def test_strict_integration_requires_advisor(self):
        with self.assertRaises(SystemExit) as thrown:
            self.args(self.base)  # validate normal no-advisor args first
            kbp.parse_args(["--base", self.base, "--target", self.base,
                            "--strict-integration"])
        self.assertEqual(thrown.exception.code, 2)
        args = kbp.parse_args(["--base", self.base, "--target", self.base,
                               "--advisor-model", "model", "--strict-integration"])
        self.assertTrue(args.strict_integration)

    def test_shared_cache_merge_preserves_entries_from_prior_run(self):
        cache = self.repo / "shared.json"
        first = object.__new__(kbp.Planner)
        first.advisor_cache_path = cache
        first.args = types.SimpleNamespace(no_advisor_cache=False)
        first.advisor_cache = {"a": {"decision": "TAKE"}}
        first.save_advisor_cache()
        second = object.__new__(kbp.Planner)
        second.advisor_cache_path = cache
        second.args = types.SimpleNamespace(no_advisor_cache=False)
        second.advisor_cache = {"b": {"decision": "REVIEW"}}
        second.save_advisor_cache()
        self.assertEqual(set(json.loads(cache.read_text())), {"a", "b"})


if __name__ == "__main__":
    unittest.main()
