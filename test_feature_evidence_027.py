#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""0.1.27: evidence coverage, downstream baseline, cross-feature safety."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from feature_evidence import (CHUNK_CHARS, chunk_patch, collect_evidence,
                              downstream_context, _chunk_cache_file)
from feature_assessor import (assess_features, build_features, feature_metadata,
                              read_feature_cache, validate_feature_result)
from review_queues import build_queues
from test_feature_assessor_026 import StubFeatureModel, record


class StagedModel(StubFeatureModel):
    def __init__(self, group, **opts):
        super().__init__(group, **opts)
        self.segments = []

    def assess_patch_segment(self, packet, prompt, schema):
        self.segments.append(packet)
        return {"behavior": "Changes a long kernel function body",
                "defined_symbols": ["EXPORTED_HANDLER"],
                "consumed_symbols": ["OTHER_MACRO"],
                "dependencies": "No proven dependency from this segment",
                "risks": "Check baseline behavioral equivalence",
                "unknowns": "Full patch must be combined across segments"}


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.repo = self.dir / 'kernel'
        self.repo.mkdir()
        self.git('init', '-q')
        self.git('config', 'user.name', 'Tests')
        self.git('config', 'user.email', 'tests@example.net')
        (self.repo / 'core.c').write_text('#define BASELINE_ONLY 1\nint old_handler(void) { return BASELINE_ONLY; }\n')
        self.git('add', '.'); self.git('commit', '-qm', 'baseline')
        self.baseline = self.git('rev-parse', 'HEAD')
        long_patch = ('/* long definition %06d */\n' * 1800)
        (self.repo / 'core.c').write_text('#define EXPORTED_HANDLER 42\n' +
            ''.join('/* long definition %06d */\n' % i for i in range(1800)))
        self.git('add', '.'); self.git('commit', '-qm', 'provider with long diff')
        self.provider = self.git('rev-parse', 'HEAD')
        (self.repo / 'consumer.c').write_text('int use(void) { return EXPORTED_HANDLER; }\n')
        self.git('add', '.'); self.git('commit', '-qm', 'consumer')
        self.consumer = self.git('rev-parse', 'HEAD')
        records = {
            self.provider: record('provide long diff', ['core.c']),
            self.consumer: record('use macro', ['consumer.c'],
                                  advisor_prerequisites=[{'sha': self.provider}]),
        }
        order = [self.provider, self.consumer]
        self.plan = SimpleNamespace(
            order=order, commits=records, overrides={},
            payload={'commits': records, 'order': order,
                     'replay_start': self.baseline,
                     'advisor': {'product': {'product': 'RHEL 10'}},
                     'platform_profile': {'sha256': '1' * 64}},
            baseline=self.baseline, directory=self.dir, plan_digest='2' * 64,
            effective=lambda sha: records[sha]['advisor_decision'])
        self.icc = {'replay': {'status': 'blocked', 'commits': [
            {'sha': self.provider, 'status': 'conflict', 'files': ['core.c'],
             'conditional': False},
            {'sha': self.consumer, 'status': 'clean', 'files': ['consumer.c'],
             'conditional': True},
        ]}}
        self.report = build_queues(self.plan.payload, icc=self.icc,
                                   plan_sha256=self.plan.plan_digest)
        self.group = build_features(self.plan, self.report, priorities=['p0'])[0]

    def git(self, *args):
        return subprocess.check_output(['git', *args], cwd=self.repo, text=True).strip()

    def test_chunking_is_lossless_including_oversized_line(self):
        text = 'a' * (CHUNK_CHARS + 100) + '\n' + 'z\n'
        chunks = chunk_patch(text)
        self.assertGreater(len(chunks), 1)
        self.assertEqual(''.join(chunks), text)
        self.assertTrue(all(len(x) <= CHUNK_CHARS for x in chunks))

    def test_scoped_symbol_candidates_are_not_equivalence_claims(self):
        from feature_evidence import _related_symbol_search
        search = _related_symbol_search(
            self.repo, self.baseline,
            {self.provider: '+#define BASELINE_ONLY 99\n'}, ['core.c'])
        self.assertEqual(search['status'], 'textual-candidates-only')
        self.assertTrue(any('BASELINE_ONLY' in hit['text'] for hit in search['hits']))
        self.assertEqual(search['scopes'], ['core.c'])

    def test_baseline_excerpts_are_from_baseline_not_head(self):
        from feature_evidence import full_patch
        patches = {s: full_patch(self.repo, s) for s in self.group['members']}
        context = downstream_context(self.plan, self.group, self.repo, patches, self.icc)
        sources = {x['path']: x for x in context['sources']}
        self.assertIn('core.c', sources)
        self.assertIn('BASELINE_ONLY', sources['core.c']['excerpt'])
        self.assertNotIn('EXPORTED_HANDLER', sources['core.c']['excerpt'])
        self.assertEqual(sources['core.c']['baseline_sha'], self.baseline)
        self.assertIn('consumer.c', context['missing_paths'])

    def test_complete_coverage_summary_cache_and_versioned_feature_result(self):
        model = StagedModel(self.group)
        before_status = self.git('status', '--porcelain')
        before_head = self.git('rev-parse', 'HEAD')
        first = assess_features(self.plan, [copy.deepcopy(self.group)], self.report,
                                self.icc, self.repo, model='stub127', client=model,
                                print_fn=lambda *x: None)
        self.assertEqual(first['new_assessments'], 1)
        self.assertGreater(len(model.segments), 1)
        self.assertEqual(len(model.calls), 1)
        dossier = model.calls[0]
        self.assertEqual(dossier['upstream_coverage'], 'full-inline-or-all-segments-summarized')
        big = next(p for p in dossier['patches'] if p['sha'] == self.provider)
        self.assertEqual(big['mode'], 'lossless-chunk-summary')
        self.assertEqual(len(big['segments_detail']), len(model.segments))
        self.assertEqual([p['segment'] for p in big['segments_detail']],
                         list(range(1, len(model.segments) + 1)))
        self.assertTrue(all('sha256' in p for p in big['segments_detail']))
        self.assertEqual(self.git('status', '--porcelain'), before_status)
        self.assertEqual(self.git('rev-parse', 'HEAD'), before_head)
        self.assertTrue(_chunk_cache_file(self.dir).is_file())
        self.assertEqual(read_feature_cache(self.dir)['schema'], 3)
        subsequent = StagedModel(self.group)
        second = assess_features(self.plan, [copy.deepcopy(self.group)], self.report,
                                 self.icc, self.repo, model='stub127', client=subsequent,
                                 print_fn=lambda *x: None)
        self.assertEqual(second['new_assessments'], 0)
        self.assertEqual(subsequent.calls, [])
        self.assertEqual(subsequent.segments, [])
        # Invalidate feature decision only: stage-1 segment results are reused.
        self.plan.commits[self.consumer]['advisor_rationale'] = 'Relevant new risk'
        later = StagedModel(self.group)
        third = assess_features(self.plan, [copy.deepcopy(self.group)], self.report,
                                self.icc, self.repo, model='stub127', client=later,
                                print_fn=lambda *x: None)
        self.assertEqual(third['new_assessments'], 1)
        self.assertFalse(later.segments)
        self.assertEqual(len(later.calls), 1)

    def test_outside_take_consumer_prevents_unqualified_exclusion(self):
        third = self.git('rev-parse', 'HEAD')
        ext = 'f' * 40
        self.plan.order.append(ext)
        self.plan.commits[ext] = record('outside feature consumer', ['other.c'],
            advisor_prerequisites=[{'sha': self.provider}])
        meta = feature_metadata(self.plan, self.group, self.report, self.icc)
        self.assertTrue(meta['external_selected_and_review_consumers'])
        result = StagedModel(self.group, recommendation='EXCLUDE',
                             member_actions={self.provider: 'EXCLUDE'}).assess_feature({}, '', {})
        guarded = validate_feature_result(result, self.group, meta, False)
        actions = {x['sha']: x['recommendation'] for x in guarded['member_recommendations']}
        self.assertEqual(actions[self.provider], 'INVESTIGATE')
        self.assertTrue(guarded['needs_verification'])

    def test_bounded_source_paths_and_v1_cache_migration(self):
        from feature_evidence import _path_ok
        self.assertFalse(_path_ok('../secret'))
        self.assertFalse(_path_ok('/etc/passwd'))
        self.assertFalse(_path_ok('-bad'))
        self.assertTrue(_path_ok('arch/arm64/mm/mmu.c'))
        file = self.dir / '.kernel-backport' / 'feature-assessments.json'
        file.parent.mkdir()
        file.write_text(json.dumps({'schema': 1, 'assessments': {'prior': {'result': {}}}}))
        self.assertEqual(read_feature_cache(self.dir)['schema'], 3)
        self.assertIn('prior', read_feature_cache(self.dir)['assessments'])


if __name__ == '__main__':
    unittest.main()
