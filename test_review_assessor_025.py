#!/usr/bin/env python3
"""0.1.25: patch-grounded valuation remains advisory, cached, and read-only."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest

from review_assessor import (assess_queues, attach_cached_assessments, evidence_metadata,
                             metadata_digest, read_cache, validate_result, git_patch)
from review_queues import build_queues, render_text, write_reports


def record(subject, files, decision='TAKE', **kwargs):
    return {
        'subject': subject, 'files': files, 'advisor_decision': decision,
        'advisor_rationale': 'The RHEL kernel should use the change if applicable.',
        'advisor_prerequisites': [], 'fixes': [], 'required_by': [],
        'match': {'status': 'missing'},
        'platform_relevance': {'status': 'SHARED_OR_UNKNOWN', 'matches': [],
                               'excluded_matches': []},
        **kwargs,
    }


class StubModel:
    def __init__(self, recommendation='EXCLUDE'):
        self.calls = []
        self.recommendation = recommendation

    def assess(self, packet):
        self.calls.append(packet)
        return {
            'recommendation': self.recommendation,
            'backport_value': 'LOW' if self.recommendation == 'EXCLUDE' else 'HIGH',
            'confidence': 'high',
            'rationale': 'Patch changes only whitespace; no semantic differences.',
            'evidence': [
                {'source': 'patch', 'finding': 'Whitespace-only hunk in test source.'},
                {'source': 'platform', 'finding': 'No selected SoC match.'},
                {'source': 'consumers', 'finding': 'No selected consumers.'},
                {'source': 'later_fixes', 'finding': 'No later fixes in plan.'},
            ],
            'next_action': 'Verify against downstream then skip if truly redundant.',
            'risks': 'Review any downstream adaptation before final exclusion.',
            'needs_verification': True,
        }


class ReviewAssessmentTests(unittest.TestCase):
    def setUp(self):
        self.work = tempfile.TemporaryDirectory()
        self.addCleanup(self.work.cleanup)
        self.root = Path(self.work.name)
        self.repo = self.root / 'repo'
        self.repo.mkdir()
        def git(*a):
            return subprocess.run(['git', *a], cwd=self.repo,
                                  capture_output=True, text=True, check=True).stdout.strip()
        git('init', '-q')
        git('config', 'user.name', 'Unit Test')
        git('config', 'user.email', 'test@example.net')
        (self.repo / 'file.c').write_text('int a = 1;\n')
        git('add', 'file.c')
        git('commit', '-qm', 'original')
        (self.repo / 'file.c').write_text('int a = 2;\n')
        git('add', 'file.c')
        git('commit', '-qm', 'cosmetic naming change')
        self.sha = git('rev-parse', 'HEAD')
        self.other = 'b' * 40
        self.records = {
            self.sha: record('Edit file', ['file.c']),
            self.other: record('Further change', ['file.c']),
        }
        self.order = [self.sha, self.other]
        self.plan = SimpleNamespace(
            overrides={}, commits=self.records, order=self.order, directory=self.root,
            plan_digest='c' * 64,
            payload={'platform_profile': {'sha256': 'd' * 64},
                     'advisor': {'product': {'product': 'RHEL 10'}},
                     'commits': self.records, 'order': self.order},
            effective=lambda s: self.records[s]['advisor_decision'],
        )
        self.icc = {
            'replay': {'status': 'blocked', 'commits': [
                {'sha': self.sha, 'status': 'conflict', 'conditional': False},
                {'sha': self.other, 'status': 'conflict', 'conditional': True},
            ]},
            'logical_blockers': [],
        }

    def report(self):
        return build_queues(self.plan.payload, icc=self.icc,
                            plan_sha256=self.plan.plan_digest)

    def test_actual_git_patch_consumers_fixes_and_platform_evidence(self):
        self.records[self.other]['advisor_prerequisites'] = [{'sha': self.sha,
                                                               'confidence': 'candidate'}]
        self.records[self.other]['fixes'] = [self.sha]
        self.records[self.sha]['platform_relevance']['matches'] = [
            {'platform': 'selected', 'support_basis': 'verified'}]
        report = self.report()
        item = next(x for x in report['queues']['p0'] if x['sha'] == self.sha)
        meta = evidence_metadata(self.plan, item, self.icc)
        self.assertEqual(meta['replay']['status'], 'conflict')
        self.assertEqual(meta['platform_relevance']['matches'][0]['platform'], 'selected')
        self.assertEqual(meta['selected_take_prerequisite_claims'][0]['sha'], self.other)
        self.assertEqual(meta['later_fixes_of_this_commit'][0]['sha'], self.other)
        patch, truncated = git_patch(self.repo, self.sha)
        self.assertFalse(truncated)
        self.assertIn('-int a = 1;', patch)
        self.assertIn('+int a = 2;', patch)

    def test_cached_recommendation_and_read_only_git(self):
        before_head = subprocess.check_output(['git', 'rev-parse', 'HEAD'],
                                             cwd=self.repo, text=True).strip()
        initial = copy.deepcopy(self.records)
        model = StubModel()
        first = self.report()
        stats = assess_queues(self.plan, first, self.icc, self.repo,
                              priorities=['p0'], model='test-model',
                              limit=1, client=model, print_fn=lambda *_: None)
        self.assertEqual(stats['new_assessments'], 1)
        self.assertEqual(len(model.calls), 1)
        self.assertIn('diff --git', model.calls[0]['upstream_patch'])
        assessed = [x for x in first['queues']['p0'] if x['assessment_status'] == 'assessed']
        self.assertEqual(len(assessed), 1)
        self.assertEqual(assessed[0]['backport_value'], 'LOW')
        self.assertEqual(assessed[0]['integration_difficulty'], 'conflict')
        # Reading a cached report produces no billable requests and no Git edits.
        second = self.report()
        attach_cached_assessments(self.plan, second, self.icc)
        self.assertEqual(len([x for x in second['queues']['p0'] if x['assessment']]), 1)
        self.assertEqual(subprocess.check_output(['git', 'status', '--porcelain'],
                                                  cwd=self.repo, text=True), '')
        self.assertEqual(subprocess.check_output(['git', 'rev-parse', 'HEAD'],
                                                  cwd=self.repo, text=True).strip(), before_head)
        self.assertEqual(self.records, initial)
        write_reports(self.root, second)
        text = (self.root / 'review-p0.txt').read_text()
        self.assertIn('recommendation: EXCLUDE', text)
        self.assertIn('backport value: LOW', text)
        self.assertTrue(all(len(line) <= 80 for line in text.splitlines()))
        cache = read_cache(self.root)
        self.assertEqual(cache['assessments'][self.sha]['model'], 'test-model')

    def test_invalidates_changed_relevant_evidence(self):
        model = StubModel()
        report = self.report()
        assess_queues(self.plan, report, self.icc, self.repo, priorities=['p0'],
                      model='test-model', limit=1, client=model, print_fn=lambda *_: None)
        first = metadata_digest(evidence_metadata(self.plan, report['queues']['p0'][0], self.icc))
        self.records[self.sha]['advisor_rationale'] = 'New engineer-relevant evidence'
        next_report = self.report()
        attach_cached_assessments(self.plan, next_report, self.icc)
        this = next(x for x in next_report['queues']['p0'] if x['sha'] == self.sha)
        self.assertNotEqual(first, metadata_digest(evidence_metadata(self.plan, this, self.icc)))
        self.assertEqual(this['assessment_status'], 'stale')

    def test_exclusion_blocked_by_fix_and_consumer_claims(self):
        meta = {'selected_take_prerequisite_claims': [{'sha': self.other}],
                'later_fixes_of_this_commit': [{'sha': self.other}],
                'originals_this_commit_fixes': []}
        stub = StubModel()
        candidate = stub.assess({})
        checked = validate_result(candidate, meta, patch_truncated=False)
        self.assertEqual(checked['recommendation'], 'INVESTIGATE')
        self.assertTrue(checked['needs_verification'])
        self.assertIn('Resolve obligations', checked['next_action'])
        metadata2 = {**meta, 'selected_take_prerequisite_claims': [],
                     'later_fixes_of_this_commit': [],
                     'originals_this_commit_fixes': [{'sha': self.other}]}
        self.assertEqual(validate_result(candidate, metadata2, False)['recommendation'],
                         'INVESTIGATE')

    def test_truncated_patch_downgrades_high_confidence(self):
        candidate = StubModel('ADAPT').assess({})
        meta = {'selected_take_prerequisite_claims': [],
                'later_fixes_of_this_commit': [], 'originals_this_commit_fixes': []}
        self.assertEqual(validate_result(candidate, meta, True)['confidence'], 'medium')
        self.assertTrue(validate_result(candidate, meta, True)['needs_verification'])

    def test_rejects_invalid_structured_response(self):
        meta = {'selected_take_prerequisite_claims': [],
                'later_fixes_of_this_commit': [], 'originals_this_commit_fixes': []}
        candidate = StubModel().assess({})
        with self.assertRaises(ValueError):
            validate_result({**candidate, 'recommendation': 'SKIP'}, meta, False)
        with self.assertRaises(ValueError):
            validate_result({**candidate, 'evidence': []}, meta, False)


if __name__ == '__main__':
    unittest.main()
