"""v0.1.7: target architecture and per-SoC DT source scope regressions."""
import unittest

from test_kernel_backport_planner import kbp
import test_kernel_backport_planner as _legacy
import test_platform_schema_v2 as _v2


SHA = "a" * 40
CONSUMER = "b" * 40


class TargetScope017Tests(unittest.TestCase):
    def basic(self, files, arch="arm64"):
        p = _legacy.AdvisorTests().minimal_planner()
        p.active_architectures = {arch}
        p.nodes = {SHA: kbp.CommitNode(SHA, "scope test", files=list(files))}
        p.changed_files = lambda sha: p.nodes[sha].files
        return p

    def test_x86_cfi_plus_common_header_is_not_arm64_relevance(self):
        p = self.basic([
            "arch/x86/entry/entry.S", "arch/x86/mm/mem_encrypt_boot.S",
            "include/linux/compiler.h",
        ])
        scope = p.advisor_scope_context(p.nodes[SHA].files)
        self.assertEqual(scope["architecture_scope"], "common-plus-unsupported")
        self.assertTrue(scope["cross_architecture_review_required"])
        self.assertEqual(scope["common_paths_in_mixed_commit"], ["include/linux/compiler.h"])
        result = p.advisor_policy_override(SHA, {"commit": {"scope": scope}})
        self.assertEqual(result["decision"], "REVIEW")
        self.assertIn("generic path", result["rationale"])

    def test_architecture_specific_driver_trees_are_not_global(self):
        cases = [
            ("drivers/platform/x86/intel/pmt.c", "x86"),
            ("drivers/s390/cio/device.c", "s390"),
            ("drivers/platform/arm64/foo.c", "arm64"),
        ]
        for path, owner in cases:
            with self.subTest(path=path):
                self.assertEqual(kbp.Planner._path_architecture_dirs(path), {owner})
        # A generic Amazon or NVIDIA driver cannot be excluded as x86-only.
        self.assertEqual(kbp.Planner._path_architecture_dirs(
            "drivers/infiniband/hw/efa/efa_main.c"), set())

    def test_x86_only_is_skipped_even_though_rhel_supports_x86(self):
        p = self.basic(["arch/x86/entry/entry.S"])
        scope = p.advisor_scope_context(p.nodes[SHA].files)
        self.assertTrue(scope["unsupported_architecture_only"])
        self.assertFalse(scope["cross_architecture_review_required"])
        result = p.advisor_policy_override(SHA, {
            "commit": {"scope": scope},
            "planning_context": {"product": p.advisor_product_context()},
        })
        self.assertEqual(result["decision"], "SKIP")

    def test_shared_generic_and_target_owned_code_stays_eligible(self):
        for arch in ("arm64", "x86", "powerpc", "s390"):
            with self.subTest(arch=arch):
                p = self.basic([f"arch/{arch}/kernel/process.c", "include/linux/compiler.h"], arch)
                scope = p.advisor_scope_context(p.nodes[SHA].files)
                self.assertFalse(scope["cross_architecture_review_required"])
                self.assertFalse(scope["unsupported_architecture_only"])
                self.assertIsNone(p.advisor_policy_override(SHA, {"commit": {"scope": scope}}))

    def test_common_only_code_is_not_architecture_restricted(self):
        p = self.basic(["kernel/sched/core.c", "include/linux/compiler.h"])
        scope = p.advisor_scope_context(p.nodes[SHA].files)
        self.assertEqual(scope["architecture_scope"], "common")
        self.assertFalse(scope["cross_architecture_review_required"])

    def test_proven_shared_provider_dependency_is_not_auto_reviewed(self):
        p = self.basic(["arch/x86/kernel/cfi.c", "include/linux/compiler.h"])
        p.nodes[CONSUMER] = kbp.CommitNode(CONSUMER, "consumer", direct=True,
            files=["arch/arm64/kernel/process.c"])
        scope = p.advisor_scope_context(p.nodes[SHA].files)
        self.assertTrue(scope["cross_architecture_review_required"])
        # A consumer's independently verified required peer is *not* discarded
        # by the architecture gate; advisor reconciliation remains responsible.
        self.assertIsNone(p.advisor_policy_override(SHA, {"commit": {
            "scope": scope, "actual_inbound_take_prerequisite_count": 1,
        }}))

    def test_dependency_discovery_preserves_foreign_arch_lead_as_candidate(self):
        p = self.basic(["arch/x86/kernel/cfi.c", "include/linux/compiler.h"])
        reason = p._discovered_dependency_reason(SHA, "conflict-hunk-preimage:5")
        self.assertEqual(reason, "architecture-scope-candidate:conflict-hunk-preimage:5")
        self.assertTrue(p.is_tentative_dependency_reason(reason))
        p.nodes[SHA].add_reason(reason, CONSUMER, tentative=p.is_tentative_dependency_reason(reason))
        self.assertEqual(p.nodes[SHA].required_by, set())
        self.assertEqual(p.nodes[SHA].triggered_by, {CONSUMER})
        self.assertFalse(p.is_actionable_node(p.nodes[SHA]))

    def test_conflict_dependency_discovery_keeps_foreign_candidate_tentative(self):
        import types
        p = self.basic(["arch/x86/kernel/cfi.c", "include/linux/compiler.h"])
        p.args.max_conflict_deps = 5
        p.context_dependencies = lambda sha, only_paths=None: [(SHA, "hunk-preimage", 8)]
        p.nodes.pop(SHA)
        p.changed_files = lambda sha: ["arch/x86/kernel/cfi.c", "include/linux/compiler.h"]
        def ensure(sha, *, reason, required_by):
            n = p.nodes.setdefault(sha, kbp.CommitNode(sha, "foreign dependency"))
            n.add_reason(reason, required_by,
                         tentative=p.is_tentative_dependency_reason(reason))
            return n
        p.ensure_node = ensure
        strong, candidates = p.add_conflict_dependencies(
            [(CONSUMER, ["arch/arm64/kernel/process.c"])])
        self.assertEqual((strong, candidates), (0, 1))
        self.assertFalse(p.is_actionable_node(p.nodes[SHA]))

    def test_static_dependency_discovery_checks_architecture_before_promotion(self):
        p = self.basic(["arch/x86/kernel/cfi.c", "include/linux/compiler.h"])
        selected = kbp.CommitNode(CONSUMER, "selected ARM64 consumer", direct=True,
                                  files=["arch/arm64/kernel/fpsimd.c"])
        p.nodes[CONSUMER] = selected
        p.args.context_deps = True
        p.args.symbol_deps = False
        p.args.context_score = 5
        p.is_pre_base_dependency = lambda n: False
        p.context_dependencies = lambda sha: [(SHA, "hunk-preimage", 10)]
        def ensure(sha, *, reason, required_by):
            n = p.nodes[sha]
            n.add_reason(reason, required_by,
                         tentative=p.is_tentative_dependency_reason(reason))
            return n
        p.ensure_node = ensure
        added = p.add_static_dependencies([selected])
        self.assertEqual(added, 0)
        self.assertFalse(p.is_actionable_node(p.nodes[SHA]))
        self.assertEqual(p.nodes[SHA].triggered_by, {CONSUMER})

    def test_shared_symbol_used_by_target_arch_remains_selected(self):
        p = self.basic(["arch/x86/kernel/cfi.c", "include/linux/compiler.h"])
        reason = p._discovered_dependency_reason(
            SHA, "symbol:shared_entry", provider_path="include/linux/compiler.h",
            consumer_paths={"arch/arm64/kernel/fpsimd.c"},
        )
        self.assertEqual(reason, "symbol:shared_entry")
        # An equally named symbol in x86-local code is NOT enough.
        candidate = p._discovered_dependency_reason(
            SHA, "symbol:shared_entry", provider_path="arch/x86/kernel/cfi.c",
            consumer_paths={"arch/arm64/kernel/fpsimd.c"},
        )
        self.assertTrue(candidate.startswith("architecture-scope-candidate:"))

    def test_without_architecture_selection_no_foreign_arch_gate(self):
        p = self.basic(["arch/x86/kernel/cfi.c", "include/linux/compiler.h"])
        p.active_architectures = set()
        self.assertEqual(p._discovered_dependency_reason(SHA, "hunk-preimage:4"),
                         "hunk-preimage:4")
        self.assertFalse(p.advisor_scope_context(p.nodes[SHA].files)[
            "cross_architecture_review_required"])


