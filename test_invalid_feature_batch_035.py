#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""0.1.35: keep bulk conditioning working across invalid paid model outputs."""
from __future__ import annotations

import copy
import json
import unittest

from feature_assessor import (assess_features, assessment_cache_key,
                              attach_feature_cache, feature_text,
                              read_feature_cache)
from test_feature_evidence_027 import EvidenceTests, StagedModel


class InvalidFirst(StagedModel):
    def __init__(self, group):
        super().__init__(group)
        self.responses = 0

    def assess_feature(self, packet, prompt, schema):
        raw = super().assess_feature(packet, prompt, schema)
        self.responses += 1
        if self.responses == 1:
            # Structurally valid JSON with repeated/missing member roles.
            raw['member_roles'][-1] = dict(raw['member_roles'][0])
        return raw


class InvalidBatch035(EvidenceTests):
    def two_groups(self):
        one = copy.deepcopy(self.group)
        two = copy.deepcopy(self.group)
        one['id'] = 'F-test-invalid'
        two['id'] = 'F-test-valid'
        return [one, two]

    def test_invalid_does_not_abort_bulk_valid_output(self):
        groups = self.two_groups()
        model = InvalidFirst(self.group)
        notes = []
        before = self.git('status', '--porcelain')
        stats = assess_features(self.plan, groups, self.report, self.icc,
                                self.repo, model='stub135', client=model,
                                continue_on_invalid=True, print_fn=notes.append)
        self.assertEqual(stats['new_assessments'], 1)
        self.assertEqual(stats['invalid_assessments'], 1)
        self.assertEqual(stats['available'], 1)
        self.assertEqual(model.responses, 2)
        self.assertEqual(self.git('status', '--porcelain'), before)
        entries = read_feature_cache(self.dir)['assessments']
        bad = entries[assessment_cache_key(groups[0]['id'], 'stub135')]
        good = entries[assessment_cache_key(groups[1]['id'], 'stub135')]
        self.assertIn('pending_raw', bad)
        self.assertIn('invalid feature member roles', bad['validation_error'])
        self.assertNotIn('result', bad)
        self.assertIn('result', good)
        self.assertNotIn('pending_raw', good)
        self.assertEqual(groups[0]['assessment_status'], 'invalid')
        self.assertEqual(groups[1]['assessment_status'], 'assessed')
        self.assertIn('validation error: invalid feature member roles', feature_text(groups))
        self.assertTrue(any('HELD feature as invalid' in n for n in notes))

        unused = StagedModel(self.group)
        repeated = self.two_groups()
        second = assess_features(self.plan, repeated, self.report, self.icc,
                                 self.repo, model='stub135', client=unused,
                                 continue_on_invalid=True, print_fn=lambda _: None)
        self.assertEqual(second['new_assessments'], 0)
        self.assertEqual(second['held_invalid_assessments'], 1)
        self.assertFalse(unused.calls)
        self.assertEqual(repeated[0]['assessment_status'], 'invalid')
        self.assertEqual(repeated[1]['assessment_status'], 'assessed')

        fresh = StagedModel(self.group)
        retried = self.two_groups()
        third = assess_features(self.plan, retried, self.report, self.icc,
                                self.repo, model='stub135', client=fresh,
                                continue_on_invalid=True, retry_invalid=True,
                                print_fn=lambda _: None)
        self.assertEqual(third['new_assessments'], 1)
        self.assertEqual(third['invalid_assessments'], 0)
        self.assertEqual(len(fresh.calls), 1)
        self.assertTrue(all(g['assessment_status'] == 'assessed' for g in retried))

    def test_interactive_review_still_fails_closed(self):
        groups = self.two_groups()
        bad = InvalidFirst(self.group)
        with self.assertRaisesRegex(ValueError, 'invalid feature member roles'):
            assess_features(self.plan, groups, self.report, self.icc,
                            self.repo, model='stub135-strict', client=bad,
                            print_fn=lambda _: None)
        self.assertEqual(bad.responses, 1)
        entry = read_feature_cache(self.dir)['assessments'][
            assessment_cache_key(groups[0]['id'], 'stub135-strict')]
        self.assertIn('pending_raw', entry)
        self.assertNotIn('result', entry)


if __name__ == '__main__':
    unittest.main()
