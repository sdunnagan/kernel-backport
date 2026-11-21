#!/usr/bin/env python3
"""0.1.23: triage is evidence-based and orthogonal to review/decisions."""
import copy
import json
from pathlib import Path
import tempfile
import unittest

from review_queues import build_queues, render_text, write_reports


def record(subject, paths, decision='REVIEW', **extra):
    return {
        'subject': subject,
        'files': paths,
        'advisor_decision': decision,
        'advisor_prerequisites': [],
        'fixes': [],
        'match': {'status': 'missing'},
        'platform_relevance': {'status': 'SHARED_OR_UNKNOWN', 'matches': []},
        **extra,
    }


class TriageTests(unittest.TestCase):
    def setUp(self):
        self.take = 'a' * 40
        self.later = 'b' * 40
        self.conflict = 'c' * 40
        self.style = 'd' * 40
        self.board = 'e' * 40
        self.unknown = 'f' * 40
        self.records = {
            self.take: record('ARM64 core change', ['arch/arm64/mm/mmu.c'], 'TAKE'),
            self.later: record('Later bug correction', ['arch/arm64/mm/mmu.c'],
                               fixes=[self.take]),
            self.conflict: record('Important architecture change',
                                  ['arch/arm64/kernel/entry.S'], 'TAKE'),
            self.style: record('arm64: dts: qcom: Use lowercase hex',
                               ['arch/arm64/boot/dts/qcom/sm8750.dtsi'], 'TAKE'),
            self.board: record('Kaanapali board-only device tree',
                               ['arch/arm64/boot/dts/qcom/kaanapali.dtsi'],
                               platform_relevance={'status': 'UNMATCHED_BOARD',
                                                   'matches': []}),
            self.unknown: record('Shared code change', ['mm/page_alloc.c']),
        }
        self.payload = {'commits': self.records, 'order': list(self.records)}
        self.icc = {
            'logical_blockers': [],
            'replay': {
                'status': 'blocked',
                'commits': [
                    {'sha': self.conflict, 'status': 'conflict',
                     'files': ['arch/arm64/kernel/entry.S'], 'conditional': True},
                    {'sha': self.style, 'status': 'conflict',
                     'files': ['arch/arm64/boot/dts/qcom/sm8750.dtsi'],
                     'conditional': True},
                ],
            },
        }

    def test_required_is_an_obligation_not_an_automatic_take(self):
        before = copy.deepcopy(self.payload)
        result = build_queues(self.payload, icc=self.icc)
        required = next(r for r in result['queues']['p0'] if r['sha'] == self.later)
        self.assertEqual(required['triage'], 'REQUIRED')
        self.assertEqual(required['decision'], 'REVIEW')
        self.assertIn('does not by itself prove', required['triage_reason'])
        self.assertEqual(before, self.payload)

    def test_integration_optional_and_unknown_do_not_change_decisions(self):
        data = build_queues(self.payload, icc=self.icc)
        rows = {r['sha']: r for p in data['queues'].values() for r in p}
        self.assertEqual(rows[self.conflict]['triage'], 'INTEGRATION')
        self.assertEqual(rows[self.style]['triage'], 'OPTIONAL')
        self.assertEqual(rows[self.style]['decision'], 'TAKE')
        self.assertEqual(rows[self.board]['triage'], 'OPTIONAL')
        self.assertEqual(rows[self.unknown]['triage'], 'UNCLASSIFIED')
        self.assertEqual(data['triage_summary']['p0']['REQUIRED'], 1)
        self.assertEqual(data['triage_summary']['p0']['INTEGRATION'], 1)
        self.assertEqual(data['triage_summary']['p0']['OPTIONAL'], 1)
        self.assertEqual(data['schema'], 4)

    def test_style_with_selected_prerequisite_consumer_is_not_optional(self):
        self.records[self.take]['advisor_prerequisites'] = [{'sha': self.style}]
        data = build_queues(self.payload, icc=self.icc)
        optional = next(r for r in data['queues']['p0'] if r['sha'] == self.style)
        self.assertEqual(optional['triage'], 'INTEGRATION')

    def test_related_files_are_not_inferred_as_hard_dependencies(self):
        self.records[self.style]['files'] = ['arch/arm64/kernel/entry.S']
        data = build_queues(self.payload, icc=self.icc)
        item = next(r for r in data['queues']['p0'] if r['sha'] == self.conflict)
        self.assertIn(self.style[:12], item['related'])
        self.assertIn('not proven', item['related_basis'])
        self.assertFalse(any(i['kind'] == 'TAKE_PREREQUISITE' and
                             i['sha'] == self.style for i in data['issues']))

    def test_human_reports_wrap_at_80_json_remains_valid(self):
        self.icc['replay']['commits'][0]['diagnosis'] = {
            'classification': 'unverified-integration-conflict',
            'suggested_action': ('Compare the upstream kernel patch against the '
                                 'downstream implementation and inspect actual '
                                 'prerequisites rather than guessing based '
                                 'on a conflict alone. ') * 4,
        }
        data = build_queues(self.payload, icc=self.icc)
        with tempfile.TemporaryDirectory() as temp:
            write_reports(Path(temp), data)
            for priority in ('p0', 'p1', 'p2', 'p3'):
                text = (Path(temp) / f'review-{priority}.txt').read_text()
                self.assertTrue(all(len(line) <= 80 for line in text.splitlines()))
                self.assertEqual(text, render_text(data, priority))
            loaded = json.loads((Path(temp) / 'review-queues.json').read_text())
            self.assertEqual(loaded['triage_summary'], data['triage_summary'])
            self.assertEqual(loaded['queues']['p0'][0]['triage'], 'REQUIRED')


if __name__ == '__main__':
    unittest.main()
