#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""v0.1.36: disagreement REVIEW assessments, safe adjudication and cached TAKEs."""
import json
import unittest

import test_conditioning_033 as _previous
import test_kernel_backport as _fixture
core = _previous.core
git = _previous.git
from feature_assessor import (build_disagreement_features, assessment_cache_key,
                              feature_metadata, _digest, read_feature_cache)
from feature_conditioner import read_conditioning_history


class DisagreementConditioningTests(unittest.TestCase):
    setUp = _fixture.UnifiedFixture.setUp
    make_commit = _fixture.UnifiedFixture.make_commit
    write_plan = _fixture.UnifiedFixture.write_plan
    runcli = _fixture.UnifiedFixture.runcli
    decide = _fixture.UnifiedFixture.decide
    check = _fixture.UnifiedFixture.check
    cache_group = _previous.ConditioningTests.cache_group

    def configure_disagreement(self, sha, *, decision="REVIEW", status="disagree",
                               fixes=None, consumer=None):
        payload = json.loads((self.directory / 'backport_plan.json').read_text())
        payload['commits'][sha]['advisor_decision'] = decision
        payload['commits'][sha]['advisor_escalation_status'] = status
        payload['commits'][sha]['fixes'] = fixes or []
        if consumer:
            payload['commits'][consumer]['advisor_prerequisites'] = [{'sha': sha}]
        kind = {'disagree': 'DISAGREEMENT', 'error': 'ERROR',
                'budget-deferred': 'BUDGET'}[status]
        payload['advisor'] = {'execution_ready': False, 'execution_blockers': [
            {'kind': 'ADVISOR_ESCALATION->' + kind, 'sha': sha,
             'reason': 'Terra/Sol decision conflict'}]}
        (self.directory / 'backport_plan.json').write_text(json.dumps(payload))
        worksheet = self.directory / 'backport_worksheet.txt'
        content = worksheet.read_text()
        # The original fixture emits each header followed by one advisor line.
        begin = content.index(sha[:12] + ' [DIRECT]')
        pos = content.index('      advisor: ', begin)
        end = content.index('\n', pos)
        content = content[:pos] + '      advisor: ' + decision + content[end:]
        worksheet.write_text(content)

    def cache_disagreement(self, *, sha, role='OPTIONAL', action='EXCLUDE',
                           selection='EXCLUDE', value='LOW', confidence='high',
                           verify=False):
        plan = core.Plan(self.directory)
        icc = core._fresh_icc(plan.directory, plan.plan_digest, plan.revision())
        report = core.refresh_review_reports(plan)
        groups = build_disagreement_features(plan, report)
        group = next(g for g in groups if sha in g['members'])
        result = {
            'recommendation': action,
            'selection_decision': selection,
            'inclusion_basis': 'No applicable shipped product, no required consumer',
            'exclusion_impact': 'No demonstrated supported functionality lost',
            'capability': 'Optional code cleanup', 'minimum_candidate_shas': [],
            'minimum_set_conditions': 'Nothing required by supported configurations',
            'member_roles': [{'sha': s, 'role': role} for s in group['members']],
            'backport_value': value, 'confidence': confidence,
            'feature_rationale': 'Cleanly optional, no RHEL requirement',
            'member_recommendations': [{'sha': s, 'recommendation': action,
                'reason': 'No supported consumer and no correctness obligation'}
                for s in group['members']],
            'evidence': [{'sha': s, 'source': 'patch',
                'finding': 'Pure cleanup without independent fix'} for s in group['members']],
            'next_action': 'Exclude optional upstream change',
            'risks': 'No supported configuration exposed',
            'needs_verification': verify,
        }
        doc = read_feature_cache(self.directory)
        key = assessment_cache_key(group['id'], 'gpt-6.1-sol', 'gpt-6.1-terra')
        doc['assessments'][key] = {
            'model': 'gpt-6.1-sol', 'summary_model': 'gpt-6.1-terra',
            'evidence_sha256': _digest(feature_metadata(plan, group, report, icc)),
            'result': result, 'patch_coverage': 'full-inline',
            'source_coverage': 'sampled'}
        target = self.directory / '.kernel-backport' / 'feature-assessments.json'
        target.parent.mkdir(exist_ok=True)
        target.write_text(json.dumps(doc))
        return group

    def test_review_disagreement_auto_skip_and_provenance(self):
        self.configure_disagreement(self.a)
        self.assertEqual(self.check().returncode, 2)
        group = self.cache_disagreement(sha=self.a)
        self.assertTrue(group['id'].startswith('D-'))
        run = self.runcli('condition', '--scope', 'all', '--allow-unresolved',
                          '--auto-skip-disagreements', '--apply-auto-skips')
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertIn('Disputed REVIEW feature groups: 1', run.stdout)
        plan = core.Plan(self.directory)
        self.assertEqual(plan.effective(self.a), 'SKIP')
        self.assertNotIn(self.a, plan.human_overrides)
        self.assertIn('resolution=advisor-disagreement', plan.auto_overrides[self.a]['source'])
        self.assertIn(group['id'], plan.auto_overrides[self.a]['source'])
        self.assertEqual(plan.advisor_readiness_blockers(), [])
        self.assertEqual(self.check('--no-replay').returncode, 0)
        report = json.loads((self.directory / 'conditioning-decisions.json').read_text())
        self.assertTrue(any(p.get('prior_decision') == 'REVIEW' for p in report['proposed_skips']))
        self.assertTrue(any(x['id'] == group['id'] for x in report['feature_decisions']))
        self.assertGreaterEqual(report['disagreement_groups_considered'], 1)
        self.assertTrue(any(e['action'] == 'COMMITTED_AUTO_SKIPS'
                            for e in read_conditioning_history(self.directory, plan.plan_digest)['events']))
        self.assertEqual(git(self.work, 'status', '--porcelain'), '')
        # The engineer must be able to reverse the auto-adjudication.
        self.assertEqual(self.decide(self.a, 'REVIEW', 'Need supported platform audit').returncode, 0)
        self.assertEqual(len(core.Plan(self.directory).advisor_readiness_blockers()), 1)

    def test_review_not_assessed_without_disagreement_opt_in(self):
        self.configure_disagreement(self.a)
        self.assertEqual(self.check().returncode, 2)
        self.cache_disagreement(sha=self.a)
        result = self.runcli('condition', '--scope', 'all', '--allow-unresolved',
                             '--apply-auto-skips')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(core.Plan(self.directory).effective(self.a), 'REVIEW')
        self.assertIn('Disputed REVIEW groups: 0',
                      (self.directory / 'conditioning-decisions.txt').read_text())

    def test_review_holds_uncertain_or_correctness_commit(self):
        for case in ('verify', 'confidence', 'fixes', 'hazard'):
            with self.subTest(case=case):
                self.configure_disagreement(self.a,
                     fixes=([self.base] if case == 'fixes' else []))
                if case == 'hazard':
                    data = json.loads((self.directory / 'backport_plan.json').read_text())
                    data['commits'][self.a]['subject'] = 'Fix crash on supported systems'
                    (self.directory / 'backport_plan.json').write_text(json.dumps(data))
                self.assertEqual(self.check().returncode, 2)
                self.cache_disagreement(sha=self.a,
                     verify=(case == 'verify'),
                     confidence=('medium' if case == 'confidence' else 'high'))
                run = self.runcli('condition', '--scope', 'all', '--allow-unresolved',
                                  '--auto-skip-disagreements', '--apply-auto-skips')
                self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
                self.assertEqual(core.Plan(self.directory).effective(self.a), 'REVIEW')
                self.assertTrue(core.Plan(self.directory).advisor_readiness_blockers())
                # Fresh test fixture for following subtest (no nested setUp on reused state).
                if case != 'hazard':
                    self.write_plan()
                    (self.directory / '.kernel-backport' / 'conditioning-history.json').unlink(missing_ok=True)

    def test_retained_take_consumer_blocks_review_provider(self):
        self.configure_disagreement(self.a, consumer=self.b)
        self.assertEqual(self.check().returncode, 2)
        self.cache_disagreement(sha=self.a)
        run = self.runcli('condition', '--scope', 'all', '--allow-unresolved',
                          '--auto-skip-disagreements', '--apply-auto-skips')
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertEqual(core.Plan(self.directory).effective(self.a), 'REVIEW')
        report = json.loads((self.directory / 'conditioning-decisions.json').read_text())
        self.assertTrue(any('consumer' in x['why'] for x in report['exceptions']
                            if x['sha'] == self.a))
        self.assertIn('outside-commit-claims-member-prerequisite',
             str(feature_metadata(core.Plan(self.directory),
                 build_disagreement_features(core.Plan(self.directory),
                   core.refresh_review_reports(core.Plan(self.directory)))[0],
                 core.refresh_review_reports(core.Plan(self.directory)), None)))

    def test_error_escalation_not_auto_adjudicated(self):
        self.configure_disagreement(self.a, status='error')
        self.assertEqual(self.check().returncode, 2)
        plan = core.Plan(self.directory)
        report = core.refresh_review_reports(plan)
        self.assertEqual(build_disagreement_features(plan, report), [])
        run = self.runcli('condition', '--scope', 'all', '--allow-unresolved',
                          '--auto-skip-disagreements', '--apply-auto-skips')
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertEqual(core.Plan(self.directory).effective(self.a), 'REVIEW')
        self.assertEqual(len(core.Plan(self.directory).advisor_readiness_blockers()), 1)

    def test_invalid_review_assessment_stays_held(self):
        self.configure_disagreement(self.a)
        self.assertEqual(self.check().returncode, 2)
        group = self.cache_disagreement(sha=self.a)
        cache_file = self.directory / '.kernel-backport' / 'feature-assessments.json'
        obj = json.loads(cache_file.read_text())
        key = assessment_cache_key(group['id'], 'gpt-6.1-sol', 'gpt-6.1-terra')
        obj['assessments'][key].pop('result')
        obj['assessments'][key]['pending_raw'] = {}
        obj['assessments'][key]['validation_error'] = 'invalid feature member roles'
        cache_file.write_text(json.dumps(obj))
        run = self.runcli('condition', '--scope', 'all', '--allow-unresolved',
                          '--auto-skip-disagreements', '--apply-auto-skips')
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertEqual(core.Plan(self.directory).effective(self.a), 'REVIEW')
        report = json.loads((self.directory / 'conditioning-decisions.json').read_text())
        self.assertTrue(any(x['id'] == group['id'] and
                            x['assessment_status'] == 'INVALID'
                            for x in report['feature_decisions']))

    def test_new_dispute_assessment_uses_dedicated_prompt_and_cache(self):
        from feature_assessor import assess_features
        from unittest.mock import patch
        self.configure_disagreement(self.a)
        self.assertEqual(self.check().returncode, 2)
        group = self.cache_disagreement(sha=self.a)
        cache_file = self.directory / '.kernel-backport' / 'feature-assessments.json'
        cached = json.loads(cache_file.read_text())
        key = assessment_cache_key(group['id'], 'gpt-6.1-sol', 'gpt-6.1-terra')
        result = cached['assessments'].pop(key)['result']
        cache_file.write_text(json.dumps(cached))
        called = []

        class FakeReviewer:
            def assess_feature(self, packet, prompt, schema):
                called.append((packet, prompt, schema))
                return result

        plan = core.Plan(self.directory)
        report = core.refresh_review_reports(plan)
        icc = core._fresh_icc(plan.directory, plan.plan_digest, plan.revision())
        groups = build_disagreement_features(plan, report)
        dossier = {'patches': [{'mode': 'complete-inline'}],
                   'downstream': {'status': 'sampled'},
                   'upstream_coverage': 'full-inline'}
        with patch('feature_assessor.collect_evidence', return_value=dossier):
            stats = assess_features(plan, groups, report, icc, self.work,
                                    model='gpt-6.1-sol',
                                    segment_model='gpt-6.1-terra',
                                    client=FakeReviewer())
        self.assertEqual(stats['new_assessments'], 1)
        self.assertEqual(groups[0]['assessment_status'], 'assessed')
        self.assertEqual(len(called), 1)
        packet, prompt, schema = called[0]
        self.assertIn('unresolved REVIEW commits, NOT TAKEs', prompt)
        self.assertEqual(packet['feature']['advisor_disagreement_context']
                         ['planner_disagreements'][0]['sha'], self.a)
        self.assertEqual(schema['properties']['member_roles']['items']
                         ['properties']['sha']['enum'], [self.a])
        self.assertEqual(read_feature_cache(self.directory)['assessments'][key]
                         ['result']['member_roles'][0]['sha'], self.a)

    def test_current_take_cache_is_not_invalidated(self):
        self.configure_disagreement(self.a)
        self.assertEqual(self.check().returncode, 2)
        group = self.cache_group(sha=self.b)
        self.cache_disagreement(sha=self.a)
        plan = core.Plan(self.directory)
        report = core.refresh_review_reports(plan)
        groups = core.build_features(plan, report)
        self.assertEqual(group['id'], next(x['id'] for x in groups if self.b in x['members']))
        self.assertEqual(len(build_disagreement_features(plan, report)), 1)
        attach = __import__('feature_assessor').attach_feature_cache
        attach(plan, groups, report,
               core._fresh_icc(plan.directory, plan.plan_digest, plan.revision()),
               model='gpt-6.1-sol', summary_model='gpt-6.1-terra')
        self.assertTrue(any(x['assessment_status'] == 'assessed' for x in groups))


if __name__ == '__main__':
    unittest.main()
