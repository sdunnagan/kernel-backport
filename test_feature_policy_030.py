#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""0.1.30: skeptical feature decisions, independent models and cache isolation."""
from __future__ import annotations

import copy
import json
import subprocess
import unittest
from pathlib import Path

from feature_assessor import (
    POLICY_VERSION, PROMPT, DEFAULT_FEATURE_MODEL,
    resolve_feature_models, assessment_cache_key, attach_feature_cache,
    assess_features, read_feature_cache, validate_feature_result, feature_metadata,
    feature_text,
)
from test_feature_evidence_027 import EvidenceTests, StagedModel


class RoutingAndPolicyTests(EvidenceTests):
    def test_default_and_overrides(self):
        self.assertEqual(DEFAULT_FEATURE_MODEL, 'gpt-6.1-sol')
        self.assertEqual(resolve_feature_models('gpt-6.1-terra', environ={}),
                         ('gpt-6.1-sol', 'gpt-6.1-terra'))
        self.assertEqual(resolve_feature_models('gpt-6.1-terra',
                         feature_model='alternate-final', summary_model='alternate-summary',
                         environ={}), ('alternate-final', 'alternate-summary'))
        self.assertEqual(resolve_feature_models('gpt-6.1-terra',
                         legacy_assess_model='legacy-final', environ={}),
                         ('legacy-final', 'gpt-6.1-terra'))
        self.assertEqual(resolve_feature_models('gpt-6.1-terra',
                         environ={'KERNEL_BACKPORT_FEATURE_MODEL': 'environment-final',
                                  'KERNEL_BACKPORT_FEATURE_SUMMARY_MODEL': 'environment-summary'}),
                         ('environment-final', 'environment-summary'))
        self.assertEqual(resolve_feature_models('gpt-6.1-terra',
                         feature_model='explicit', environ={
                             'KERNEL_BACKPORT_FEATURE_MODEL':'environment'}),
                         ('explicit', 'gpt-6.1-terra'))

    def test_skeptical_primary_selection_in_schema_and_prompt(self):
        from feature_assessor import SCHEMA, feature_response_schema
        self.assertEqual(SCHEMA['properties']['selection_decision']['enum'],
                         ['INCLUDE', 'EXCLUDE', 'INVESTIGATE'])
        self.assertIn('inclusion_basis', SCHEMA['required'])
        self.assertIn('exclusion_impact', SCHEMA['required'])
        self.assertEqual(feature_response_schema(self.group)['properties']
                         ['evidence']['items']['properties']['sha']['enum'],
                         self.group['members'])
        for term in ('EXCLUDE', 'Omitted-fix:', 'not to explain',
                     'independent', 'Architecture/path matches', 'SELECTION FIRST'):
            self.assertIn(term, PROMPT)
        self.assertTrue(POLICY_VERSION.startswith('skeptical'))

    def test_separate_terras_summaries_and_sols_final_decisions(self):
        final = StagedModel(self.group)
        summaries = StagedModel(self.group)
        messages = []
        initial_head = self.git('rev-parse', 'HEAD')
        stats = assess_features(self.plan, [copy.deepcopy(self.group)], self.report,
                                self.icc, self.repo, model='gpt-6.1-sol',
                                segment_model='gpt-6.1-terra',
                                client=final, segment_client=summaries,
                                print_fn=messages.append)
        self.assertEqual(stats['new_assessments'], 1)
        self.assertEqual(len(final.calls), 1)
        self.assertFalse(final.segments)
        self.assertGreater(len(summaries.segments), 0)
        self.assertFalse(summaries.calls)
        self.assertEqual(self.git('rev-parse', 'HEAD'), initial_head)
        self.assertNotIn('(paid)', '\n'.join(messages))
        cache = read_feature_cache(self.dir)['assessments']
        sol_key = assessment_cache_key(self.group['id'], 'gpt-6.1-sol', 'gpt-6.1-terra')
        self.assertIn(sol_key, cache)
        self.assertEqual(cache[sol_key]['summary_model'], 'gpt-6.1-terra')
        self.assertEqual(cache[sol_key]['policy_version'], POLICY_VERSION)
        view = [copy.deepcopy(self.group)]
        attach_feature_cache(self.plan, view, self.report, self.icc,
                             model='gpt-6.1-sol', summary_model='gpt-6.1-terra')
        self.assertEqual(view[0]['assessment_status'], 'assessed')
        self.assertIn('reviewer: gpt-6.1-sol; patch summaries: gpt-6.1-terra',
                      feature_text(view))
        # Swapping the final reviewer triggers a NEW decision, not new summaries.
        other_final = StagedModel(self.group)
        new_summaries = StagedModel(self.group)
        new = assess_features(self.plan, [copy.deepcopy(self.group)], self.report,
                              self.icc, self.repo, model='other-final',
                              segment_model='gpt-6.1-terra', client=other_final,
                              segment_client=new_summaries, print_fn=lambda _: None)
        self.assertEqual(new['new_assessments'], 1)
        self.assertEqual(len(other_final.calls), 1)
        self.assertFalse(new_summaries.segments)
        cache = read_feature_cache(self.dir)['assessments']
        self.assertIn(sol_key, cache)
        self.assertIn(assessment_cache_key(self.group['id'], 'other-final', 'gpt-6.1-terra'), cache)
        final_again = StagedModel(self.group)
        again = assess_features(self.plan, [copy.deepcopy(self.group)], self.report,
                                self.icc, self.repo, model='gpt-6.1-sol',
                                segment_model='gpt-6.1-terra', client=final_again,
                                print_fn=lambda _: None)
        self.assertEqual(again['new_assessments'], 0)
        self.assertFalse(final_again.calls)

    def test_old_terras_cache_kept_for_audit_and_marked_stale(self):
        old_file = self.dir / '.kernel-backport' / 'feature-assessments.json'
        old_file.parent.mkdir(exist_ok=True)
        old_entry = {'model': 'gpt-6.1-terra', 'evidence_sha256': 'legacy',
                     'result': {'legacy': True}}
        old_file.write_text(json.dumps({'schema': 2,
                                        'assessments': {self.group['id']: old_entry}}))
        display = [copy.deepcopy(self.group)]
        attach_feature_cache(self.plan, display, self.report, self.icc,
                             model='gpt-6.1-sol')
        self.assertEqual(display[0]['assessment_status'], 'stale')
        self.assertIsNone(display[0]['assessment'])
        result = assess_features(self.plan, [copy.deepcopy(self.group)], self.report,
                                 self.icc, self.repo, model='gpt-6.1-sol',
                                 client=StagedModel(self.group),
                                 print_fn=lambda _: None)
        self.assertEqual(result['new_assessments'], 1)
        new_cache = read_feature_cache(self.dir)['assessments']
        self.assertEqual(new_cache[self.group['id']], old_entry)
        self.assertIn(assessment_cache_key(self.group['id'], 'gpt-6.1-sol'), new_cache)

    def test_independent_fix_survives_optional_primary_exclusion(self):
        # Mark first member as an applicable fix for existing downstream code;
        # the optional capability may still be excluded while that fix stays.
        self.plan.commits[self.group['members'][0]]['fixes'] = ['f' * 40]
        raw = StagedModel(self.group, recommendation='EXCLUDE',
                          member_actions={s: 'EXCLUDE' for s in self.group['members']}) \
                          .assess_feature({}, '', {})
        raw['member_roles'][0]['role'] = 'FIX'
        meta = feature_metadata(self.plan, self.group, self.report, self.icc)
        result = validate_feature_result(raw, self.group, meta, False)
        self.assertEqual(result['selection_decision'], 'EXCLUDE')
        self.assertEqual(result['recommendation'], 'INVESTIGATE')
        self.assertEqual(result['member_recommendations'][0]['recommendation'],
                         'INVESTIGATE')

    def test_summary_model_change_invalidates_final_cache(self):
        final = StagedModel(self.group)
        first = assess_features(self.plan, [copy.deepcopy(self.group)], self.report,
                                self.icc, self.repo, model='gpt-6.1-sol',
                                segment_model='terra-1', client=final,
                                segment_client=StagedModel(self.group),
                                print_fn=lambda _: None)
        self.assertEqual(first['new_assessments'], 1)
        second_final = StagedModel(self.group)
        other_summary = StagedModel(self.group)
        second = assess_features(self.plan, [copy.deepcopy(self.group)], self.report,
                                 self.icc, self.repo, model='gpt-6.1-sol',
                                 segment_model='terra-2', client=second_final,
                                 segment_client=other_summary,
                                 print_fn=lambda _: None)
        self.assertEqual(second['new_assessments'], 1)
        self.assertGreater(len(other_summary.segments), 0)
        cache = read_feature_cache(self.dir)['assessments']
        self.assertIn(assessment_cache_key(self.group['id'], 'gpt-6.1-sol', 'terra-1'), cache)
        self.assertIn(assessment_cache_key(self.group['id'], 'gpt-6.1-sol', 'terra-2'), cache)

    def test_optional_exclusion_not_forced_to_adapt(self):
        raw = StagedModel(self.group, recommendation='EXCLUDE',
                          member_actions={s: 'EXCLUDE' for s in self.group['members']}) \
                          .assess_feature({}, '', {})
        meta = feature_metadata(self.plan, self.group, self.report, self.icc)
        # This fixture has an internal dependency, but if all members are
        # excluded together the dependency imposes no remaining consumer.
        result = validate_feature_result(raw, self.group, meta, False)
        self.assertEqual(result['selection_decision'], 'EXCLUDE')
        self.assertEqual(result['recommendation'], 'EXCLUDE')
        self.assertEqual(len(result['member_recommendations']), 2)


if __name__ == '__main__':
    unittest.main()
