#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""0.1.28 regression: don't reject schema-valid paid segment responses."""
from __future__ import annotations

import tempfile
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from feature_evidence import (
    MAX_SEGMENT_SUMMARY_CHARS, _summary_validation_error, _summary_valid,
    _read_chunks, collect_evidence,
)


def model_summary(*, behavior_chars=2800):
    return {
        'behavior': 'A' * behavior_chars,
        'defined_symbols': ['sym_' + str(i) for i in range(40)] + ['x' * 170],
        'consumed_symbols': ['SECONDARY'],
        'dependencies': 'No proof of hard dependency in this segment',
        'risks': 'Validate behavioral equivalence',
        'unknowns': 'Segment summary is derived evidence, not a complete audit',
    }


class Model:
    def __init__(self, summary):
        self.summary = summary
        self.calls = 0

    def assess_patch_segment(self, packet, prompt, schema):
        self.calls += 1
        return self.summary


class SummaryValidationTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.directory = Path(tmp.name)
        self.sha = 'a' * 40
        self.group = {'members': [self.sha]}
        self.plan = SimpleNamespace(directory=self.directory)
        # Deliberately larger than the inline maximum: one paid segment call.
        self.patch_text = 'diff --git a/test.c b/test.c\n' + '+/* PATCH BODY */\n' * 750

    def collect(self, client):
        with (patch('feature_evidence.full_patch', return_value=self.patch_text),
              patch('feature_evidence.downstream_context',
                    return_value={'status': 'no-readable-matches', 'sources': []})):
            return collect_evidence(self.plan, self.group, self.directory, {},
                                    model='fake-terra', client=client,
                                    print_fn=lambda message: None)

    def test_verbose_schema_valid_summary_is_accepted_cached_and_reused(self):
        summary = model_summary()
        self.assertIsNone(_summary_validation_error(summary))
        self.assertTrue(_summary_valid(summary))
        model = Model(summary)
        first = self.collect(model)
        self.assertGreater(model.calls, 0)
        self.assertEqual(first['upstream_coverage'], 'full-inline-or-all-segments-summarized')
        chunks = _read_chunks(self.directory)['chunks']
        self.assertEqual(len(chunks), model.calls)
        self.assertTrue(all(x['behavior'] == summary['behavior'] for x in chunks.values()))
        again = Model({'bad': 'unexpected'})
        second = self.collect(again)
        self.assertEqual(again.calls, 0)
        self.assertEqual(second['patches'], first['patches'])

    def test_wrong_field_type_gives_actionable_diagnostic_and_is_not_cached(self):
        summary = model_summary()
        summary['consumed_symbols'] = 'a string instead of an array'
        self.assertIn('consumed_symbols must be an array',
                      _summary_validation_error(summary))
        with self.assertRaisesRegex(ValueError,
                                    'invalid upstream segment summary for a{12} '
                                    'segment 1/.*consumed_symbols must be an array'):
            self.collect(Model(summary))
        self.assertEqual(_read_chunks(self.directory)['chunks'], {})

    def test_missing_field_gives_actionable_diagnostic(self):
        summary = model_summary()
        del summary['unknowns']
        self.assertIn("fields missing=['unknowns']", _summary_validation_error(summary))

    def test_oversized_summary_is_rejected_without_silent_truncation(self):
        summary = model_summary(behavior_chars=MAX_SEGMENT_SUMMARY_CHARS + 1)
        self.assertIn('needs a shorter summary', _summary_validation_error(summary))
        with self.assertRaisesRegex(ValueError, 'needs a shorter summary'):
            self.collect(Model(summary))
        self.assertEqual(_read_chunks(self.directory)['chunks'], {})


if __name__ == '__main__':
    unittest.main()