class SoCScope017Tests(unittest.TestCase):
    def planner(self, files, obj=None):
        return _v2.PlatformSchemaV2Tests().planner(files, obj)

    def test_unmatched_soc_dtsi_requires_scope_review_not_take(self):
        for path in (
            "arch/arm64/boot/dts/qcom/kaanapali.dtsi",
            "arch/arm64/boot/dts/qcom/qcm2290.dtsi",
            "arch/arm64/boot/dts/allwinner/sun55i-a523.dtsi",
            "arch/arm64/boot/dts/qcom/talos.dtsi",
            "arch/arm64/boot/dts/rockchip/rk3576-pinctrl.dtsi",
        ):
            with self.subTest(path=path):
                p = self.planner([path])
                rel = p.platform_relevance(SHA)
                self.assertEqual(rel["status"], "UNMATCHED_SOC")
                self.assertFalse(rel["board_leaf_only"])
                self.assertTrue(rel["soc_dtsi_only"])
                result = p.advisor_policy_override(SHA, {"commit": {
                    "platform_relevance": rel, "scope": {},
                }})
                self.assertEqual(result["decision"], "REVIEW")

    def test_provenance_only_consumer_does_not_approve_soc(self):
        p = self.planner(["arch/arm64/boot/dts/qcom/kaanapali.dtsi"])
        consumer = kbp.CommitNode(CONSUMER, "unmatched consumer",
            files=["arch/arm64/boot/dts/qcom/selected.dts"])
        p.nodes[CONSUMER] = consumer
        p.nodes[SHA].add_reason("hunk-preimage:5", CONSUMER)
        rel = p.platform_relevance(SHA)
        self.assertEqual(rel["status"], "UNMATCHED_SOC")
        self.assertTrue(rel["candidate_roots"])
        result = p.advisor_policy_override(SHA, {"commit": {
            "platform_relevance": rel, "scope": {},
        }})
        self.assertEqual(result["decision"], "REVIEW")

    def test_selected_soc_match_remains_eligible(self):
        obj = _v2.profile_data()
        obj["platforms"].append(_v2.entry("Selected SoC", "arch/arm64/boot/dts/qcom/kaanapali.dtsi",
                                      scope="soc_shared"))
        p = self.planner(["arch/arm64/boot/dts/qcom/kaanapali.dtsi"], obj)
        rel = p.platform_relevance(SHA)
        self.assertEqual(rel["status"], "DIRECT")
        self.assertIsNone(p.advisor_policy_override(SHA, {"commit": {
            "platform_relevance": rel, "scope": {},
        }}))

    def test_verified_dt_include_for_take_consumer_exempts_review(self):
        p = self.planner(["arch/arm64/boot/dts/qcom/kaanapali.dtsi"])
        consumer = kbp.CommitNode(CONSUMER, "accepted board", direct=True,
            files=["arch/arm64/boot/dts/qcom/selected.dts"])
        consumer.advisor_decision = "TAKE"
        p.nodes[CONSUMER] = consumer
        p.nodes[SHA].add_reason("dt-include:arch/arm64/boot/dts/qcom/kaanapali.dtsi", CONSUMER)
        rel = p.platform_relevance(SHA)
        self.assertEqual(rel["status"], "UNMATCHED_SOC")
        self.assertIsNone(p.advisor_policy_override(SHA, {"commit": {
            "platform_relevance": rel, "scope": {},
        }}))

    def test_dt_makefile_only_does_not_become_soc(self):
        p = self.planner(["arch/arm64/boot/dts/rockchip/Makefile"])
        self.assertEqual(p.platform_relevance(SHA)["status"], "SHARED_OR_UNKNOWN")

    def test_mixed_soc_and_driver_change_is_not_auto_rejected(self):
        p = self.planner(["arch/arm64/boot/dts/qcom/kaanapali.dtsi", "drivers/gpu/drm/generic.c"])
        self.assertEqual(p.platform_relevance(SHA)["status"], "SHARED_OR_UNKNOWN")

    def test_prompt_cache_bumped_and_scope_words_present(self):
        p = _legacy.AdvisorTests().minimal_planner()
        self.assertEqual(kbp.ADVISOR_PROMPT_VERSION, "36")
        system = p.advisor_system_prompt()
        self.assertIn("--architecture defines the *current* target", system)
        self.assertIn("SoC-specific .dtsi", system)


