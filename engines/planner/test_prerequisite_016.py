"""0.1.16 missing-code provider regression tests, with synthetic series evidence."""
import types
import unittest

import test_kernel_backport_planner as legacy
kbp = legacy.kbp


class MissingCodePrerequisiteTests(unittest.TestCase):
    def planner(self, declaration='struct generic_pm_domain { bool previously; };'):
        p = object.__new__(kbp.Planner)
        p.args = types.SimpleNamespace(advisor_max_diff_chars=20000,
                                       advisor_max_peer_diff_chars=12000)
        p.replay_start = 'f' * 40
        p._code_member_evidence_cache = {}
        consumer = 'a' * 40
        provider = 'b' * 40
        p.nodes = {
            consumer: kbp.CommitNode(consumer, 'sync_state', files=['drivers/pmdomain/core.c']),
            provider: kbp.CommitNode(provider, 'introduce stay_on', files=['include/linux/pm_domain.h']),
        }
        p.advisor_allowed_prerequisite_peers = lambda sha: [provider]
        p.patch_excerpt = lambda sha, limit: (
            'diff --git a/drivers/pmdomain/core.c b/drivers/pmdomain/core.c\n'
            '@@ -1 +1 @@\n+ genpd->stay_on = false;\n'
            if sha == consumer else
            'diff --git a/include/linux/pm_domain.h b/include/linux/pm_domain.h\n'
            '@@ -1 +1 @@\n+ bool stay_on;\n')
        p._tree_file_text = lambda sha, path: declaration
        p.subject = lambda sha: p.nodes[sha].subject
        return p, consumer, provider

    def test_missing_stay_on_provider_supplied_to_terra(self):
        p, consumer, provider = self.planner()
        evidence = p.advisor_missing_code_member_evidence(consumer)
        self.assertEqual(len(evidence), 1)
        self.assertEqual(evidence[0]['peer_sha'], provider)
        self.assertEqual(evidence[0]['object'], 'stay_on')
        self.assertEqual(evidence[0]['kind'], 'missing-struct-member')
        self.assertEqual(evidence[0]['baseline_provider_declaration'], 'absent')
        self.assertEqual(evidence, p.advisor_missing_code_member_evidence(consumer))

    def test_existing_downstream_field_not_a_missing_prerequisite(self):
        p, consumer, _ = self.planner('struct generic_pm_domain {\n bool stay_on;\n};')
        self.assertEqual(p.advisor_missing_code_member_evidence(consumer), [])

    def test_unrelated_peer_does_not_create_a_provider(self):
        p, consumer, provider = self.planner()
        p.patch_excerpt = lambda sha, limit: (
            'diff --git a/drivers/pmdomain/core.c b/drivers/pmdomain/core.c\n+ genpd->stay_on = false;'
            if sha == consumer else
            'diff --git a/include/linux/pm_domain.h b/include/linux/pm_domain.h\n+ dev->stay_on = true;')
        self.assertEqual(p.advisor_missing_code_member_evidence(consumer), [])

    def test_evidence_is_bounded_not_a_hard_dependency(self):
        p, consumer, provider = self.planner()
        p.advisor_same_series_consumed_objects = lambda sha: [{
            'evidence_id': 'ev-other', 'kind': 'macro', 'peer_sha': provider,
        }]
        evidence = p.advisor_prerequisite_evidence(consumer)
        self.assertEqual(len(evidence), 2)
        self.assertEqual(len(p.advisor_missing_code_member_evidence(consumer)), 1)
        self.assertEqual(evidence[0]['peer_sha'], provider)
        self.assertEqual(p.nodes[provider].required_by, set())
        schema = kbp.advisor_response_schema(evidence)
        self.assertIn(evidence[0]['evidence_id'],
                      schema['properties']['prerequisite_evidence_ids']['items']['enum'])


if __name__ == '__main__':
    unittest.main()

