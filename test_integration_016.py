"""0.1.16 P0 replay conflict reporting without altering worksheet decisions."""
import unittest
from review_queues import build_queues


class IntegrationPriorityTests(unittest.TestCase):
    def setUp(self):
        self.s = 'a'*40
        self.rec = {
            'subject': 'pmdomain: core: Leave powered-on genpds on',
            'files': ['drivers/pmdomain/core.c'],
            'advisor_decision': 'TAKE',
            'match': {'status': 'missing'},
            'advisor_prerequisites': [],
        }
        self.payload = {'commits': {self.s:self.rec}, 'order':[self.s]}

    def icc(self, status):
        return {'logical_blockers': [], 'replay': {'status': status,
           'commits': [{'sha': self.s, 'status':'conflict',
                        'files':['drivers/pmdomain/core.c'],
                        'diagnosis': {'classification': 'unverified-integration-conflict',
                                      'suggested_action':'Review upstream series'}}]}}

    def test_replay_conflict_is_p0_even_when_take_and_logical_clean(self):
        result = build_queues(self.payload, icc=self.icc('blocked'))
        self.assertEqual(result['summary']['p0'], 1)
        self.assertEqual(result['queues']['p0'][0]['sha'], self.s)
        self.assertEqual(result['queues']['p0'][0]['decision'], 'TAKE')
        self.assertIn('not a proven prerequisite',
                      result['queues']['p0'][0]['priority_reason'])
        self.assertEqual(result['queues']['p0'][0]['conflict_diagnosis']['classification'],
                         'unverified-integration-conflict')
        self.assertEqual(self.rec['advisor_decision'], 'TAKE')

    def test_logical_only_run_does_not_claim_a_replay_failure(self):
        result = build_queues(self.payload, icc=self.icc('not-run'))
        self.assertEqual(result['summary']['p0'], 0)

if __name__ == '__main__':
    unittest.main()


class RealReplayConflictTests(unittest.TestCase):
    """Exercise the actual CLI and disposable ICC Git worktree."""
    def test_conflicting_take_is_visible_in_critical_review(self):
        import json
        import os
        from pathlib import Path
        import subprocess
        import sys
        import tempfile
        from review_queues import _fresh_icc
        cli = Path(__file__).with_name('kernel-backport')
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / 'repo'
            plan = Path(tmp) / 'plan'
            repo.mkdir()
            plan.mkdir()
            def git(*args):
                cp = subprocess.run(['git', *args], cwd=repo,
                                    capture_output=True, text=True)
                self.assertEqual(cp.returncode, 0, cp.stderr)
                return cp.stdout.strip()
            git('init', '-b', 'main')
            git('config', 'user.name', 'Test')
            git('config', 'user.email', 'test@example.invalid')
            path = repo / 'drivers/pmdomain/core.c'
            path.parent.mkdir(parents=True)
            path.write_text('int power_state = 0;\n')
            git('add', '.')
            git('commit', '-qm', 'base')
            root = git('rev-parse', 'HEAD')
            path.write_text('int power_state = 1;\n')
            git('commit', '-qam', 'upstream change')
            upstream = git('rev-parse', 'HEAD')
            git('switch', '-c', 'downstream', root)
            path.write_text('int power_state = 2;\n')
            git('commit', '-qam', 'downstream change')
            baseline = git('rev-parse', 'HEAD')
            payload = {
                'downstream': baseline, 'replay_start': baseline,
                'target': upstream, 'order': [upstream],
                'commits': {upstream: {
                    'subject': 'upstream change', 'advisor_decision':'TAKE',
                    'files': ['drivers/pmdomain/core.c'],
                    'advisor_prerequisites': [],
                    'match': {'status':'missing'},
                }},
            }
            (plan / 'backport_plan.json').write_text(json.dumps(payload))
            (plan / 'backport_worksheet.txt').write_text(
                'BACKPORT WORKSHEET (oldest first)\n' + '='*80 + '\n' +
                f'{upstream[:12]} [DIRECT] ("upstream change")\n'
                '      advisor: TAKE\n      advisor-rationale: test\n')
            opts = ['--results-dir', str(plan), '--repo', str(repo)]
            cp = subprocess.run([sys.executable, str(cli), 'check', *opts],
                                capture_output=True, text=True)
            self.assertEqual(cp.returncode, 0, cp.stderr)
            self.assertIn('Exploratory replay: blocked', cp.stdout)
            icc = json.loads((plan/'.kernel-backport/icc.json').read_text())
            self.assertEqual(icc['logical_blockers'], [])
            diag = icc['replay']['commits'][0]['diagnosis']
            self.assertFalse(diag['proof_of_missing_prerequisite'])
            self.assertIn('power_state', diag['upstream_patch_excerpt'])
            cp = subprocess.run([sys.executable, str(cli), 'review',
                                 '--results-dir', str(plan), '--priority', 'critical'],
                                capture_output=True, text=True)
            self.assertEqual(cp.returncode, 0, cp.stderr)
            self.assertIn('P0 CRITICAL (1)', cp.stdout)
            self.assertIn('unverified-integration-conflict', cp.stdout)
            self.assertIn(upstream[:12], cp.stdout)
            self.assertEqual(path.read_text(), 'int power_state = 2;\n')