class ScopeTradeoff018Tests(unittest.TestCase):
    """Regress run-07 Kaanapali/Lemans whole-commit promotion."""
    def test_kaanapali_whitespace_consumer_is_reconsidered_before_take(self):
        import types
        obj = _v2.profile_data()
        obj["platforms"].append(_v2.entry(
            "RHIVOS Lemans", "arch/arm64/boot/dts/qcom/lemans.dtsi",
            product="rhivos", basis="product_documented", scope="soc_shared"))
        obj["active_product_scopes"].append("rhivos")
        source = "arch/arm64/boot/dts/qcom/kaanapali.dtsi"
        consumer_sha = CONSUMER
        p = _v2.PlatformSchemaV2Tests().planner([source], obj)
        provider = p.nodes[SHA]
        provider.subject = "Introduce Kaanapali SoC"
        provider.advisor_decision = "TAKE"
        provider.required_by.add(consumer_sha)
        consumer = kbp.CommitNode(consumer_sha, "Minor whitespace cleanup", direct=True,
            files=[source, "arch/arm64/boot/dts/qcom/lemans.dtsi"])
        consumer.advisor_decision = "TAKE"
        p.nodes[consumer_sha] = consumer
        p.args = types.SimpleNamespace(advisor_max_related_diff_chars=300)
        p._patch_whitespace_only = lambda sha: True
        p.patch_excerpt = lambda sha, length: "whitespace patch"
        p.info = lambda msg: None
        prompts = []
        def evaluate(client, prompt, sha, **kwargs):
            prompts.append((sha, kwargs.get("scope_tradeoff_context")))
            p.nodes[sha].advisor_decision = "SKIP" if sha == CONSUMER else "REVIEW"
        p._advisor_evaluate_one = evaluate
        self.assertEqual(p.platform_relevance(SHA)["status"], "UNMATCHED_SOC")
        self.assertEqual(p.platform_relevance(CONSUMER)["status"], "DIRECT")
        self.assertEqual(p._advisor_scope_tradeoff_pass(None, "prompt"), 1)
        self.assertEqual([x[0] for x in prompts], [CONSUMER, SHA])
        self.assertEqual(prompts[0][1]["consumer_whitespace_only"], True)
        self.assertEqual(prompts[0][1]["out_of_scope_providers"][0]["whole_commit_shared_files"], [source])
        self.assertEqual(provider.advisor_decision, "REVIEW")
        self.assertEqual(consumer.advisor_decision, "SKIP")
        self.assertEqual(p._advisor_scope_tradeoff_pass(None, "prompt"), 0)



if __name__ == "__main__":
    unittest.main()
