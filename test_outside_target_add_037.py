#!/usr/bin/env python3
"""0.1.37 scoped post-target Fixes additions and old-plan preservation."""
import json
import unittest
from pathlib import Path

import test_kernel_backport as _legacy
from test_kernel_backport import git


class OutsideTargetAddTests(unittest.TestCase):
    setUp = _legacy.UnifiedFixture.setUp
    make_commit = _legacy.UnifiedFixture.make_commit
    write_plan = _legacy.UnifiedFixture.write_plan
    runcli = _legacy.UnifiedFixture.runcli
    check = _legacy.UnifiedFixture.check
    decide = _legacy.UnifiedFixture.decide
    execute = _legacy.UnifiedFixture.execute
    state = _legacy.UnifiedFixture.state
    def make_later_fix(self):
        # A real Git Fixes trailer, pointing at an existing planned original.
        with open(self.up / 'post-target', 'w', encoding='utf-8') as f:
            f.write('corrected implementation\n')
        git(self.up, 'add', 'post-target')
        git(self.up, 'commit', '-m', 'fix a retained commit',
            '-m', f'Fixes: {self.a[:12]} ("first change")')
        fix = git(self.up, 'rev-parse', 'HEAD')
        git(self.work, 'fetch', '-q', str(self.up), 'main')
        return fix

    def test_existing_plan_and_decisions_remain_untouched_until_explicit_add(self):
        self.assertEqual(self.decide(self.b, 'SKIP', 'existing engineering review').returncode, 0)
        worksheet = self.directory / 'backport_worksheet.txt'
        planner = self.directory / 'backport_plan.json'
        before_ws, before_plan = worksheet.read_bytes(), planner.read_bytes()
        result = self.check('--no-replay')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(before_plan, planner.read_bytes())
        self.assertIn('engineer-rationale: existing engineering review', worksheet.read_text())
        self.assertFalse((self.directory / '.kernel-backport/additions.json').exists())
        self.assertIn('TAKE: 2', result.stdout)
        # The pre-add ICC digest must not change just from loading 0.1.37.
        before_icc = json.loads((self.directory / '.kernel-backport/icc.json').read_text())
        self.assertEqual(self.check('--no-replay').returncode, 0)
        after_icc = json.loads((self.directory / '.kernel-backport/icc.json').read_text())
        self.assertEqual(before_icc['decision_revision'], after_icc['decision_revision'])
        self.assertEqual(before_ws.split(b'SAME PATCH SERIES')[1],
                         worksheet.read_bytes().split(b'SAME PATCH SERIES')[1])

    def test_post_target_requires_explicit_override_without_modifying_plan(self):
        fix = self.make_later_fix()
        original = (self.directory / 'backport_worksheet.txt').read_bytes()
        rejected = self.runcli('add', fix, '--reason', 'post-target stable Fixes commit',
                               '--fixes', self.a[:12])
        self.assertNotEqual(rejected.returncode, 0)
        self.assertIn('--allow-outside-target', rejected.stderr)
        self.assertEqual(original, (self.directory / 'backport_worksheet.txt').read_bytes())
        self.assertFalse((self.directory / '.kernel-backport/additions.json').exists())

    def test_outside_target_fix_tracks_verified_provenance_and_applies_normally(self):
        fix = self.make_later_fix()
        original = (self.directory / 'backport_plan.json').read_bytes()
        add = self.runcli('add', fix, '--reason', 'post-target correction for selected original',
                          '--allow-outside-target', '--fixes', self.a[:12])
        self.assertEqual(add.returncode, 0, add.stderr + add.stdout)
        self.assertEqual((self.directory / 'backport_plan.json').read_bytes(), original)
        overlay = json.loads((self.directory / '.kernel-backport/additions.json').read_text())
        entry = overlay['commits'][0]
        self.assertEqual(entry['sha'], fix)
        self.assertEqual(entry['verified_fixes'], [self.a])
        self.assertTrue(entry['outside_target'])
        self.assertEqual(entry['record']['fixes'], [self.a])
        ws = (self.directory / 'backport_worksheet.txt').read_text()
        self.assertIn(f'engineer-verified-fixes: {self.a[:12]}', ws)
        self.assertIn('engineer-target-exception:', ws)
        result = self.check('--no-replay')
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertIn('TAKE: 4', result.stdout)
        self.assertIn('Explicit outside-target commits: 1', result.stdout)
        icc = json.loads((self.directory / '.kernel-backport/icc.json').read_text())
        self.assertEqual(icc['logical_blockers'], [])
        self.assertEqual(icc['warnings'], [])
        self.assertEqual(icc['engineer_target_exceptions'][0]['sha'], fix)
        self.assertGreater(icc['pending_order'].index(fix), icc['pending_order'].index(self.a))
        self.assertEqual(self.check().returncode, 0)  # Full exploratory replay.
        completed = self.execute()
        self.assertEqual(completed.returncode, 0, completed.stderr + completed.stdout)
        self.assertTrue((self.work / 'post-target').exists())

    def test_false_fixes_provenance_rejected_fail_closed(self):
        fix = self.make_later_fix()
        error = self.runcli('add', fix, '--reason', 'incorrect provenance',
                            '--allow-outside-target', '--fixes', self.b[:12])
        self.assertNotEqual(error.returncode, 0)
        self.assertIn('has no Fixes: trailer', error.stderr)
        self.assertFalse((self.directory / '.kernel-backport/additions.json').exists())

    def test_verified_fixes_does_not_create_implementation_dependency(self):
        fix = self.make_later_fix()
        self.assertEqual(self.decide(self.a, 'SKIP', 'optional parent skipped').returncode, 0)
        add = self.runcli('add', fix, '--reason', 'independent retained fix',
                          '--allow-outside-target', '--fixes', self.a[:12])
        self.assertEqual(add.returncode, 0, add.stderr + add.stdout)
        check = self.check('--no-replay')
        self.assertEqual(check.returncode, 0, check.stderr + check.stdout)
        icc = json.loads((self.directory / '.kernel-backport/icc.json').read_text())
        self.assertFalse(any(e.get('prerequisite') == self.a for e in icc['logical_blockers']))
        self.assertNotIn({'consumer': fix, 'prerequisite': self.a},
                         json.loads((self.directory / '.kernel-backport/additions.json').read_text())['relationships'])

    def test_identical_readd_is_noop_and_preserves_icc(self):
        fix = self.make_later_fix()
        args = ('add', fix, '--reason', 'reviewed later fix', '--allow-outside-target',
                '--fixes', self.a[:12])
        self.assertEqual(self.runcli(*args).returncode, 0)
        self.assertEqual(self.check('--no-replay').returncode, 0)
        ws = self.directory / 'backport_worksheet.txt'
        overlay = self.directory / '.kernel-backport/additions.json'
        icc = self.directory / '.kernel-backport/icc.json'
        before = (ws.read_bytes(), overlay.read_bytes(), icc.read_bytes())
        repeat = self.runcli(*args)
        self.assertEqual(repeat.returncode, 0, repeat.stderr + repeat.stdout)
        self.assertIn('unchanged', repeat.stdout)
        self.assertEqual(before, (ws.read_bytes(), overlay.read_bytes(), icc.read_bytes()))
        changed = self.runcli('add', fix, '--reason', 'a different reason',
                              '--allow-outside-target', '--fixes', self.a[:12])
        self.assertNotEqual(changed.returncode, 0)
        self.assertIn('already engineer-added', changed.stderr)
        self.assertEqual(before, (ws.read_bytes(), overlay.read_bytes(), icc.read_bytes()))

    def test_old_additions_overlay_remains_readable(self):
        fix = self.make_later_fix()
        self.assertEqual(self.runcli('add', fix, '--reason', 'post-target legacy record',
                                     '--allow-outside-target').returncode, 0)
        additions_path = self.directory / '.kernel-backport/additions.json'
        overlay = json.loads(additions_path.read_text())
        overlay['commits'][0].pop('outside_target')
        overlay['commits'][0].pop('verified_fixes')
        additions_path.write_text(json.dumps(overlay))
        result = self.check('--no-replay')
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertIn('warnings: 1', result.stdout)
        self.assertNotIn('Explicit outside-target commits:', result.stdout)


if __name__ == '__main__':
    unittest.main()
