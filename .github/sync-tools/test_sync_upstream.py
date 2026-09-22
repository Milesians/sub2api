import os
from pathlib import Path
import subprocess
import tempfile
import unittest


SCRIPT = Path(__file__).with_name('sync_upstream.py').resolve()


class SyncUpstreamTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.upstream = self.root / 'upstream'
        self.origin = self.root / 'origin.git'
        self.work = self.root / 'work'
        self.upstream.mkdir()
        self.git(self.upstream, 'init', '-q', '-b', 'main')
        self.identity(self.upstream)
        self.commit(self.upstream, 'shared', 'initial')
        self.git(self.upstream, 'tag', 'v1.0.0')
        self.git(self.root, 'clone', '--bare', str(self.upstream), str(self.origin))
        self.git(self.origin, 'branch', 'milesians', 'main')
        self.git(self.root, 'clone', '-b', 'milesians', str(self.origin), str(self.work))
        self.identity(self.work)

    def git(self, directory, *args):
        return subprocess.check_output(['git', '-C', str(directory), *args],
                                       text=True, stderr=subprocess.PIPE).strip()

    def identity(self, directory):
        self.git(directory, 'config', 'user.name', 'test')
        self.git(directory, 'config', 'user.email', 'test@example.com')

    def commit(self, directory, name, content):
        (directory / name).write_text(content)
        self.git(directory, 'add', name)
        self.git(directory, 'commit', '-qm', content)

    def run_sync(self, success=True):
        result = subprocess.run(['python3', str(SCRIPT), '--upstream', str(self.upstream)],
                                cwd=self.work, text=True, capture_output=True,
                                env={**os.environ, 'GITHUB_STEP_SUMMARY': str(self.root / 'summary')})
        self.assertEqual(result.returncode == 0, success, result.stdout + result.stderr)
        return result

    def tags(self):
        return self.git(self.origin, 'tag', '-l').splitlines()

    def test_new_release_tag_points_to_merged_fork_and_is_not_repeated(self):
        self.commit(self.work, 'custom', 'fork customization')
        self.git(self.work, 'push', 'origin', 'milesians')
        custom_sha = self.git(self.work, 'rev-parse', 'HEAD')
        self.commit(self.upstream, 'new', 'upstream change')
        self.git(self.upstream, 'tag', '-a', 'v1.1.0', '-m', 'upstream release')
        self.run_sync()
        tagged_sha = self.git(self.origin, 'rev-parse', 'v1.1.0^{commit}')
        self.assertEqual(tagged_sha, self.git(self.origin, 'rev-parse', 'milesians'))
        self.assertNotEqual(tagged_sha, self.git(self.upstream, 'rev-parse', 'v1.1.0^{commit}'))
        self.git(self.work, 'merge-base', '--is-ancestor', custom_sha, tagged_sha)
        self.assertEqual(self.git(self.origin, 'show', 'v1.1.0:custom'), 'fork customization')
        self.run_sync()
        self.assertEqual(tagged_sha, self.git(self.origin, 'rev-parse', 'milesians'))
        self.assertEqual(self.tags(), ['v1.0.0', 'v1.1.0'])

    def test_new_tag_is_detected_even_without_a_new_main_commit(self):
        self.commit(self.upstream, 'new', 'upstream change')
        self.run_sync()
        self.assertEqual(self.tags(), ['v1.0.0'])
        self.git(self.upstream, 'tag', 'v1.1.0')
        self.run_sync()
        self.assertEqual(self.git(self.origin, 'rev-parse', 'v1.1.0^{commit}'),
                         self.git(self.origin, 'rev-parse', 'milesians'))

    def test_merge_conflict_does_not_push_branch_or_release(self):
        self.commit(self.work, 'shared', 'fork change')
        self.git(self.work, 'push', 'origin', 'milesians')
        before = self.git(self.origin, 'rev-parse', 'milesians')
        self.commit(self.upstream, 'shared', 'upstream change')
        self.git(self.upstream, 'tag', 'v1.1.0')
        self.run_sync(success=False)
        self.assertEqual(before, self.git(self.origin, 'rev-parse', 'milesians'))
        self.assertEqual(self.tags(), ['v1.0.0'])
        self.assertEqual(self.git(self.work, 'status', '--porcelain'), '')
        self.assertIn('shared', (self.root / 'summary').read_text())

    def test_release_outside_main_is_deferred(self):
        self.git(self.upstream, 'checkout', '-qb', 'release')
        self.commit(self.upstream, 'new', 'release only')
        self.git(self.upstream, 'tag', 'v1.1.0')
        self.git(self.upstream, 'checkout', 'main')
        self.run_sync()
        self.assertEqual(self.tags(), ['v1.0.0'])

    def test_prereleases_do_not_trigger_publication(self):
        self.commit(self.upstream, 'new', 'upstream prerelease')
        self.git(self.upstream, 'tag', 'v1.1.0-rc.1')
        self.run_sync()
        self.assertEqual(self.tags(), ['v1.0.0'])
        self.assertEqual(self.git(self.origin, 'show', 'milesians:new'), 'upstream prerelease')

    def test_selects_highest_version_and_does_not_downgrade_fork(self):
        self.git(self.upstream, 'tag', 'v1.9.0')
        self.git(self.upstream, 'tag', 'v1.10.0')
        self.run_sync()
        self.assertEqual(self.tags(), ['v1.0.0', 'v1.10.0'])
        self.git(self.upstream, 'tag', '-d', 'v1.10.0')
        self.run_sync()
        self.assertEqual(self.tags(), ['v1.0.0', 'v1.10.0'])

    def test_rejected_push_does_not_partially_publish(self):
        self.commit(self.upstream, 'new', 'upstream change')
        self.git(self.upstream, 'tag', 'v1.1.0')
        before = self.git(self.origin, 'rev-parse', 'milesians')
        hook = self.origin / 'hooks' / 'update'
        hook.write_text('#!/bin/sh\n[ "$1" != "refs/tags/v1.1.0" ]\n')
        hook.chmod(0o755)
        self.run_sync(success=False)
        self.assertEqual(before, self.git(self.origin, 'rev-parse', 'milesians'))
        self.assertEqual(self.tags(), ['v1.0.0'])


if __name__ == '__main__':
    unittest.main()
