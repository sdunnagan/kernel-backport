#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""0.1.33 safety and workflow regressions using disposable local Git repos."""
import importlib.machinery
import importlib.util
import json
import unittest
from pathlib import Path

import test_kernel_backport as _fixture
git = _fixture.git
CLI = _fixture.CLI
from feature_assessor import (assessment_cache_key, feature_metadata, _digest,
                              SCHEMA_VERSION, read_feature_cache)
from feature_conditioner import recommend_conditioning, RequestCounter, read_conditioning_history

loader = importlib.machinery.SourceFileLoader('kb033_cli', str(CLI))
spec = importlib.util.spec_from_loader(loader.name, loader)
core = importlib.util.module_from_spec(spec)
loader.exec_module(core)


class ConditioningTests(unittest.TestCase):
    setUp = _fixture.UnifiedFixture.setUp
    make_commit = _fixture.UnifiedFixture.make_commit
    write_plan = _fixture.UnifiedFixture.write_plan
    runcli = _fixture.UnifiedFixture.runcli
    decide = _fixture.UnifiedFixture.decide
    check = _fixture.UnifiedFixture.check

    def cache_group(self, *, sha=None, action='EXCLUDE', role='OPTIONAL',
                    feature_value='LOW', feature_selection='EXCLUDE'):
        plan = core.Plan(self.directory)
        icc = core._fresh_icc(plan.directory, plan.plan_digest, plan.revision())
        report = core.refresh_review_reports(plan)
        groups = core.build_features(plan, report, priorities=None)
        group = next(g for g in groups if sha is None or sha in g['members'])
        recs = [{"sha": s, "recommendation": action, "reason": "Optional cleanup; no RHEL requirement"}
                for s in group['members']]
        result = {
            'recommendation': action, 'selection_decision': feature_selection,
            'inclusion_basis': 'No product requirement',
            'exclusion_impact': 'No demonstrated loss',
            'capability': 'Optional test capability', 'minimum_candidate_shas': [],
            'minimum_set_conditions': 'No retention needed',
            'member_roles': [{'sha': s, 'role': role} for s in group['members']],
            'backport_value': feature_value, 'confidence': 'medium',
            'feature_rationale': 'No required feature',
            'member_recommendations': recs,
            'evidence': [{'sha': s, 'source': 'patch', 'finding': 'Pure cleanup'} for s in group['members']],
            'next_action': 'Exclude optional', 'risks': 'None established',
            'needs_verification': True,
        }
        target = self.directory / '.kernel-backport' / 'feature-assessments.json'
        doc = read_feature_cache(self.directory)
        key = assessment_cache_key(group['id'], 'gpt-6.1-sol', 'gpt-6.1-terra')
        doc['assessments'][key] = {
            'model': 'gpt-6.1-sol', 'summary_model': 'gpt-6.1-terra',
            'evidence_sha256': _digest(feature_metadata(plan, group, report, icc)),
            'result': result, 'patch_coverage': 'full-inline',
            'source_coverage': 'sampled'}
        target.parent.mkdir(exist_ok=True)
        target.write_text(json.dumps(doc))
        return group

    def test_dry_run_does_not_change_worksheet_decisions(self):
        self.assertEqual(self.check().returncode, 0)
        self.cache_group(sha=self.a)
        previous = core.Plan(self.directory).revision()
        head = git(self.work, 'rev-parse', 'HEAD')
        run = self.runcli('condition', '--scope', 'all')
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertIn('Proposed safe SKIPs: 1', run.stdout)
        self.assertEqual(core.Plan(self.directory).revision(), previous)
        self.assertEqual(git(self.work, 'rev-parse', 'HEAD'), head)
        self.assertNotIn('auto-decision', (self.directory / 'backport_worksheet.txt').read_text())
        report = json.loads((self.directory / 'conditioning-decisions.json').read_text())
        self.assertEqual(len(report['proposed_skips']), 1)
        self.assertEqual(report['auto_skips_applied_this_run'], 0)
        log = read_conditioning_history(self.directory, core.Plan(self.directory).plan_digest)
        self.assertEqual(log['events'][0]['action'], 'PROPOSAL')

    def test_apply_guarded_skip_and_user_can_restore_take(self):
        self.assertEqual(self.check().returncode, 0)
        self.cache_group(sha=self.a)
        start = git(self.work, 'rev-parse', 'HEAD')
        run = self.runcli('condition', '--scope', 'all', '--apply-auto-skips')
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertIn('Applied 1 automatic SKIPs', run.stdout)
        self.assertEqual(core.Plan(self.directory).effective(self.a), 'SKIP')
        self.assertNotIn(self.a, core.Plan(self.directory).human_overrides)
        self.assertEqual(git(self.work, 'rev-parse', 'HEAD'), start)
        self.assertEqual(git(self.work, 'status', '--porcelain'), '')
        self.assertEqual(json.loads((self.directory / '.kernel-backport' / 'icc.json').read_text())['take_total'], 2)
        report = json.loads((self.directory / 'conditioning-decisions.json').read_text())
        self.assertEqual(report['auto_skips_applied_this_run'], 1)
        log = read_conditioning_history(self.directory, core.Plan(self.directory).plan_digest)
        self.assertTrue(any(e['action'] == 'COMMITTED_AUTO_SKIPS' for e in log['events']))
        self.assertEqual(self.decide(self.a, 'TAKE', 'Reviewer explicitly requested inclusion').returncode, 0)
        plan = core.Plan(self.directory)
        self.assertEqual(plan.effective(self.a), 'TAKE')
        self.assertEqual(plan.human_overrides[self.a]['decision'], 'TAKE')
        self.assertIn(self.a, plan.auto_overrides)
        self.assertEqual(self.check('--no-replay').returncode, 0)

    def test_manual_take_never_auto_replaced(self):
        self.assertEqual(self.check().returncode, 0)
        self.assertEqual(self.decide(self.a, 'TAKE', 'Manual policy').returncode, 0)
        self.assertEqual(self.check().returncode, 0)
        self.cache_group(sha=self.a)
        run = self.runcli('condition', '--scope', 'all', '--apply-auto-skips')
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertEqual(core.Plan(self.directory).effective(self.a), 'TAKE')
        self.assertNotIn(self.a, core.Plan(self.directory).auto_overrides)

    def test_fixes_or_critical_defect_never_auto_skipped(self):
        payload = json.loads((self.directory / 'backport_plan.json').read_text())
        payload['commits'][self.a]['fixes'] = [self.base]
        (self.directory / 'backport_plan.json').write_text(json.dumps(payload))
        self.assertEqual(self.check().returncode, 0)
        self.cache_group(sha=self.a, role='CLEANUP')
        run = self.runcli('condition', '--scope', 'all', '--apply-auto-skips')
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertEqual(core.Plan(self.directory).effective(self.a), 'TAKE')
        report = json.loads((self.directory / 'conditioning-decisions.json').read_text())
        self.assertTrue(any('Fixes relationship' in x['why'] for x in report['exceptions']))

    def test_related_consumer_blocks_auto_skip_provider(self):
        self.write_plan(prerequisites={self.b: [self.a]})
        self.assertEqual(self.check().returncode, 0)
        group = self.cache_group(sha=self.a)
        # A has an optimistic EXCLUDE but B remains selected and is not
        # independently eligible for the auto-SKIP policy.
        path = self.directory / '.kernel-backport' / 'feature-assessments.json'
        doc = json.loads(path.read_text())
        key = assessment_cache_key(group['id'], 'gpt-6.1-sol', 'gpt-6.1-terra')
        result = doc['assessments'][key]['result']
        result['member_recommendations'] = [
            {'sha': s, 'recommendation': ('EXCLUDE' if s == self.a else 'RETAIN'),
             'reason': 'Keep consumer' if s == self.b else 'Optional'}
            for s in group['members']]
        path.write_text(json.dumps(doc))
        run = self.runcli('condition', '--scope', 'all', '--apply-auto-skips')
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertEqual(core.Plan(self.directory).effective(self.a), 'TAKE')
        report = json.loads((self.directory / 'conditioning-decisions.json').read_text())
        self.assertTrue(any('consumer' in x['why'] for x in report['exceptions']))

    def test_fresh_plan_can_condition_with_unresolved_advisor_disagreement(self):
        # A safe optional member may be excluded even when another commit
        # requires human adjudication. The disagreement must still block apply.
        path = self.directory / 'backport_plan.json'
        payload = json.loads(path.read_text())
        payload['commits'][self.b]['advisor_escalation_status'] = 'disagree'
        payload['advisor'] = {'execution_ready': False, 'execution_blockers': [
            {'kind': 'ADVISOR_ESCALATION->DISAGREEMENT', 'sha': self.b,
             'reason': 'Engineer must resolve this disagreement'}]}
        path.write_text(json.dumps(payload))
        self.assertEqual(self.check().returncode, 2)
        self.cache_group(sha=self.a)
        blocked = self.runcli('condition', '--scope', 'all', '--apply-auto-skips')
        self.assertNotEqual(blocked.returncode, 0)
        run = self.runcli('condition', '--scope', 'all', '--allow-unresolved',
                          '--apply-auto-skips')
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertEqual(core.Plan(self.directory).effective(self.a), 'SKIP')
        self.assertEqual(core.Plan(self.directory).effective(self.b), 'TAKE')
        self.assertIn('OPEN ENGINEERING EXCEPTIONS', run.stdout)
        report = (self.directory / 'conditioning-decisions.txt').read_text()
        self.assertIn('UNRESOLVED EXECUTION OBLIGATIONS', report)
        self.assertIn('ADVISOR_ESCALATION->DISAGREEMENT', report)
        icc = json.loads((self.directory / '.kernel-backport' / 'icc.json').read_text())
        self.assertEqual(len(icc['advisor_readiness_blockers']), 1)
        self.assertEqual(self.check('--no-replay').returncode, 2)
        self.assertEqual(git(self.work, 'status', '--porcelain'), '')

    def test_explicit_optional_disagreement_auto_skipped_with_provenance(self):
        path = self.directory / 'backport_plan.json'
        payload = json.loads(path.read_text())
        payload['commits'][self.a]['advisor_escalation_status'] = 'disagree'
        payload['advisor'] = {'execution_ready': False, 'execution_blockers': [{
            'kind': 'ADVISOR_ESCALATION->DISAGREEMENT', 'sha': self.a,
            'reason': 'Terra/Sol disagree'}]}
        path.write_text(json.dumps(payload))
        self.assertEqual(self.check().returncode, 2)
        self.cache_group(sha=self.a)
        held = self.runcli('condition', '--scope', 'all', '--allow-unresolved',
                           '--apply-auto-skips')
        self.assertEqual(held.returncode, 0, held.stdout + held.stderr)
        self.assertEqual(core.Plan(self.directory).effective(self.a), 'TAKE')
        self.assertEqual(len(core.Plan(self.directory).advisor_readiness_blockers()), 1)
        accepted = self.runcli('condition', '--scope', 'all', '--allow-unresolved',
                               '--auto-skip-disagreements', '--apply-auto-skips')
        self.assertEqual(accepted.returncode, 0, accepted.stdout + accepted.stderr)
        plan = core.Plan(self.directory)
        self.assertEqual(plan.effective(self.a), 'SKIP')
        self.assertNotIn(self.a, plan.human_overrides)
        self.assertIn('resolution=advisor-disagreement', plan.auto_overrides[self.a]['source'])
        self.assertEqual(plan.advisor_readiness_blockers(), [])
        self.assertEqual(self.check('--no-replay').returncode, 0)
        history = read_conditioning_history(self.directory, plan.plan_digest)
        self.assertTrue(any(e['action'] == 'COMMITTED_AUTO_SKIPS' for e in history['events']))
        self.assertEqual(self.decide(self.a, 'REVIEW', 'Reopen exception').returncode, 0)
        self.assertEqual(len(core.Plan(self.directory).advisor_readiness_blockers()), 1)

    def test_auto_disagreement_never_clears_error_or_fixes(self):
        path = self.directory / 'backport_plan.json'
        payload = json.loads(path.read_text())
        payload['commits'][self.a]['advisor_escalation_status'] = 'error'
        payload['advisor'] = {'execution_ready': False, 'execution_blockers': [{
            'kind': 'ADVISOR_ESCALATION->ERROR', 'sha': self.a,
            'reason': 'Assessment error'}]}
        path.write_text(json.dumps(payload))
        self.assertEqual(self.check().returncode, 2)
        self.cache_group(sha=self.a)
        run = self.runcli('condition', '--scope', 'all', '--allow-unresolved',
                          '--auto-skip-disagreements', '--apply-auto-skips')
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertEqual(core.Plan(self.directory).effective(self.a), 'TAKE')
        self.assertEqual(len(core.Plan(self.directory).advisor_readiness_blockers()), 1)
    def test_auto_disagreement_never_skips_fixes_tagged_commit(self):
        path = self.directory / 'backport_plan.json'
        payload = json.loads(path.read_text())
        payload['commits'][self.a]['advisor_escalation_status'] = 'disagree'
        payload['commits'][self.a]['fixes'] = [self.base]
        payload['advisor'] = {'execution_ready': False, 'execution_blockers': [{
            'kind': 'ADVISOR_ESCALATION->DISAGREEMENT', 'sha': self.a,
            'reason': 'Terra/Sol disagree'}]}
        path.write_text(json.dumps(payload))
        self.assertEqual(self.check().returncode, 2)
        self.cache_group(sha=self.a)
        run = self.runcli('condition', '--scope', 'all', '--allow-unresolved',
                          '--auto-skip-disagreements', '--apply-auto-skips')
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertEqual(core.Plan(self.directory).effective(self.a), 'TAKE')
        self.assertEqual(len(core.Plan(self.directory).advisor_readiness_blockers()), 1)

    def test_auto_disagreement_requires_explicit_opt_in(self):
        result = self.runcli('condition', '--auto-skip-disagreements')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('requires --allow-unresolved', result.stderr)

    def test_unresolved_logical_dependency_cannot_be_silently_waived(self):
        # Existing graph blockers can remain visible; optional safe SKIPs may
        # still proceed, but the resulting plan must not become execution-ready.
        self.write_plan(decisions={self.a: 'REVIEW'},
                        prerequisites={self.b: [self.a]})
        self.assertEqual(self.check().returncode, 2)
        self.cache_group(sha=self.c)
        run = self.runcli('condition', '--scope', 'all', '--allow-unresolved',
                          '--apply-auto-skips')
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertEqual(core.Plan(self.directory).effective(self.c), 'SKIP')
        icc = json.loads((self.directory / '.kernel-backport' / 'icc.json').read_text())
        self.assertEqual(len(icc['logical_blockers']), 1)
        self.assertEqual(self.check('--no-replay').returncode, 2)

    def test_no_api_without_opt_in_and_request_counter(self):
        self.assertEqual(self.check().returncode, 0)
        run = self.runcli('condition', '--scope', 'all', '--max-api-requests', '2')
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertIn('Conditioning finished: 0 new feature assessments', run.stdout)
        self.assertEqual(json.loads((self.directory / 'conditioning-decisions.json').read_text())['api_usage']['attempted_api_requests'], 0)
        counter = RequestCounter(1)
        counter.before_request('terra')
        counter.on_usage('terra', {'input_tokens': 17, 'output_tokens': 5})
        with self.assertRaisesRegex(ValueError, 'limit 1 reached'):
            counter.before_request('sol')
        self.assertEqual(counter.summary()['input_tokens_reported'], 17)


if __name__ == '__main__':
    unittest.main()
