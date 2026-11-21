#!/usr/bin/env python3
"""Release 0.1.32 advisor-readiness and explicit engineer resolution regressions."""
import json
import unittest

import test_kernel_backport as _legacy
from test_kernel_backport import git


class AdvisorReadinessTests(unittest.TestCase):
    setUp = _legacy.UnifiedFixture.setUp
    make_commit = _legacy.UnifiedFixture.make_commit
    write_plan = _legacy.UnifiedFixture.write_plan
    runcli = _legacy.UnifiedFixture.runcli
    check = _legacy.UnifiedFixture.check
    decide = _legacy.UnifiedFixture.decide
    execute = _legacy.UnifiedFixture.execute
    state = _legacy.UnifiedFixture.state

    def add_escalation(self, sha=None, *, status='disagree', issue_kind=None):
        sha = sha or self.a
        path = self.directory / 'backport_plan.json'
        plan = json.loads(path.read_text())
        plan['commits'][sha]['advisor_escalation_status'] = status
        kind = issue_kind or {
            'disagree': 'ADVISOR_ESCALATION->DISAGREEMENT',
            'error': 'ADVISOR_ESCALATION->ERROR',
            'budget-deferred': 'ADVISOR_ESCALATION->BUDGET',
        }[status]
        issues = list((plan.get('advisor') or {}).get('execution_blockers', []))
        issues.append({'kind': kind, 'sha': sha, 'reason': 'independent assessment unresolved'})
        plan['advisor'] = {'execution_ready': False, 'execution_blockers': issues}
        path.write_text(json.dumps(plan))
        return sha

    def test_disagreement_blocks_check_and_apply(self):
        self.add_escalation()
        before = git(self.work, 'rev-parse', 'HEAD')
        result = self.check('--no-replay')
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn('Logical blockers: 0', result.stdout)
        self.assertIn('Advisor readiness blockers: 1', result.stdout)
        self.assertIn(self.a[:12], result.stdout)
        attestation = json.loads((self.directory / '.kernel-backport' / 'icc.json').read_text())
        self.assertEqual(len(attestation['advisor_readiness_blockers']), 1)
        self.assertEqual(attestation['replay']['status'], 'not-run')
        worksheet = (self.directory / 'backport_worksheet.txt').read_text()
        self.assertIn('take-integration: BLOCKED', worksheet)
        self.assertIn('ADVISOR_ESCALATION->DISAGREEMENT', worksheet)
        rejected = self.execute()
        self.assertNotEqual(rejected.returncode, 0)
        self.assertIn('unresolved advisor readiness blocker', rejected.stderr)
        self.assertEqual(git(self.work, 'rev-parse', 'HEAD'), before)
        self.assertFalse((self.directory / '.kernel-backport' / 'state.json').exists())

    def test_review_and_missing_rationale_do_not_resolve(self):
        self.add_escalation()
        self.assertEqual(self.decide(self.a, 'REVIEW', 'investigation ongoing').returncode, 0)
        self.assertEqual(self.check('--no-replay').returncode, 2)
        rejected = self.decide(self.a, 'TAKE')
        self.assertNotEqual(rejected.returncode, 0)
        self.assertIn('requires --reason', rejected.stderr)
        self.assertEqual(self.check('--no-replay').returncode, 2)

    def test_explicit_skip_or_take_resolves_and_icc_clears(self):
        self.add_escalation()
        self.assertEqual(self.check('--no-replay').returncode, 2)
        self.assertEqual(self.decide(self.a, 'SKIP', 'Feature not needed for supported systems').returncode, 0)
        result = self.check('--no-replay')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('Advisor readiness blockers: 0', result.stdout)
        report = json.loads((self.directory / '.kernel-backport' / 'icc.json').read_text())
        self.assertEqual(report['advisor_readiness_blockers'], [])
        worksheet = (self.directory / 'backport_worksheet.txt').read_text()
        self.assertNotIn('ADVISOR_ESCALATION->DISAGREEMENT', worksheet.split('BACKPORT WORKSHEET')[0])
        self.assertEqual(self.decide(self.a, 'TAKE', 'Required independent fix').returncode, 0)
        self.assertEqual(self.check('--no-replay').returncode, 0)
        self.assertIn('Advisor readiness blockers: 0', self.check('--no-replay').stdout)

    def test_full_replay_remains_available_when_readiness_blocked(self):
        self.add_escalation()
        result = self.check()
        self.assertEqual(result.returncode, 2)
        self.assertIn('Advisor readiness blockers: 1', result.stdout)
        self.assertIn('Exploratory replay: clean', result.stdout)
        attestation = json.loads((self.directory / '.kernel-backport' / 'icc.json').read_text())
        self.assertEqual(attestation['replay']['status'], 'clean')

    def test_legacy_icc_cannot_bypass_readiness_check(self):
        result = self.check('--no-replay')
        self.assertEqual(result.returncode, 0)
        file = self.directory / '.kernel-backport' / 'icc.json'
        old = json.loads(file.read_text())
        old.pop('advisor_readiness_blockers')
        file.write_text(json.dumps(old))
        denied = self.execute()
        self.assertNotEqual(denied.returncode, 0)
        self.assertIn('predates advisor readiness checks', denied.stderr)

    def test_budget_group_resolves_individually(self):
        self.add_escalation(self.a, status='budget-deferred')
        self.add_escalation(self.b, status='budget-deferred')
        path = self.directory / 'backport_plan.json'
        plan = json.loads(path.read_text())
        plan['advisor']['execution_blockers'] = [
            {'kind': 'ADVISOR_ESCALATION->BUDGET', 'reason': '2 candidates deferred'}]
        path.write_text(json.dumps(plan))
        result = self.check('--no-replay')
        self.assertEqual(result.returncode, 2)
        self.assertIn('Advisor readiness blockers: 2', result.stdout)
        self.assertEqual(self.decide(self.a, 'SKIP', 'optional').returncode, 0)
        self.assertIn('Advisor readiness blockers: 1', self.check('--no-replay').stdout)
        self.assertEqual(self.decide(self.b, 'TAKE', 'required fix').returncode, 0)
        self.assertEqual(self.check('--no-replay').returncode, 0)

    def test_aggregate_budget_without_candidate_status_fails_closed(self):
        path = self.directory / 'backport_plan.json'
        plan = json.loads(path.read_text())
        plan['advisor'] = {'execution_ready': False,
                           'execution_blockers': [{'kind': 'ADVISOR_ESCALATION->BUDGET',
                                                   'reason': 'unknown deferred cases'}]}
        path.write_text(json.dumps(plan))
        self.assertIn('Advisor readiness blockers: 1', self.check('--no-replay').stdout)

    def test_non_escalation_planner_issue_not_waived_by_decision(self):
        path = self.directory / 'backport_plan.json'
        plan = json.loads(path.read_text())
        plan['advisor'] = {'execution_ready': False,
                           'execution_blockers': [{'kind': 'SCOPE_TRADEOFF_REVIEW_PENDING',
                                                   'sha': self.a, 'reason': 'budget exhausted'}]}
        path.write_text(json.dumps(plan))
        self.assertEqual(self.decide(self.a, 'SKIP', 'optional').returncode, 0)
        result = self.check('--no-replay')
        self.assertEqual(result.returncode, 2)
        self.assertIn('SCOPE_TRADEOFF_REVIEW_PENDING', result.stdout)

    def _add_planner_take_review_issue(self):
        # B is a selected consumer; A is a REVIEW provider at planner time.
        self.write_plan(decisions={self.a: 'REVIEW'},
                        prerequisites={self.b: [self.a]})
        path = self.directory / 'backport_plan.json'
        plan = json.loads(path.read_text())
        plan['advisor'] = {'execution_ready': False, 'execution_blockers': [{
            'kind': 'TAKE->REVIEW', 'consumer_sha': self.b,
            'prerequisite_sha': self.a, 'prerequisite_decision': 'REVIEW',
            'reason': 'selected consumer needs provider',
        }]}
        path.write_text(json.dumps(plan))

    def test_planner_take_review_clears_when_provider_becomes_take(self):
        self._add_planner_take_review_issue()
        blocked = self.check('--no-replay')
        self.assertEqual(blocked.returncode, 2)
        self.assertIn('Advisor readiness blockers: 1', blocked.stdout)
        self.assertIn('Logical blockers: 1', blocked.stdout)
        self.assertEqual(self.decide(self.a, 'TAKE', 'Required by selected consumer').returncode, 0)
        ready = self.check('--no-replay')
        self.assertEqual(ready.returncode, 0, ready.stdout + ready.stderr)
        self.assertIn('Advisor readiness blockers: 0', ready.stdout)
        self.assertIn('Logical blockers: 0', ready.stdout)

    def test_planner_take_review_clears_when_consumer_skipped(self):
        self._add_planner_take_review_issue()
        self.assertEqual(self.decide(self.b, 'SKIP', 'Consumer out of scope').returncode, 0)
        ready = self.check('--no-replay')
        self.assertEqual(ready.returncode, 0, ready.stdout + ready.stderr)
        self.assertIn('Advisor readiness blockers: 0', ready.stdout)
        self.assertIn('Logical blockers: 0', ready.stdout)

    def test_planner_take_review_still_blocks_if_provider_skipped(self):
        self._add_planner_take_review_issue()
        self.assertEqual(self.decide(self.a, 'SKIP', 'Provider out of scope').returncode, 0)
        blocked = self.check('--no-replay')
        self.assertEqual(blocked.returncode, 2)
        self.assertIn('Advisor readiness blockers: 1', blocked.stdout)
        self.assertIn('Logical blockers: 1', blocked.stdout)

    def test_malformed_planner_take_review_issue_stays_blocked(self):
        self._add_planner_take_review_issue()
        path = self.directory / 'backport_plan.json'
        plan = json.loads(path.read_text())
        plan['advisor']['execution_blockers'][0]['consumer_sha'] = 'unknown'
        path.write_text(json.dumps(plan))
        self.assertEqual(self.decide(self.a, 'TAKE', 'Required by selected consumer').returncode, 0)
        blocked = self.check('--no-replay')
        self.assertEqual(blocked.returncode, 2)
        self.assertIn('Advisor readiness blockers: 1', blocked.stdout)

    def test_malformed_planner_blocker_fails_closed(self):
        path = self.directory / 'backport_plan.json'
        plan = json.loads(path.read_text())
        plan['advisor'] = {'execution_blockers': [{'kind': 'ADVISOR_ESCALATION->ERROR',
                                                   'sha': 'bad'}]}
        path.write_text(json.dumps(plan))
        result = self.check('--no-replay')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('invalid planner advisor execution blocker SHA', result.stderr)

    def test_escalation_status_without_aggregate_still_blocks(self):
        path = self.directory / 'backport_plan.json'
        plan = json.loads(path.read_text())
        plan['commits'][self.a]['advisor_escalation_status'] = 'error'
        path.write_text(json.dumps(plan))
        result = self.check('--no-replay')
        self.assertEqual(result.returncode, 2)
        self.assertIn('ADVISOR_ESCALATION->ERROR', result.stdout)

if __name__ == '__main__':
    unittest.main()