class ReverseFixesAndFunctionProviderTests(unittest.TestCase):
    def test_unadvised_original_can_seed_series_without_becoming_take(self):
        p = object.__new__(kbp.Planner)
        consumer, provider = 'a' * 40, 'b' * 40
        p.nodes = {consumer: kbp.CommitNode(consumer, 'original',
                    files=['drivers/pmdomain/core.c'])}
        p.nodes[consumer].reasons.add('Fixes-target')
        p.nodes[consumer].triggered_by.add('e' * 40)
        p.series_groups = {}
        p._body_cache = {}
        p.target = 'f' * 40
        p.info = lambda *_: None
        prefix = '20250701114733.636510'
        domain = '@linaro.org'
        def body(sha):
            number = '23' if sha == consumer else '22'
            return f'Link: https://lore.kernel.org/r/{prefix}-{number}-ulf.hansson{domain}\n'
        p.body = body
        p.git = types.SimpleNamespace(run=lambda cmd: (
            provider + '\0' + body(provider) + '\0'
            + consumer + '\0' + body(consumer) + '\0'))
        p.match = lambda sha: types.SimpleNamespace(status='missing')
        p.upstream_before = lambda first, second: first == provider and second == consumer
        self.assertFalse(p.is_actionable_node(p.nodes[consumer]))
        p.build_series_index(seed_shas=[consumer])
        self.assertEqual(len(p.series_groups), 1)
        self.assertEqual(p.advisor_allowed_prerequisite_peers(consumer), [provider])
        self.assertFalse(p.is_actionable_node(p.nodes[consumer]))

    def fixture(self, base_core='static int unrelated(void) { return 0; }\n'):
        p = object.__new__(kbp.Planner)
        p.args = types.SimpleNamespace(advisor_max_diff_chars=20000,
                                       advisor_max_peer_diff_chars=16000)
        p.replay_start = 'f' * 40
        consumer = 'a' * 40
        field = 'b' * 40
        of_helper = 'c' * 40
        sync_helper = 'd' * 40
        shas = [field, of_helper, sync_helper]
        p.nodes = {sha: kbp.CommitNode(sha, sha) for sha in [consumer, *shas]}
        p.advisor_allowed_prerequisite_peers = lambda sha: shas
        p.advisor_same_series_consumed_objects = lambda sha: []
        patches = {
            consumer: '''diff --git a/drivers/pmdomain/core.c b/drivers/pmdomain/core.c
@@ -3459,6 +3461,7 @@ void of_genpd_sync_state(struct device_node *np)
+	genpd->stay_on = false;
@@ -3486,6 +3489,7 @@ static void genpd_provider_sync_state(struct device *dev)
+	genpd->stay_on = false;
''',
            field: '''diff --git a/include/linux/pm_domain.h b/include/linux/pm_domain.h
@@ -199,6 +199,7 @@ struct generic_pm_domain {
+	bool stay_on;
''',
            of_helper: '''diff --git a/drivers/pmdomain/core.c b/drivers/pmdomain/core.c
@@ -0,0 +1,5 @@
+void of_genpd_sync_state(struct device_node *np)
+{
+	genpd_power_off(NULL, false, 0);
+}
''',
            sync_helper: '''diff --git a/drivers/pmdomain/core.c b/drivers/pmdomain/core.c
@@ -0,0 +1,5 @@
+static void genpd_provider_sync_state(struct device *dev)
+{
+	return;
+}
''',
        }
        p.patch_excerpt = lambda sha, limit: patches[sha]
        p._tree_file_text = lambda rev, path: (
            'struct generic_pm_domain { bool other; };\n'
            if path.endswith('.h') else base_core)
        p.subject = lambda sha: p.nodes[sha].subject
        return p, consumer, field, of_helper, sync_helper

    def test_exact_power_domain_earlier_peers_are_source_evidence(self):
        p, consumer, field, of_helper, sync_helper = self.fixture()
        evidence = p.advisor_prerequisite_evidence(consumer)
        self.assertEqual({(e['kind'], e['object'], e['peer_sha']) for e in evidence}, {
            ('missing-struct-member', 'stay_on', field),
            ('missing-code-function', 'of_genpd_sync_state', of_helper),
            ('missing-code-function', 'genpd_provider_sync_state', sync_helper),
        })
        self.assertTrue(all(e['baseline_provider_declaration'] == 'absent'
                            for e in evidence))
        self.assertTrue(all(not p.nodes[e['peer_sha']].required_by for e in evidence))
        self.assertEqual(evidence, p.advisor_prerequisite_evidence(consumer))

    def test_existing_downstream_function_is_not_missing(self):
        baseline = 'void of_genpd_sync_state(struct device_node *np)\n{\n}\n'
        p, consumer, field, of_helper, sync_helper = self.fixture(baseline)
        evidence = p.advisor_missing_code_function_evidence(consumer)
        self.assertEqual([(e['object'], e['peer_sha']) for e in evidence],
                         [('genpd_provider_sync_state', sync_helper)])

    def test_peer_call_without_definition_is_not_provider(self):
        p, consumer, field, of_helper, sync_helper = self.fixture()
        real = p.patch_excerpt
        p.patch_excerpt = lambda sha, limit: (
            'diff --git a/drivers/pmdomain/core.c b/drivers/pmdomain/core.c\n'
            '+of_genpd_sync_state(NULL);\n'
            if sha == of_helper else real(sha, limit))
        evidence = p.advisor_missing_code_function_evidence(consumer)
        self.assertEqual([(e['object'], e['peer_sha']) for e in evidence],
                         [('genpd_provider_sync_state', sync_helper)])
