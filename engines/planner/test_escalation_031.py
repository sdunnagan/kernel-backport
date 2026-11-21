"""Selective Terra -> Sol planning escalation regression tests (0.1.31)."""
import collections
import types
import unittest
from unittest import mock
import test_kernel_backport_planner as legacy
kbp = legacy.kbp


class Routing031Tests(unittest.TestCase):
    def test_cli_accepts_selective_two_model_split(self):
        opts = kbp.parse_args([
            '--base', 'v6.19', '--target', 'v7.0', '--advisor', 'openai',
            '--advisor-model', 'gpt-6.1-terra',
            '--advisor-escalation-model', 'gpt-6.1-sol',
            '--advisor-escalation-policy', 'selective',
        ])
        self.assertEqual(opts.advisor_escalation_limit, 96)
        self.assertEqual(opts.advisor_escalation_provider, 'openai')
        self.assertEqual(opts.advisor_escalation_model, 'gpt-6.1-sol')

    def test_cloud_preflight_checks_both_models_before_planning(self):
        args = kbp.parse_args([
            '--base', 'v6.19', '--target', 'v7.0', '--advisor', 'openai',
            '--advisor-model', 'gpt-6.1-terra',
            '--advisor-escalation-model', 'gpt-6.1-sol',
            '--advisor-escalation-policy', 'selective',
            '--advisor-escalation-timeout', '444',
        ])
        sent = []
        response = legacy.OpenAIAdvisor0433PreflightTests.response()
        network = legacy.OpenAIAdvisor0433PreflightTests.fake_urlopen(response, sent)
        with mock.patch.dict(kbp.os.environ, {'OPENAI_API_KEY': 'test-key'}):
            with mock.patch.object(kbp.urllib.request, 'urlopen', side_effect=network):
                self.assertTrue(kbp.preflight_openai_advisors(args))
        self.assertEqual([__import__('json').loads(req.data)['model'] for req, _ in sent],
                         ['gpt-6.1-terra', 'gpt-6.1-sol'])
        self.assertEqual([timeout for _, timeout in sent],
                         [args.advisor_timeout, 444.0])

    def test_escalation_cache_does_not_reuse_terra_response(self):
        p = self.planner()
        p.base = 'a'*40
        p.target = 'b'*40
        p.downstream = 'c'*40
        p.advisor_model = 'gpt-6.1-terra'
        p._advisor_stage = 'primary'
        p.args.advisor = 'openai'
        original = p.advisor_cache_key('d'*40, {'source': 'identical'})
        p.advisor_model = 'gpt-6.1-sol'
        p._advisor_stage = 'escalation'
        secondary = p.advisor_cache_key('d'*40, {
            'source': 'identical', 'independent_escalation_policy_version': '1'})
        self.assertNotEqual(original, secondary)

    def test_bad_combinations_fail_early(self):
        base = ['--base', 'v6.19', '--target', 'v7.0', '--advisor', 'openai']
        cases = [
            ['--advisor-escalation-model', 'gpt-6.1-sol'],
            ['--advisor-escalation-policy', 'selective'],
            ['--advisor-escalation-model', 'gpt-6.1-terra', '--advisor-escalation-policy', 'selective'],
            ['--advisor-escalation-model', 'gpt-6.1-sol', '--advisor-escalation-policy', 'selective',
             '--advisor-fallback-model', 'gemma4'],
            ['--advisor-escalation-model', 'gpt-6.1-sol', '--advisor-escalation-policy', 'selective',
             '--advisor-escalation-limit', '-1'],
        ]
        for extra in cases:
            with self.subTest(extra=extra), self.assertRaises(SystemExit):
                with mock.patch('sys.stderr'):
                    kbp.parse_args(base + extra)

    def planner(self):
        p = object.__new__(kbp.Planner)
        p.nodes = {}
        p.advisor_consistency_issues = []
        p.advisor_model = 'gpt-6.1-terra'
        p._advisor_stage = 'primary'
        p.args = types.SimpleNamespace(advisor_escalation_policy='selective',
            advisor_escalation_model='gpt-6.1-sol', advisor_escalation_provider='openai',
            advisor_escalation_limit=96, advisor_escalation_timeout=600.,
            advisor_escalation_context_tokens=131072,
            advisor_escalation_max_request_chars=120000,
            advisor_max_request_chars=120000, advisor_context_tokens=131072)
        p.advisor_escalation_model = 'gpt-6.1-sol'
        p.advisor_escalation_candidates = 0
        p.advisor_escalation_evaluated = 0
        p.advisor_escalation_agreed = 0
        p.advisor_escalation_disagreed = 0
        p.advisor_escalation_failed = 0
        p.advisor_escalation_unprocessed = 0
        p.advisor_escalation_issues = []
        p.advisor_failure_kinds = collections.Counter()
        p.advisor_failures = 0
        p.platform_relevance = lambda _sha: None
        p.info = lambda _msg: None
        p.advisor_system_prompt = lambda: 'shared planning prompt'
        p._record_advisor_prerequisite_recommendations = lambda _sha: None
        p._advisor_resolve_rejected_prerequisites = lambda: None
        p.ordered_missing = lambda: []
        p._advisor_check_consistency = lambda: None
        return p

    def node(self, p, symbol, subject, decision, *, error=None):
        n = kbp.CommitNode(symbol * 40, subject, direct=True)
        n.advisor_decision = decision
        n.advisor_rationale = 'original reasoning'
        n.advisor_decision_model = p.advisor_model
        n.advisor_error_kind = error
        n.advisor_error = 'primary error' if error else None
        p.nodes[n.sha] = n
        return n

    def test_uncertainty_and_risky_skip_without_conflict_only(self):
        p = self.planner()
        optional = self.node(p, 'a', 'arm64: optimize slow function', 'TAKE')
        fix = self.node(p, 'b', 'arm64: fix deadlock during migration', 'SKIP')
        uncertain = self.node(p, 'c', 'arm64: change interface', 'REVIEW')
        conflict_only = self.node(p, 'd', 'arm64: add required function', 'TAKE')
        conflict_only.replay = 'conflict'
        routed = {s: set(why) for s, why in p._advisor_escalation_candidates()}
        self.assertIn('optional-take', routed[optional.sha])
        self.assertIn('possibly-applicable-fix-skip', routed[fix.sha])
        self.assertIn('undecided-review', routed[uncertain.sha])
        self.assertNotIn(conflict_only.sha, routed)

    def test_model_disagreement_is_review_and_original_votes_preserved(self):
        p = self.planner()
        node = self.node(p, 'a', 'arm64: optimize foo', 'TAKE')
        p._advisor_escalation_candidates = lambda: [(node.sha, ['optional-take'])]
        def fake_eval(_client, _prompt, sha, **kwargs):
            self.assertEqual(kwargs['escalation_policy_version'], '1')
            self.assertEqual(p.advisor_model, 'gpt-6.1-sol')
            p.nodes[sha].advisor_decision = 'SKIP'
            p.nodes[sha].advisor_rationale = 'No demonstrated product benefit'
            p.nodes[sha].advisor_decision_model = p.advisor_model
        p._advisor_evaluate_one = fake_eval
        with mock.patch.object(kbp, 'make_advisor_client', return_value=object()):
            p._advisor_escalation_pass()
        self.assertEqual(node.advisor_decision, 'REVIEW')
        self.assertEqual(node.advisor_escalation_status, 'disagree')
        self.assertEqual(node.advisor_escalation_result['primary']['advisor_decision'], 'TAKE')
        self.assertEqual(node.advisor_escalation_result['secondary']['advisor_decision'], 'SKIP')
        self.assertEqual(node.advisor_escalation_result['secondary']['advisor_rationale'],
                         'No demonstrated product benefit')
        self.assertEqual(p.advisor_model, 'gpt-6.1-terra')
        self.assertEqual(p._advisor_stage, 'primary')
        self.assertEqual(p.advisor_escalation_disagreed, 1)
        blockers = p._advisor_execution_blockers()
        self.assertTrue(any(b['kind'] == 'ADVISOR_ESCALATION->DISAGREEMENT' for b in blockers))

    def test_secondary_batch_reads_frozen_primary_graph(self):
        p = self.planner()
        first = self.node(p, 'a', 'arm64: optimize one', 'TAKE')
        second = self.node(p, 'b', 'arm64: optimize two', 'TAKE')
        p._advisor_escalation_candidates = lambda: [
            (first.sha, ['optional-take']), (second.sha, ['optional-take'])]
        seen = []
        def fake_eval(_client, _prompt, sha, **kwargs):
            # The second Sol evaluation must not see the REVIEW imposed by
            # reconciliation of the first Sol disagreement.
            seen.append(p.nodes[first.sha].advisor_decision)
            p.nodes[sha].advisor_decision = 'SKIP'
            p.nodes[sha].advisor_decision_model = 'gpt-6.1-sol'
        p._advisor_evaluate_one = fake_eval
        with mock.patch.object(kbp, 'make_advisor_client', return_value=object()):
            p._advisor_escalation_pass()
        self.assertEqual(seen, ['TAKE', 'TAKE'])
        self.assertEqual([first.advisor_decision, second.advisor_decision],
                         ['REVIEW', 'REVIEW'])

    def test_agreement_preserves_take_and_prerequisite_edges(self):
        p = self.planner()
        node = self.node(p, 'a', 'arm64: optimize foo', 'TAKE')
        node.advisor_prerequisites = [{'sha': 'b'*40, 'reason': 'uses type', 'evidence_id': 'E1'}]
        p._advisor_escalation_candidates = lambda: [(node.sha, ['optional-take'])]
        def fake_eval(_client, _prompt, sha, **kwargs):
            p.nodes[sha].advisor_decision = 'TAKE'
            p.nodes[sha].advisor_rationale = 'necessary optimization'
            p.nodes[sha].advisor_prerequisites = [{'sha': 'b'*40, 'reason': 'confirmed', 'evidence_id': 'E1'}]
        p._advisor_evaluate_one = fake_eval
        with mock.patch.object(kbp, 'make_advisor_client', return_value=object()):
            p._advisor_escalation_pass()
        self.assertEqual(node.advisor_decision, 'TAKE')
        self.assertEqual(node.advisor_escalation_status, 'agree')
        self.assertEqual(node.advisor_prerequisites[0]['reason'], 'uses type')

    def test_dependency_disagreement_is_review_even_when_both_take(self):
        p = self.planner()
        node = self.node(p, 'a', 'arm64: optimize foo', 'TAKE')
        node.advisor_prerequisites = [{'sha': 'b'*40}]
        p._advisor_escalation_candidates = lambda: [(node.sha, ['optional-take'])]
        def fake_eval(_client, _prompt, sha, **kwargs):
            p.nodes[sha].advisor_decision = 'TAKE'
            p.nodes[sha].advisor_prerequisites = [{'sha': 'c'*40}]
        p._advisor_evaluate_one = fake_eval
        with mock.patch.object(kbp, 'make_advisor_client', return_value=object()):
            p._advisor_escalation_pass()
        self.assertEqual(node.advisor_decision, 'REVIEW')
        self.assertEqual(node.advisor_prerequisites, [])
        self.assertEqual(node.advisor_escalation_status, 'disagree')

    def test_failed_secondary_blocks_execution_not_silent_take(self):
        p = self.planner()
        node = self.node(p, 'a', 'arm64: optimize foo', 'TAKE')
        p._advisor_escalation_candidates = lambda: [(node.sha, ['optional-take'])]
        def fake_eval(_client, _prompt, sha, **kwargs):
            p.nodes[sha].advisor_decision = 'REVIEW'
            p.nodes[sha].advisor_error_kind = 'timeout'
            p.nodes[sha].advisor_error = 'Sol timeout'
        p._advisor_evaluate_one = fake_eval
        with mock.patch.object(kbp, 'make_advisor_client', return_value=object()):
            p._advisor_escalation_pass()
        self.assertEqual(node.advisor_decision, 'REVIEW')
        self.assertEqual(p.advisor_escalation_failed, 1)
        self.assertTrue(any(x['kind'] == 'ADVISOR_ESCALATION->ERROR'
                            for x in p._advisor_execution_blockers()))

    def test_budget_overflow_blocks_readiness(self):
        p = self.planner()
        p.args.advisor_escalation_limit = 1
        nodes = [self.node(p, c, 'arm64: optimize foo', 'TAKE') for c in 'ab']
        p._advisor_escalation_candidates = lambda: [(n.sha, ['optional-take']) for n in nodes]
        p._advisor_evaluate_one = lambda _client, _prompt, sha, **kwargs: None
        with mock.patch.object(kbp, 'make_advisor_client', return_value=object()):
            p._advisor_escalation_pass()
        self.assertEqual(p.advisor_escalation_unprocessed, 1)
        self.assertEqual(nodes[1].advisor_decision, 'REVIEW')
        self.assertEqual(nodes[1].advisor_escalation_status, 'budget-deferred')
        self.assertTrue(any(x['kind'] == 'ADVISOR_ESCALATION->BUDGET'
                            for x in p._advisor_execution_blockers()))


if __name__ == '__main__':
    unittest.main()
