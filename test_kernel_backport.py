#!/usr/bin/env python3
"""Consolidated workflow tests, run entirely with local disposable Git repos."""
import json
import os
from unittest.mock import patch
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

CLI = Path(__file__).with_name('kernel-backport')


def cmd(cwd, *args, check=True):
    cp = subprocess.run(list(args), cwd=cwd, capture_output=True, text=True)
    if check and cp.returncode:
        raise AssertionError(f"command {args} failed ({cp.returncode}):\n{cp.stdout}\n{cp.stderr}")
    return cp


def git(repo, *args):
    return cmd(repo, 'git', *args).stdout.strip()


class UnifiedFixture(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.up = self.root / 'up'
        self.work = self.root / 'work'
        self.up.mkdir()
        git(self.up, 'init', '-b', 'main')
        git(self.up, 'config', 'user.name', 'Test User')
        git(self.up, 'config', 'user.email', 'test@example.com')
        (self.up / 'base').write_text('baseline\n')
        git(self.up, 'add', '.')
        git(self.up, 'commit', '-m', 'base')
        self.base = git(self.up, 'rev-parse', 'HEAD')
        self.a = self.make_commit('a', 'first change')
        self.b = self.make_commit('b', 'second change')
        self.c = self.make_commit('c', 'third change')
        self.target = self.c
        git(self.root, 'clone', '-q', str(self.up), str(self.work))
        git(self.work, 'switch', '-c', 'backport-work', self.base)
        git(self.work, 'config', 'user.name', 'Backport Tester')
        git(self.work, 'config', 'user.email', 'backport@example.com')
        self.directory = self.root / 'backport_plan_08'
        self.directory.mkdir()
        self.write_plan()

    def make_commit(self, filename, title):
        (self.up / filename).write_text(title + '\n')
        git(self.up, 'add', filename)
        git(self.up, 'commit', '-m', title)
        return git(self.up, 'rev-parse', 'HEAD')

    def write_plan(self, decisions=None, prerequisites=None):
        decisions = decisions or {}
        prerequisites = prerequisites or {}
        commits = {}
        lines = ['BACKPORT WORKSHEET (oldest first)', '=' * 80]
        for s, title in [(self.a, 'first'), (self.b, 'second'), (self.c, 'third')]:
            decision = decisions.get(s, 'TAKE')
            commits[s] = {'selected': True, 'match': {'status': 'missing'},
                          'advisor_decision': decision,
                          'advisor_prerequisites': [{'sha': p} for p in prerequisites.get(s, [])],
                          'subject': title}
            lines.extend([f'{s[:12]} [DIRECT] ("{title}")',
                          f'      advisor: {decision}', '      advisor-rationale: test evidence'])
        lines.extend(['', 'SAME PATCH SERIES', '=' * 80, 'appendix unmodified'])
        payload = {'downstream': self.base, 'replay_start': self.base, 'target': self.target,
                   'order': [self.a, self.b, self.c], 'commits': commits}
        (self.directory / 'backport_plan.json').write_text(json.dumps(payload))
        (self.directory / 'backport_worksheet.txt').write_text('\n'.join(lines) + '\n')

    def runcli(self, *args):
        return cmd(self.root, sys.executable, str(CLI), *args,
                   '--results-dir', str(self.directory), '--repo', str(self.work), check=False)

    def check(self, *args):
        return self.runcli('check', *args)

    def decide(self, source, decision, reason=None):
        args = ['decide', source[:12], decision]
        if reason:
            args += ['--reason', reason]
        return self.runcli(*args)

    def execute(self, command='apply', *extra, project_id='rhel-223619_2'):
        return self.runcli(command, '--project-id', project_id, '--skip-refresh',
                           '--reference-root', str(self.root / 'refs'),
                           '--upstream-url', str(self.up),
                           '--downstream-url', str(self.up), '--max-attempts', '10', *extra)

    def state(self):
        return json.loads((self.directory / '.kernel-backport' / 'state.json').read_text())

    def test_initial_check_read_only(self):
        before = git(self.work, 'rev-parse', 'HEAD')
        result = self.check()
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertIn('Exploratory replay: clean', result.stdout)
        self.assertIn('ICC revision:', result.stdout)
        self.assertIn('ICC report:', result.stdout)
        self.assertTrue((self.directory / '.kernel-backport' / 'icc.json').is_file())
        self.assertFalse((self.directory / '.kernel-backport' / 'ics.json').exists())
        self.assertEqual(before, git(self.work, 'rev-parse', 'HEAD'))
        self.assertEqual(git(self.work, 'status', '--porcelain'), '')

    def test_check_replaces_stale_integration_header_only(self):
        worksheet = self.directory / 'backport_worksheet.txt'
        original = worksheet.read_text()
        stale = ('take-integration: BLOCKED (TAKE=118, clean=6, conflict=1, '
                 'not-tested=111, blockers=1)\n'
                 '  integration-blocker: TAKE_REPLAY_CONFLICT 1c402295c108\n')
        worksheet.write_text(stale + original)
        plan_before = (self.directory / 'backport_plan.json').read_bytes()
        block_before = worksheet.read_text().split('BACKPORT WORKSHEET (oldest first)', 1)[1]
        result = self.check()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        content = worksheet.read_text()
        self.assertIn('take-integration: READY (TAKE=3, already-applied=0, clean=3)', content)
        self.assertIn('take-integration-counts: conflict=0, empty=0, not-tested=0', content)
        self.assertIn('take-integration-counts: conditional=0, blockers=0', content)
        self.assertNotIn('1c402295c108', content)
        self.assertEqual(content.split('BACKPORT WORKSHEET (oldest first)', 1)[1], block_before)
        self.assertEqual((self.directory / 'backport_plan.json').read_bytes(), plan_before)
        # Header refresh does not change the engineer's decision revision.
        icc = json.loads((self.directory / '.kernel-backport' / 'icc.json').read_text())
        self.assertEqual(icc['decision_revision'], self._decision_revision())
        first = worksheet.read_bytes()
        self.assertEqual(self.check().returncode, 0)
        self.assertEqual(worksheet.read_bytes(), first)  # stable, idempotent summary

    def _decision_revision(self):
        # Inspect the revision using the CLI, independently of worksheet header text.
        outcome = self.runcli('status')
        self.assertEqual(outcome.returncode, 0, outcome.stdout + outcome.stderr)
        import re
        m = re.search(r'Decision revision: ([0-9a-f]{12})', outcome.stdout)
        self.assertIsNotNone(m)
        icc = json.loads((self.directory / '.kernel-backport' / 'icc.json').read_text())
        self.assertEqual(m.group(1), icc['decision_revision'][:12])
        return icc['decision_revision']

    def test_check_no_replay_clears_old_replay_conflict(self):
        self.assertEqual(self.check().returncode, 0)
        self.assertEqual(self.decide(self.b, 'SKIP', 'deferred in this run').returncode, 0)
        worksheet = self.directory / 'backport_worksheet.txt'
        block_before = worksheet.read_text().split('BACKPORT WORKSHEET (oldest first)', 1)[1]
        self.assertEqual(self.check('--no-replay').returncode, 0)
        content = worksheet.read_text()
        self.assertIn('take-integration: NOT-RUN (TAKE=2, already-applied=0, clean=0)', content)
        self.assertIn('take-integration-counts: conflict=0, empty=0, not-tested=2', content)
        self.assertIn('take-integration-counts: conditional=0, blockers=0', content)
        self.assertIn('Replay not run; pending commits are untested', content)
        self.assertIn('engineer-rationale: deferred in this run', content)
        self.assertEqual(content.split('BACKPORT WORKSHEET (oldest first)', 1)[1], block_before)
        self.assertEqual(self.check().returncode, 0)
        self.assertIn('take-integration: READY (TAKE=2, already-applied=0, clean=2)', worksheet.read_text())
        self.assertNotIn('take-integration-note: Replay not run', worksheet.read_text())

    def _set_downstream_base(self, *, a=None, c=None):
        """Create downstream-only versions of A/C and rebase the fixture plan."""
        for name, value in (("a", a), ("c", c)):
            if value is not None:
                (self.work / name).write_text(value + "\n")
                git(self.work, 'add', name)
        git(self.work, 'commit', '-m', 'downstream divergent changes')
        updated = git(self.work, 'rev-parse', 'HEAD')
        path = self.directory / 'backport_plan.json'
        payload = json.loads(path.read_text())
        payload.update(downstream=updated, replay_start=updated)
        path.write_text(json.dumps(payload))
        return updated

    def test_check_conflict_continues_through_independent_clean_commits(self):
        baseline = self._set_downstream_base(a='another implementation')
        result = self.check()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        icc = json.loads((self.directory / '.kernel-backport' / 'icc.json').read_text())
        self.assertEqual(icc['replay']['status'], 'blocked')
        self.assertTrue(icc['replay']['scan_complete'])
        self.assertFalse(icc['replay']['complete'])
        self.assertEqual([x['status'] for x in icc['replay']['commits']],
                         ['conflict', 'clean', 'clean'])
        self.assertEqual([x['conditional'] for x in icc['replay']['commits']],
                         [False, True, True])
        content = (self.directory / 'backport_worksheet.txt').read_text()
        self.assertIn('take-integration: BLOCKED (TAKE=3, already-applied=0, clean=2)', content)
        self.assertIn('take-integration-counts: conflict=1, empty=0, not-tested=0', content)
        self.assertIn('take-integration-counts: conditional=2, blockers=1', content)
        self.assertIn('  integration-blocker: TAKE_REPLAY_CONFLICT ' + self.a[:12], content)
        self.assertEqual(git(self.work, 'status', '--porcelain'), '')
        self.assertEqual(git(self.work, 'rev-parse', 'HEAD'), baseline)

    def test_check_detects_two_conflicts_in_one_replay(self):
        baseline = self._set_downstream_base(a='other a', c='other c')
        outcome = self.check()
        self.assertEqual(outcome.returncode, 0, outcome.stdout + outcome.stderr)
        icc = json.loads((self.directory / '.kernel-backport' / 'icc.json').read_text())
        replay = icc['replay']
        self.assertEqual(replay['status'], 'blocked')
        self.assertEqual([c['status'] for c in replay['commits']],
                         ['conflict', 'clean', 'conflict'])
        self.assertEqual([c['conditional'] for c in replay['commits']],
                         [False, True, True])
        self.assertEqual(len(replay['commits']), 3)
        summary = (self.directory / 'backport_worksheet.txt').read_text()
        self.assertIn('take-integration-counts: conflict=2, empty=0, not-tested=0', summary)
        self.assertIn('take-integration-counts: conditional=2, blockers=2', summary)
        self.assertIn('TAKE_REPLAY_CONFLICT ' + self.a[:12], summary)
        self.assertIn('TAKE_REPLAY_CONFLICT ' + self.c[:12] + ' (conditional)', summary)
        self.assertIn('Replay findings: 2 application difficulties', outcome.stdout)
        self.assertIn('CONFLICT: ' + self.a[:12], outcome.stdout)
        self.assertIn('CONFLICT: ' + self.c[:12], outcome.stdout)
        review = self.runcli('review', '--priority', 'critical')
        self.assertEqual(review.returncode, 0, review.stdout + review.stderr)
        self.assertIn(self.a[:12], review.stdout)
        self.assertIn(self.c[:12], review.stdout)
        self.assertEqual(git(self.work, 'rev-parse', 'HEAD'), baseline)
        self.assertEqual(git(self.work, 'status', '--porcelain'), '')
        self.assertEqual(git(self.work, 'worktree', 'list', '--porcelain').count('worktree '), 1)

    def test_failed_known_prerequisite_is_not_tested_but_independent_continues(self):
        self.write_plan(prerequisites={self.b: [self.a]})
        self._set_downstream_base(a='other implementation')
        outcome = self.check()
        self.assertEqual(outcome.returncode, 0, outcome.stdout + outcome.stderr)
        icc = json.loads((self.directory / '.kernel-backport' / 'icc.json').read_text())
        replay = icc['replay']
        self.assertEqual([c['status'] for c in replay['commits']],
                         ['conflict', 'not-tested', 'clean'])
        self.assertEqual(replay['commits'][1]['reason'], 'failed-prerequisite')
        self.assertEqual(replay['commits'][1]['blocked_by'], [self.a])
        self.assertTrue(replay['commits'][2]['conditional'])
        content = (self.directory / 'backport_worksheet.txt').read_text()
        self.assertIn('take-integration-counts: conflict=1, empty=0, not-tested=1', content)
        self.assertIn('take-integration-counts: conditional=1, blockers=1', content)
        # Integration text is width-wrapped; compare semantic content rather
        # than assuming this diagnostic fits on one physical line.
        self.assertIn('1 commit not tested because at least one known prerequisite failed',
                      ' '.join(content.split()))

    def test_successful_pick_survives_later_failure(self):
        self._set_downstream_base(c='conflicting c')
        outcome = self.check()
        self.assertEqual(outcome.returncode, 0, outcome.stdout + outcome.stderr)
        icc = json.loads((self.directory / '.kernel-backport' / 'icc.json').read_text())
        self.assertEqual([x['status'] for x in icc['replay']['commits']],
                         ['clean', 'clean', 'conflict'])
        self.assertFalse(icc['replay']['commits'][1]['conditional'])
        self.assertEqual(icc['replay']['skipped_commits'], 1)

    def test_transitive_blocked_prerequisites_remain_untested(self):
        self.write_plan(prerequisites={self.b: [self.a], self.c: [self.b]})
        self._set_downstream_base(a='conflicting a')
        outcome = self.check()
        self.assertEqual(outcome.returncode, 0, outcome.stdout + outcome.stderr)
        replay = json.loads((self.directory / '.kernel-backport' / 'icc.json').read_text())['replay']
        self.assertEqual([x['status'] for x in replay['commits']],
                         ['conflict', 'not-tested', 'not-tested'])
        self.assertEqual(replay['commits'][1]['blocked_by'], [self.a])
        self.assertEqual(replay['commits'][2]['blocked_by'], [self.b])
        self.assertEqual(replay['skipped_commits'], 3)
        self.assertIn('take-integration-counts: conditional=0, blockers=1',
                      (self.directory / 'backport_worksheet.txt').read_text())

    def test_empty_cherry_pick_does_not_stop_later_probes(self):
        self._set_downstream_base(a='first change')
        outcome = self.check()
        self.assertEqual(outcome.returncode, 0, outcome.stdout + outcome.stderr)
        icc = json.loads((self.directory / '.kernel-backport' / 'icc.json').read_text())
        statuses = [x['status'] for x in icc['replay']['commits']]
        self.assertEqual(statuses, ['empty', 'clean', 'clean'], outcome.stdout)
        self.assertEqual(icc['replay']['commits'][0]['conditional'], False)
        self.assertTrue(icc['replay']['commits'][1]['conditional'])
        self.assertIn('TAKE_REPLAY_EMPTY ' + self.a[:12],
                      (self.directory / 'backport_worksheet.txt').read_text())

    def test_check_dependency_cycle_does_not_claim_unordered_commits_applied(self):
        self.write_plan(prerequisites={self.a: [self.b], self.b: [self.a]})
        outcome = self.check('--no-replay')
        self.assertEqual(outcome.returncode, 2, outcome.stdout + outcome.stderr)
        self.assertIn('TAKE: 3 (pending: 3, already applied: 0)', outcome.stdout)
        content = (self.directory / 'backport_worksheet.txt').read_text()
        self.assertIn('take-integration: BLOCKED (TAKE=3, already-applied=0, clean=0)', content)
        self.assertIn('take-integration-counts: conflict=0, empty=0, not-tested=3', content)
        self.assertIn('take-integration-counts: conditional=0, blockers=1', content)
        self.assertIn('  integration-blocker: DEPENDENCY_CYCLE', content)

    def test_check_logical_blocker_updates_worksheet_without_replay(self):
        self.write_plan(decisions={self.b: 'SKIP'}, prerequisites={self.a: [self.b]})
        result = self.check('--no-replay')
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        content = (self.directory / 'backport_worksheet.txt').read_text()
        self.assertIn('take-integration: BLOCKED (TAKE=2, already-applied=0, clean=0)', content)
        self.assertIn('take-integration-counts: conflict=0, empty=0, not-tested=2', content)
        self.assertIn('take-integration-counts: conditional=0, blockers=1', content)
        self.assertIn('  integration-blocker: UNSATISFIED_PREREQUISITE ' + self.a[:12], content)
        self.assertIn('Replay not run; pending commits are untested', content)
        self.assertEqual(self.decide(self.b, 'TAKE', 'required').returncode, 0)
        self.assertEqual(self.check('--no-replay').returncode, 0)
        updated = (self.directory / 'backport_worksheet.txt').read_text()
        self.assertNotIn('UNSATISFIED_PREREQUISITE', updated)
        self.assertIn('take-integration: NOT-RUN (TAKE=3', updated)

    def test_initial_apply_and_resume(self):
        self.assertEqual(self.check().returncode, 0)
        outcome = self.execute()
        self.assertEqual(outcome.returncode, 0, outcome.stdout + outcome.stderr)
        self.assertEqual(len(self.state()['phases']), 1)
        self.assertEqual(self.runcli('status').returncode, 0)
        self.assertEqual(self.execute('resume').returncode, 0)
        self.assertTrue((self.work / 'a').exists())
        self.assertTrue((self.work / 'b').exists())
        self.assertTrue((self.work / 'c').exists())

    def test_stale_icc_blocks_after_edit(self):
        self.write_plan(decisions={self.b: 'SKIP'})
        self.assertEqual(self.check().returncode, 0)
        self.assertEqual(self.decide(self.b, 'TAKE', 'needed for integration').returncode, 0)
        result = self.execute()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('ICC is stale', result.stderr)
        self.assertEqual(git(self.work,'rev-parse','HEAD'), self.base)

    def test_promote_previously_skipped_after_execution(self):
        self.write_plan(decisions={self.b: 'SKIP'})
        self.assertEqual(self.check().returncode, 0)
        applied = self.execute()
        self.assertEqual(applied.returncode, 0, applied.stdout + applied.stderr)
        self.assertFalse((self.work / 'b').exists())
        self.assertEqual(len(self.state()['phases']), 1)
        self.assertEqual(self.decide(self.b, 'TAKE').returncode, 0)
        self.assertEqual(self.check().returncode, 0)
        resumed = self.execute('resume')
        self.assertEqual(resumed.returncode, 0, resumed.stdout + resumed.stderr)
        self.assertTrue((self.work / 'b').exists())
        self.assertEqual(len(self.state()['phases']), 2)
        self.assertEqual(self.runcli('status').returncode, 0)

    def test_fixes_target_absent_is_warning_not_blocker(self):
        self.write_plan(decisions={self.a: 'SKIP', self.c: 'SKIP'})
        path = self.directory / 'backport_plan.json'
        payload = json.loads(path.read_text())
        payload['commits'][self.b]['fixes'] = [self.a]
        path.write_text(json.dumps(payload))
        result = self.check('--no-replay')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('Logical blockers: 0', result.stdout)
        self.assertIn('FIXES_TARGET_NOT_SELECTED', result.stdout)
        self.assertNotIn('UNSATISFIED_PREREQUISITE', result.stdout)
        icc = json.loads((self.directory / '.kernel-backport' / 'icc.json').read_text())
        self.assertEqual(icc['logical_blockers'], [])
        self.assertEqual(icc['warnings'][0]['fixes_target'], self.a)

    def test_foreign_architecture_only_fixes_original_is_not_an_icc_warning(self):
        self.write_plan(decisions={self.a: "SKIP", self.c: "SKIP"})
        path = self.directory / "backport_plan.json"
        payload = json.loads(path.read_text())
        payload["architecture_filter"] = ["arm64"]
        payload["commits"][self.b]["fixes"] = [self.a]
        payload["commits"][self.a]["files"] = ["arch/x86/entry/vdso/vclock.c"]
        path.write_text(json.dumps(payload))
        report = self.check("--no-replay")
        self.assertEqual(report.returncode, 0, report.stdout + report.stderr)
        self.assertNotIn("FIXES_TARGET_NOT_SELECTED", report.stdout)
        # Mixed common + foreign code must remain visible for review.
        payload["commits"][self.a]["files"].append("include/linux/mm.h")
        path.write_text(json.dumps(payload))
        report = self.check("--no-replay")
        self.assertEqual(report.returncode, 0, report.stdout + report.stderr)
        self.assertIn("FIXES_TARGET_NOT_SELECTED", report.stdout)

    def test_selected_change_with_unselected_later_fix_blocks(self):
        # Legacy plan metadata (or no metadata at all) must not bypass MR policy.
        self.write_plan(decisions={self.b: 'SKIP', self.c: 'SKIP'})
        path = self.directory / 'backport_plan.json'
        payload = json.loads(path.read_text())
        payload['commits'][self.b]['fixes'] = [self.a]
        path.write_text(json.dumps(payload))
        result = self.check('--no-replay')
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn('UNRESOLVED_LATER_FIX', result.stdout)
        self.assertNotIn('KNOWN_FIX_NOT_SELECTED', result.stdout)
        self.assertNotIn('UNSATISFIED_PREREQUISITE', result.stdout)

    def test_later_fix_enforcement_ignores_all_plan_version_fields(self):
        # Never infer policy from application, plan, or advisor prompt versions.
        for metadata in (
            {'version': '0.1.4', 'advisor': {'prompt_version': '25'}},
            {'version': '0.1.7', 'advisor': {'prompt_version': '29'}},
            {'version': '0.1.8', 'advisor': {'prompt_version': '30'}},
            {'version': '0.1.9', 'advisor': {'prompt_version': 'unknown'}},
            {'version': '0.1.9'},
        ):
            with self.subTest(metadata=metadata):
                self.write_plan(decisions={self.b: 'SKIP', self.c: 'SKIP'})
                path = self.directory / 'backport_plan.json'
                payload = json.loads(path.read_text())
                payload.update(metadata)
                payload['commits'][self.b]['fixes'] = [self.a]
                path.write_text(json.dumps(payload))
                report = self.check('--no-replay')
                self.assertEqual(report.returncode, 2, report.stdout + report.stderr)
                self.assertIn('UNRESOLVED_LATER_FIX', report.stdout)

    def test_later_fix_not_in_executable_order_still_blocks(self):
        self.write_plan(decisions={self.b: 'SKIP', self.c: 'SKIP'})
        path = self.directory / 'backport_plan.json'
        payload = json.loads(path.read_text())
        # The fix is known to the planner, but was not selected for evaluation.
        payload['order'] = [self.a, self.b, self.c]
        fix_sha = 'f' * 40
        payload['commits'][fix_sha] = {
            'selected': False, 'match': {'status': 'missing'},
            'fixes': [self.a], 'subject': 'later fix not in worksheet',
        }
        path.write_text(json.dumps(payload))
        report = self.check('--no-replay')
        self.assertEqual(report.returncode, 2, report.stdout + report.stderr)
        self.assertIn('UNRESOLVED_LATER_FIX', report.stdout)
        icc = json.loads((self.directory / '.kernel-backport' / 'icc.json').read_text())
        unresolved = [b for b in icc['logical_blockers'] if b['kind'] == 'UNRESOLVED_LATER_FIX']
        self.assertEqual(len(unresolved), 1)
        self.assertEqual(unresolved[0]['fix_decision'], 'NOT_IN_PLAN')

    def test_engineer_added_known_fix_retains_original_provenance(self):
        self.write_plan(decisions={self.c: 'SKIP'})
        path = self.directory / 'backport_plan.json'
        payload = json.loads(path.read_text())
        payload['order'] = [self.a, self.c]
        payload['commits'][self.b]['selected'] = False
        payload['commits'][self.b]['fixes'] = [self.a]
        path.write_text(json.dumps(payload))
        worksheet = self.directory / 'backport_worksheet.txt'
        source = worksheet.read_text()
        start = source.index(f'{self.b[:12]} [DIRECT] ("second")')
        end = source.index(f'{self.c[:12]} [DIRECT] ("third")')
        worksheet.write_text(source[:start] + source[end:])
        blocked = self.check('--no-replay')
        self.assertEqual(blocked.returncode, 2, blocked.stdout + blocked.stderr)
        self.assertIn('UNRESOLVED_LATER_FIX', blocked.stdout)
        added = self.add(self.b, '--decision', 'SKIP')
        self.assertEqual(added.returncode, 0, added.stdout + added.stderr)
        resolved = self.check('--no-replay')
        self.assertEqual(resolved.returncode, 0, resolved.stdout + resolved.stderr)
        icc = json.loads((self.directory / '.kernel-backport' / 'icc.json').read_text())
        self.assertEqual(len(icc['omitted_fixes']), 1)
        self.assertIn('Omitted-fix: ' + self.b, icc['omitted_fixes'][0])
        self.assertIn('OMITTED_FIX', resolved.stdout)

    def test_selected_or_downstream_present_later_fix_does_not_block(self):
        path = self.directory / 'backport_plan.json'
        self.write_plan(decisions={self.c: 'SKIP'})
        payload = json.loads(path.read_text())
        payload['commits'][self.b]['fixes'] = [self.a]
        path.write_text(json.dumps(payload))
        selected = self.check('--no-replay')
        self.assertEqual(selected.returncode, 0, selected.stdout + selected.stderr)
        self.write_plan(decisions={self.b: 'SKIP', self.c: 'SKIP'})
        payload = json.loads(path.read_text())
        payload['commits'][self.b]['fixes'] = [self.a]
        payload['commits'][self.b]['match']['status'] = 'present'
        path.write_text(json.dumps(payload))
        present = self.check('--no-replay')
        self.assertEqual(present.returncode, 0, present.stdout + present.stderr)

    def test_unresolved_later_fix_blocks_mr_readiness(self):
        self.write_plan(decisions={self.b: "SKIP", self.c: "SKIP"})
        path = self.directory / "backport_plan.json"
        payload = json.loads(path.read_text())
        payload["commits"][self.b]["fixes"] = [self.a]
        path.write_text(json.dumps(payload))
        report = self.check("--no-replay")
        self.assertEqual(report.returncode, 2, report.stdout + report.stderr)
        self.assertIn("UNRESOLVED_LATER_FIX", report.stdout)
        self.assertNotIn("UNSATISFIED_PREREQUISITE", report.stdout)
        self.assertEqual(self.execute().returncode != 0, True)

        # Merely agreeing with the advisor SKIP is insufficient for CKI/MR.
        omit = self.decide(self.b, "SKIP", "Not applicable to the target ABI")
        self.assertEqual(omit.returncode, 0, omit.stdout + omit.stderr)
        report = self.check("--no-replay")
        self.assertEqual(report.returncode, 0, report.stdout + report.stderr)
        icc = json.loads((self.directory / ".kernel-backport" / "icc.json").read_text())
        self.assertEqual(len(icc["omitted_fixes"]), 1)
        self.assertIn("Omitted-fix: " + self.b, icc["omitted_fixes"][0])
        self.assertEqual(icc["logical_blockers"], [])

    def test_omission_reason_changes_icc_revision(self):
        self.write_plan(decisions={self.b: "SKIP", self.c: "SKIP"})
        path = self.directory / "backport_plan.json"
        payload = json.loads(path.read_text())
        payload["commits"][self.b]["fixes"] = [self.a]
        path.write_text(json.dumps(payload))
        self.assertEqual(self.decide(self.b, "SKIP", "Reason one").returncode, 0)
        self.assertEqual(self.check("--no-replay").returncode, 0)
        old_revision = json.loads((self.directory / ".kernel-backport" / "icc.json").read_text())["decision_revision"]
        self.assertEqual(self.decide(self.b, "SKIP", "Reason two").returncode, 0)
        from_context = __import__("subprocess").run(
            [sys.executable, str(CLI), "status", "--results-dir", str(self.directory),
             "--repo", str(self.work)], cwd=self.root, capture_output=True, text=True)
        self.assertIn("needs check", from_context.stdout)
        self.assertEqual(self.check("--no-replay").returncode, 0)
        new_revision = json.loads((self.directory / ".kernel-backport" / "icc.json").read_text())["decision_revision"]
        self.assertNotEqual(old_revision, new_revision)

    def test_missing_prerequisite_flags_block(self):
        self.write_plan(decisions={self.b: 'SKIP'}, prerequisites={self.c: [self.b]})
        result = self.check('--no-replay')
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn('UNSATISFIED_PREREQUISITE', result.stdout)
        outcome = self.execute()
        self.assertNotEqual(outcome.returncode, 0)
        self.assertEqual(self.decide(self.b, 'TAKE').returncode, 0)
        self.assertEqual(self.check('--no-replay').returncode, 0)

    def test_previously_applied_commit_cannot_be_unselected(self):
        self.assertEqual(self.check().returncode, 0)
        self.assertEqual(self.execute().returncode, 0)
        result = self.decide(self.a, 'SKIP')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('already applied', result.stderr)

    def test_conflict_revise_plan_and_resume_in_new_phase(self):
        # Source A adds file "a"; downstream independently adds it first.
        (self.work / 'a').write_text('a different downstream implementation\n')
        git(self.work, 'add', 'a')
        git(self.work, 'commit', '-m', 'existing downstream a')
        updated_base = git(self.work, 'rev-parse', 'HEAD')
        payload = json.loads((self.directory / 'backport_plan.json').read_text())
        payload['downstream'] = updated_base
        payload['replay_start'] = updated_base
        (self.directory / 'backport_plan.json').write_text(json.dumps(payload))
        result = self.check()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('Exploratory replay: blocked', result.stdout)
        attempt = self.execute()
        self.assertNotEqual(attempt.returncode, 0, attempt.stdout + attempt.stderr)
        self.assertEqual(git(self.work, 'rev-parse', 'HEAD'), updated_base)
        self.assertEqual(git(self.work, 'status', '--porcelain'), '')
        self.assertEqual(self.decide(self.a, 'SKIP').returncode, 0)
        self.assertEqual(self.check().returncode, 0)
        repaired = self.execute('resume')
        self.assertEqual(repaired.returncode, 0, repaired.stdout + repaired.stderr)
        self.assertEqual(len(self.state()['phases']), 2)
        self.assertTrue(self.state()['phases'][0]['superseded'])
        self.assertEqual((self.work / 'a').read_text(), 'a different downstream implementation\n')
        self.assertTrue((self.work / 'b').exists())
        self.assertTrue((self.work / 'c').exists())

    def test_dependency_order_is_corrected_by_icc(self):
        # Upstream order is A,B,C, but require B before A for the selected series.
        self.write_plan(prerequisites={self.a: [self.b]})
        report = self.check('--no-replay')
        self.assertEqual(report.returncode, 0, report.stdout + report.stderr)
        icc = json.loads((self.directory / '.kernel-backport' / 'icc.json').read_text())
        self.assertEqual(icc['pending_order'], [self.b, self.a, self.c])
        self.assertIn('REORDERED_PREREQUISITES', report.stdout if 'REORDERED_PREREQUISITES' in report.stdout else str(icc['warnings']))
        applied = self.execute()
        self.assertEqual(applied.returncode, 0, applied.stdout + applied.stderr)
        self.assertEqual(self.state()['phases'][0]['order'], [self.b, self.a, self.c])

    def test_dependency_cycle_is_blocked(self):
        self.write_plan(prerequisites={self.a: [self.b], self.b: [self.a]})
        report = self.check('--no-replay')
        self.assertEqual(report.returncode, 2, report.stdout + report.stderr)
        self.assertIn('DEPENDENCY_CYCLE', report.stdout)
        self.assertNotEqual(self.execute().returncode, 0)

    def test_execute_on_main_and_resume_without_override(self):
        git(self.work, 'switch', '-C', 'main', self.base)
        self.assertEqual(self.check().returncode, 0)
        applied = self.execute('apply', '--max-attempts', '1')
        self.assertEqual(applied.returncode, 0, applied.stderr + applied.stdout)
        self.assertEqual(self.state()['branch'], 'main')
        self.assertEqual(len(self.state()['phases']), 1)
        self.assertTrue((self.work / 'a').is_file())
        self.assertFalse((self.work / 'b').exists())
        self.assertEqual(git(self.work, 'status', '--porcelain'), '')
        resumed = self.execute('resume')
        self.assertEqual(resumed.returncode, 0, resumed.stderr + resumed.stdout)
        self.assertTrue((self.work / 'b').is_file())
        self.assertTrue((self.work / 'c').is_file())

    def test_execute_on_master_without_override(self):
        git(self.work, 'switch', '-C', 'master', self.base)
        self.assertEqual(self.check('--no-replay').returncode, 0)
        applied = self.execute('apply')
        self.assertEqual(applied.returncode, 0, applied.stderr + applied.stdout)
        self.assertEqual(self.state()['branch'], 'master')
        self.assertTrue((self.work / 'c').is_file())

    def test_execution_on_main_still_requires_current_icc(self):
        git(self.work, 'switch', '-C', 'main', self.base)
        self.assertEqual(self.check().returncode, 0)
        self.assertEqual(self.decide(self.b, 'SKIP', 'not needed').returncode, 0)
        stale = self.execute('apply')
        self.assertNotEqual(stale.returncode, 0)
        self.assertIn('ICC is stale', stale.stderr)
        self.assertEqual(git(self.work, 'rev-parse', 'HEAD'), self.base)

    def test_execution_on_main_still_requires_plan_baseline(self):
        git(self.work, 'switch', '-C', 'main', self.base)
        (self.work / 'local').write_text('a local commit\n')
        git(self.work, 'add', 'local')
        git(self.work, 'commit', '-m', 'diverged from plan baseline')
        changed_head = git(self.work, 'rev-parse', 'HEAD')
        result = self.check('--no-replay')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('HEAD does not match plan baseline', result.stderr)
        attempt = self.execute('apply')
        self.assertNotEqual(attempt.returncode, 0)
        self.assertEqual(git(self.work, 'rev-parse', 'HEAD'), changed_head)

    def test_allow_main_option_removed(self):
        for command in ('apply', 'resume'):
            result = self.runcli(command, '--allow-main')
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('unrecognized arguments: --allow-main', result.stderr)

    def test_project_id_from_environment_and_generic_commit_trailer(self):
        self.assertEqual(self.check().returncode, 0)
        with patch.dict(os.environ, {"PROJECT_ID": "rhel-223619_2"}):
            outcome = self.runcli('apply', '--skip-refresh',
                                  '--reference-root', str(self.root / 'refs'),
                                  '--upstream-url', str(self.up),
                                  '--downstream-url', str(self.up),
                                  '--max-attempts', '10')
        self.assertEqual(outcome.returncode, 0, outcome.stdout + outcome.stderr)
        message = git(self.work, 'log', '-1', '--format=%B')
        self.assertIn('Project-ID: rhel-223619_2', message)
        self.assertNotIn('JIRA:', message)
        state = self.state()
        self.assertEqual(state['project_id'], 'rhel-223619_2')
        phase = state['phases'][0]
        self.assertEqual(phase['project_id'], 'rhel-223619_2')
        snapshot = json.loads(Path(phase['plan']).read_text())
        self.assertEqual(snapshot['project_id'], 'rhel-223619_2')
        journal = json.loads(Path(phase['journal']).read_text())
        self.assertEqual(journal['project_id'], 'rhel-223619_2')

    def test_centos_stream_commit_header_on_apply_and_resume(self):
        # The user's CS10 fork uses main, but the branch name must not matter.
        stream_repo = self.root / 'centos-stream-10'
        self.work.rename(stream_repo)
        self.work = stream_repo
        git(self.work, 'switch', '-C', 'main', self.base)
        self.assertEqual(self.check('--no-replay').returncode, 0)
        first = self.execute('apply', '--max-attempts', '1', project_id='rhel-9668')
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        first_head = git(self.work, 'rev-parse', 'HEAD')
        message = git(self.work, 'log', '-1', '--format=%B')
        self.assertIn('JIRA: https://issues.redhat.com/browse/RHEL-9668', message)
        self.assertNotIn('Project-ID:', message)
        self.assertIn('(cherry picked from commit ', message)
        self.assertIn('Signed-off-by: Backport Tester <backport@example.com>', message)
        continued = self.execute('resume', project_id='rhel-9668')
        self.assertEqual(continued.returncode, 0, continued.stdout + continued.stderr)
        self.assertNotEqual(first_head, git(self.work, 'rev-parse', 'HEAD'))
        for message in git(self.work, 'log', '-3', '--format=%B%x00').split('\0'):
            if message.strip():
                self.assertIn('JIRA: https://issues.redhat.com/browse/RHEL-9668', message)
                self.assertNotIn('Project-ID:', message)
        self.assertEqual(self.state()['project_id'], 'rhel-9668')
        self.assertEqual(git(self.work, 'status', '--porcelain'), '')

    def test_centos_stream_remote_recognized_for_arbitrary_checkout_name(self):
        git(self.work, 'remote', 'add', 'cs10',
            'https://gitlab.com/redhat/centos-stream/src/kernel/centos-stream-10.git')
        self.assertEqual(self.check('--no-replay').returncode, 0)
        result = self.execute('apply', '--max-attempts', '1', project_id='rhel-9668')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        message = git(self.work, 'log', '-1', '--format=%B')
        self.assertIn('JIRA: https://issues.redhat.com/browse/RHEL-9668', message)
        self.assertNotIn('Project-ID:', message)

    def test_non_centos_commit_header_preserves_lowercase_project_id(self):
        self.assertEqual(self.check('--no-replay').returncode, 0)
        result = self.execute('apply', '--max-attempts', '1')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        message = git(self.work, 'log', '-1', '--format=%B')
        self.assertIn('Project-ID: rhel-223619_2', message)
        self.assertNotIn('JIRA:', message)

    def test_resume_rejects_project_change(self):
        self.assertEqual(self.check().returncode, 0)
        self.assertEqual(self.execute().returncode, 0)
        result = self.runcli('resume', '--project-id', 'another-project')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('another project ID', result.stderr)

    def test_project_id_is_required_and_sanitized(self):
        self.assertEqual(self.check().returncode, 0)
        with patch.dict(os.environ, {'PROJECT_ID': ''}):
            result = self.runcli('apply')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('--project-id is required', result.stderr)
        unsafe = self.runcli('apply', '--project-id', 'id\nFake-Trailer: something')
        self.assertNotEqual(unsafe.returncode, 0)
        self.assertIn('invalid project ID', unsafe.stderr)
        old = self.runcli('apply', '--jira', 'RHEL-223619')
        self.assertNotEqual(old.returncode, 0)
        self.assertIn('unrecognized arguments', old.stderr)

    def test_check_plan_dispatch_and_help(self):
        cp = cmd(self.root, sys.executable, str(CLI), 'plan', '--version', check=False)
        self.assertEqual(cp.returncode, 0)
        self.assertEqual(cp.stdout.strip(), 'kernel-backport ' + (CLI.parent / 'VERSION').read_text().strip())
        cp = cmd(self.root, sys.executable, str(CLI), 'legacy-backport', '--version', check=False)
        self.assertEqual(cp.returncode, 0)
        self.assertEqual(cp.stdout.strip(), 'kernel-backport ' + (CLI.parent / 'VERSION').read_text().strip())

    def test_review_override_is_supported(self):
        self.assertEqual(self.decide(self.b, 'REVIEW').returncode, 0)
        result = self.check('--no-replay')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('TAKE: 2', result.stdout)


    def make_extra(self, filename='new-provider'):
        extra = self.make_commit(filename, 'manually discovered prerequisite')
        git(self.work, 'fetch', '-q', str(self.up), 'main')
        return extra

    def add(self, commit, *args):
        return self.runcli('add', commit, '--reason', 'Engineer verified target relevance',
                           '--allow-outside-target', *args)

    def test_manual_add_preserves_original_plan_and_updates_icc(self):
        extra = self.make_extra()
        original = (self.directory / 'backport_plan.json').read_bytes()
        result = self.add(extra)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual((self.directory / 'backport_plan.json').read_bytes(), original)
        overlay = json.loads((self.directory / '.kernel-backport' / 'additions.json').read_text())
        self.assertEqual(overlay['commits'][0]['sha'], extra)
        self.assertEqual(overlay['commits'][0]['record']['advisor_decision'], 'REVIEW')
        self.assertIn(f'{extra[:12]} [ENGINEER]', (self.directory / 'backport_worksheet.txt').read_text())
        report = self.check('--no-replay')
        self.assertEqual(report.returncode, 0, report.stderr + report.stdout)
        self.assertIn('TAKE: 4', report.stdout)
        self.assertEqual(self.runcli('status').returncode, 0)

    def test_manual_add_with_required_by_changes_order(self):
        extra = self.make_extra()
        result = self.add(extra, '--required-by', self.a[:12])
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        report = self.check('--no-replay')
        self.assertEqual(report.returncode, 0, report.stderr + report.stdout)
        icc = json.loads((self.directory / '.kernel-backport' / 'icc.json').read_text())
        self.assertLess(icc['pending_order'].index(extra), icc['pending_order'].index(self.a))
        self.assertIn('REORDERED_PREREQUISITES', str(icc['warnings']))
        self.assertEqual(icc['logical_blockers'], [])

    def test_manual_add_after_execution_creates_new_phase(self):
        self.assertEqual(self.check('--no-replay').returncode, 0)
        outcome = self.execute()
        self.assertEqual(outcome.returncode, 0, outcome.stderr + outcome.stdout)
        extra = self.make_extra()
        result = self.add(extra)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        stale = self.execute('resume')
        self.assertNotEqual(stale.returncode, 0)
        self.assertIn('ICC is stale', stale.stderr)
        self.assertEqual(self.check('--no-replay').returncode, 0)
        resumed = self.execute('resume')
        self.assertEqual(resumed.returncode, 0, resumed.stderr + resumed.stdout)
        self.assertEqual(len(self.state()['phases']), 2)
        self.assertTrue((self.work / 'new-provider').exists())

    def test_manual_add_review_editable_in_worksheet(self):
        extra = self.make_extra()
        result = self.add(extra, '--decision', 'REVIEW')
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertIn('TAKE: 3', self.check('--no-replay').stdout)
        ws = self.directory / 'backport_worksheet.txt'
        ws.write_text(ws.read_text().replace('engineer-decision: REVIEW', 'engineer-decision: TAKE'))
        self.assertIn('TAKE: 4', self.check('--no-replay').stdout)

    def test_manual_add_refuses_duplicate_and_unknown_and_base(self):
        self.assertIn('already present in plan', self.add(self.a).stderr)
        self.assertIn('ancestor of the downstream baseline', self.add(self.base).stderr)
        bad = self.add('f' * 40)
        self.assertNotEqual(bad.returncode, 0)
        self.assertEqual((self.directory / 'backport_plan.json').exists(), True)
        self.assertFalse((self.directory / '.kernel-backport' / 'additions.json').exists())

    def test_manual_add_refuses_dependency_on_already_applied_commit(self):
        self.assertEqual(self.check('--no-replay').returncode, 0)
        self.assertEqual(self.execute().returncode, 0)
        extra = self.make_extra()
        error = self.add(extra, '--required-by', self.a)
        self.assertNotEqual(error.returncode, 0)
        self.assertIn('already applied', error.stderr)

    def test_manual_add_with_unsatisfied_provider_blocks_icc(self):
        extra = self.make_extra()
        self.write_plan(decisions={self.b: 'SKIP'})
        self.assertEqual(self.add(extra, '--requires', self.b[:12]).returncode, 0)
        outcome = self.check('--no-replay')
        self.assertEqual(outcome.returncode, 2, outcome.stderr + outcome.stdout)
        self.assertIn('UNSATISFIED_PREREQUISITE', outcome.stdout)
        self.assertEqual(self.decide(self.b, 'TAKE').returncode, 0)
        self.assertEqual(self.check('--no-replay').returncode, 0)


    def test_finish_requires_complete_execution_and_does_not_mutate_git(self):
        self.assertEqual(self.check('--no-replay').returncode, 0)
        initial = self.runcli('finish')
        self.assertNotEqual(initial.returncode, 0)
        self.assertIn('no execution phase', initial.stderr)
        self.assertFalse((self.directory / '.kernel-backport' / 'finish.json').exists())
        attempt = self.runcli('apply', '--project-id', 'rhel-223619', '--max-attempts', '1',
                              '--skip-refresh', '--reference-root', str(self.root / 'refs'),
                              '--upstream-url', str(self.up), '--downstream-url', str(self.up))
        self.assertEqual(attempt.returncode, 0, attempt.stderr + attempt.stdout)
        incomplete = self.runcli('finish')
        self.assertNotEqual(incomplete.returncode, 0)
        self.assertIn('execution is incomplete', incomplete.stderr)
        self.assertNotIn('finished', self.state())
        self.assertFalse((self.directory / '.kernel-backport' / 'finish.json').exists())
        self.assertEqual(self.execute('resume', project_id='rhel-223619').returncode, 0)
        before = git(self.work, 'rev-parse', 'HEAD')
        clean = git(self.work, 'status', '--porcelain')
        original_json = (self.directory / 'backport_plan.json').read_bytes()
        original_worksheet = (self.directory / 'backport_worksheet.txt').read_bytes()
        finished = self.runcli('finish')
        self.assertEqual(finished.returncode, 0, finished.stderr + finished.stdout)
        manifest = json.loads((self.directory / '.kernel-backport' / 'finish.json').read_text())
        report = (self.directory / '.kernel-backport' / 'finish-review.md').read_text()
        self.assertEqual(manifest['tool_version'], '1.0.0')
        self.assertEqual(manifest['counts']['TAKE'], 3)
        self.assertEqual(manifest['final_head'], before)
        self.assertEqual(len(manifest['phases']), 1)
        self.assertEqual(len([x for x in manifest['decisions'] if x['decision'] == 'TAKE']), 3)
        self.assertEqual([x['execution']['status'] for x in manifest['decisions']], ['APPLIED'] * 3)
        self.assertIn(self.a, report)
        self.assertIn('historical construction record', report)
        self.assertEqual(git(self.work, 'rev-parse', 'HEAD'), before)
        self.assertEqual(git(self.work, 'status', '--porcelain'), clean)
        self.assertEqual((self.directory / 'backport_plan.json').read_bytes(), original_json)
        self.assertEqual((self.directory / 'backport_worksheet.txt').read_bytes(), original_worksheet)
        manifest_bytes = (self.directory / '.kernel-backport' / 'finish.json').read_bytes()
        again = self.runcli('finish')
        self.assertEqual(again.returncode, 0, again.stderr)
        self.assertIn('Already finished', again.stdout)
        self.assertEqual((self.directory / '.kernel-backport' / 'finish.json').read_bytes(), manifest_bytes)
        self.assertIn('Execution: FINISHED', self.runcli('status').stdout)
        for action in (('resume',), ('apply',), ('check', '--no-replay'),
                       ('decide', self.a[:12], 'SKIP', '--reason', 'changed')):
            with self.subTest(action=action):
                if action[0] in ('apply', 'resume'):
                    result = self.execute(action[0], project_id='rhel-223619')
                else:
                    result = self.runcli(*action)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('plan is finished', result.stderr)
        self.assertEqual(self.runcli('review', '--all').returncode, 0)
        # MR review may rewrite the patch series after the one-way handoff.
        git(self.work, 'commit', '--amend', '--no-edit')
        self.assertNotEqual(git(self.work, 'rev-parse', 'HEAD'), before)
        after = self.runcli('status')
        self.assertEqual(after.returncode, 0, after.stderr)
        self.assertIn('Git history changed after handoff', after.stdout)
        self.assertEqual(self.runcli('finish').returncode, 0)

    def test_finish_after_selection_changes_creates_historical_phase_record(self):
        self.write_plan(decisions={self.b: 'SKIP'})
        self.assertEqual(self.check('--no-replay').returncode, 0)
        self.assertEqual(self.execute().returncode, 0)
        self.assertEqual(self.decide(self.b, 'TAKE', 'needed for product').returncode, 0)
        self.assertNotEqual(self.runcli('finish').returncode, 0)
        self.assertEqual(self.check('--no-replay').returncode, 0)
        self.assertEqual(self.execute('resume').returncode, 0)
        done = self.runcli('finish')
        self.assertEqual(done.returncode, 0, done.stderr + done.stdout)
        manifest = json.loads((self.directory / '.kernel-backport' / 'finish.json').read_text())
        self.assertEqual(len(manifest['phases']), 2)
        self.assertTrue(manifest['phases'][0]['superseded'])
        self.assertFalse(manifest['phases'][1]['superseded'])
        self.assertEqual(manifest['counts']['TAKE'], 3)
        self.assertEqual(next(x for x in manifest['decisions'] if x['upstream_sha'] == self.b)['rationale'],
                         'needed for product')

    def test_finish_after_skipping_remaining_candidates(self):
        self.assertEqual(self.check('--no-replay').returncode, 0)
        one = self.runcli('apply', '--project-id', 'rhel-223619', '--max-attempts', '1',
                          '--skip-refresh', '--reference-root', str(self.root / 'refs'),
                          '--upstream-url', str(self.up), '--downstream-url', str(self.up))
        self.assertEqual(one.returncode, 0, one.stderr + one.stdout)
        self.assertEqual(self.decide(self.b, 'SKIP', 'not needed').returncode, 0)
        self.assertEqual(self.decide(self.c, 'SKIP', 'not needed').returncode, 0)
        self.assertEqual(self.check('--no-replay').returncode, 0)
        resumed = self.execute('resume', project_id='rhel-223619')
        self.assertEqual(resumed.returncode, 0, resumed.stderr + resumed.stdout)
        self.assertIn('No pending TAKE commits', resumed.stdout)
        # Old phase was superseded before reaching its former pending items.
        self.assertTrue(self.state()['phases'][0]['superseded'])
        ended = self.runcli('finish')
        self.assertEqual(ended.returncode, 0, ended.stderr + ended.stdout)
        manifest = json.loads((self.directory / '.kernel-backport' / 'finish.json').read_text())
        self.assertEqual(manifest['counts']['TAKE'], 1)
        self.assertEqual(manifest['counts']['SKIP'], 2)

    def test_finish_rejects_corrupt_journal_without_finalizing(self):
        self.assertEqual(self.check('--no-replay').returncode, 0)
        self.assertEqual(self.execute().returncode, 0)
        journal_path = self.directory / '.kernel-backport' / 'phase_001' / 'journal.json'
        journal = json.loads(journal_path.read_text())
        journal['entries'][0]['sha'] = self.c
        journal_path.write_text(json.dumps(journal))
        result = self.runcli('finish')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('invalid journal entry/order', result.stderr)
        self.assertFalse((self.directory / '.kernel-backport' / 'finish.json').exists())
        self.assertNotIn('finished', self.state())



class VersionConsistencyTests(unittest.TestCase):
    def test_every_entrypoint_uses_one_release_version(self):
        root = CLI.parent
        version = (root / 'VERSION').read_text(encoding='ascii').strip()
        expected = f'kernel-backport {version}'
        entries = [
            [sys.executable, str(CLI), '--version'],
            [sys.executable, str(CLI), 'plan', '--version'],
            [sys.executable, str(CLI), 'legacy-backport', '--version'],
            [sys.executable, str(root / 'engines/planner/kernel-backport-planner'), '--version'],
            [sys.executable, str(root / 'engines/backporter/kernel-backporter'), '--version'],
        ]
        for entry in entries:
            with self.subTest(entry=entry):
                cp = subprocess.run(entry, cwd=root, capture_output=True, text=True)
                self.assertEqual(cp.returncode, 0, cp.stderr)
                self.assertEqual(cp.stdout.strip(), expected)



if __name__ == '__main__':
    unittest.main()
