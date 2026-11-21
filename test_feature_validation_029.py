#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""0.1.29: avoid paid-response loss from unadvertised evidence limits."""
import copy
import json
import unittest

from feature_assessor import (feature_response_schema, validate_feature_result,
                              assess_features, feature_metadata, read_feature_cache,
                              assessment_cache_key)
from test_feature_evidence_027 import EvidenceTests, StagedModel


class BrokenEvidence(StagedModel):
    def assess_feature(self, packet, prompt, schema):
        raw = super().assess_feature(packet, prompt, schema)
        raw['evidence'][0]['sha'] = 'f' * 40
        return raw


class FeatureEvidenceValidation029(EvidenceTests):
    def test_more_than_40_schema_compliant_evidence_entries_are_accepted(self):
        model = StagedModel(self.group)
        raw = model.assess_feature({}, '', {})
        raw['evidence'] = raw['evidence'] * 41
        meta = feature_metadata(self.plan, self.group, self.report, self.icc)
        result = validate_feature_result(raw, self.group, meta, False)
        self.assertEqual(len(result['evidence']), 41)

    def test_schema_constrains_shas_at_API_boundary(self):
        schema = feature_response_schema(self.group)
        for k in ('member_roles', 'member_recommendations', 'evidence'):
            allowed = schema['properties'][k]['items']['properties']['sha']['enum']
            self.assertEqual(allowed, self.group['members'])
        self.assertEqual(schema['properties']['minimum_candidate_shas']['items']['enum'],
                         self.group['members'])

    def test_exact_bad_evidence_field_and_paid_response_preservation(self):
        faulty = BrokenEvidence(self.group)
        with self.assertRaisesRegex(ValueError, r'feature evidence\[0\]\.sha'):
            assess_features(self.plan, [copy.deepcopy(self.group)], self.report,
                            self.icc, self.repo, model='stub-029', client=faulty,
                            print_fn=lambda *x: None)
        self.assertEqual(len(faulty.calls), 1)
        cache = read_feature_cache(self.dir)['assessments'][assessment_cache_key(self.group['id'], 'stub-029')]
        self.assertEqual(cache['pending_raw']['evidence'][0]['sha'], 'f' * 40)
        self.assertNotIn('result', cache)
        # Retried with an unused client: previous paid result is revalidated,
        # but never silently accepted or paid for twice.
        unused = StagedModel(self.group)
        with self.assertRaisesRegex(ValueError, r'feature evidence\[0\]\.sha'):
            assess_features(self.plan, [copy.deepcopy(self.group)], self.report,
                            self.icc, self.repo, model='stub-029', client=unused,
                            print_fn=lambda *x: None)
        self.assertEqual(len(unused.calls), 0)
        self.assertEqual(len(unused.segments), 0)
        # Explicit re-request is the only path that can trigger another charge.
        retry = StagedModel(self.group)
        result = assess_features(self.plan, [copy.deepcopy(self.group)], self.report,
                                 self.icc, self.repo, model='stub-029', client=retry,
                                 retry_invalid=True, print_fn=lambda *x: None)
        self.assertEqual(len(retry.calls), 1)
        self.assertEqual(len(retry.segments), 0)
        self.assertEqual(result['new_assessments'], 1)
        completed = read_feature_cache(self.dir)['assessments'][assessment_cache_key(self.group['id'], 'stub-029')]
        self.assertIn('result', completed)
        self.assertNotIn('pending_raw', completed)

    def test_schema_does_not_mutate_shared_base(self):
        from feature_assessor import SCHEMA
        feature_response_schema(self.group)
        self.assertNotIn('enum', SCHEMA['properties']['evidence']['items']['properties']['sha'])


if __name__ == '__main__':
    unittest.main()
