import copy
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('sync_upstream', Path(__file__).with_name('sync_upstream.py'))
sync = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sync)


class UpstreamPRTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.original_directory = Path.cwd()
        self.addCleanup(os.chdir, self.original_directory)
        self.upstream, self.origin, self.work = [self.root / p for p in ('upstream', 'origin.git', 'work')]
        self.upstream.mkdir()
        self.git(self.upstream, 'init', '-q', '-b', 'main')
        self.identity(self.upstream)
        self.commit(self.upstream, 'shared', 'initial')
        self.git(self.upstream, 'tag', 'v1.0.0')
        self.git(self.root, 'clone', '--bare', str(self.upstream), str(self.origin))
        self.git(self.origin, 'branch', 'milesians', 'main')
        self.git(self.root, 'clone', '-b', 'milesians', str(self.origin), str(self.work))
        self.identity(self.work)
        os.chdir(self.work)
        self.release = {'tag_name': 'v1.1.0', 'body': 'Upstream notes\n\n- fixed bug',
                        'draft': False, 'prerelease': False, 'published_at': '2026-09-22T00:00:00Z'}
        self.pr = None
        self.dispatched = []
        self.checks_pass = True
        self.close_during_checks = False
        self.fail_create = False
        self.addCleanup(patch.stopall)
        patch.dict(os.environ, {'GITHUB_REPOSITORY': 'Milesians/sub2api',
                                'GITHUB_OUTPUT': str(self.root / 'outputs'),
                                'GITHUB_STEP_SUMMARY': str(self.root / 'summary')}).start()
        patch.object(sync, 'api', side_effect=self.api).start()
        self.release_api = patch.object(sync, 'release_info', side_effect=lambda tag='latest': self.release).start()
        patch.object(sync, 'run', side_effect=self.command).start()

    def git(self, directory, *args):
        return subprocess.check_output(['git', '-C', str(directory), *args], text=True,
                                       stderr=subprocess.PIPE).strip()

    def identity(self, directory):
        self.git(directory, 'config', 'user.name', 'test')
        self.git(directory, 'config', 'user.email', 'test@example.com')

    def commit(self, directory, name, value):
        (directory / name).write_text(value)
        self.git(directory, 'add', name)
        self.git(directory, 'commit', '-qm', value)

    def command(self, *args, input=None):
        if args[0] != 'gh':
            return subprocess.check_output(args, input=input, text=True, stderr=subprocess.PIPE).strip()
        if args[1:3] == ('pr', 'list'):
            if not self.pr or args[args.index('--head') + 1] != self.pr['head']['ref']:
                return '[]'
            state = 'MERGED' if self.pr['merged'] else self.pr['state'].upper()
            return json.dumps([{'number': 1, 'state': state}])
        if args[1:3] == ('workflow', 'run'):
            self.dispatched.append(args)
            return ''
        if args[1:3] == ('pr', 'checks'):
            if not self.checks_pass:
                raise subprocess.CalledProcessError(1, args)
            if self.close_during_checks:
                self.pr['state'] = 'closed'
            return ''
        self.fail(f'Unexpected gh call: {args}')

    def api(self, path, data=None):
        if path.endswith('/pulls') and data:
            if self.fail_create:
                raise RuntimeError('temporary API failure')
            self.pr = {'number': 1, 'state': 'open', 'merged': False, 'merge_commit_sha': None,
                       'title': data['title'], 'body': data['body'], 'labels': [],
                       'user': {'login': 'github-actions[bot]'}, 'base': {'ref': data['base']},
                       'head': {'ref': data['head'], 'repo': {'full_name': 'Milesians/sub2api'}}}
            return copy.deepcopy(self.pr)
        if path.endswith('/labels'):
            names = {label['name'] for label in self.pr['labels']} | set(data['labels'])
            self.pr['labels'] = [{'name': name} for name in sorted(names)]
            return copy.deepcopy(self.pr['labels'])
        if path.endswith('/pulls/1'):
            self.pr['head']['sha'] = self.git(self.origin, 'rev-parse', self.pr['head']['ref'])
            return copy.deepcopy(self.pr)
        self.fail(f'Unexpected API call: {path}')

    def discover_release(self, conflict=False):
        self.commit(self.work, 'shared' if conflict else 'custom', 'fork customization')
        self.git(self.work, 'push', 'origin', 'milesians')
        self.before = self.git(self.origin, 'rev-parse', 'milesians')
        self.commit(self.upstream, 'shared' if conflict else 'new', 'upstream release change')
        self.git(self.upstream, 'tag', '-a', 'v1.1.0', '-m', 'release')
        self.upstream_sha = self.git(self.upstream, 'rev-parse', 'HEAD')
        self.commit(self.upstream, 'unreleased', 'newer main change')
        sync.discover('release', str(self.upstream))

    def test_discovery_only_opens_marked_pr_without_merging_or_tagging(self):
        self.discover_release()
        self.assertEqual(self.git(self.origin, 'rev-parse', 'milesians'), self.before)
        self.assertEqual(self.git(self.origin, 'tag', '-l'), 'v1.0.0')
        self.assertIn('upstream-release', {label['name'] for label in self.pr['labels']})
        self.assertIn(self.release['body'], self.pr['body'])
        self.assertEqual(len(self.dispatched), 1)
        source = sync.source_for(self.api('repos/Milesians/sub2api/pulls/1'))
        self.assertEqual(source['sha'], self.upstream_sha)
        tree = self.git(self.origin, 'ls-tree', '-r', '--name-only', self.pr['head']['ref'])
        self.assertNotIn('unreleased', tree)
        self.assertIn('.github/upstream-releases/v1.1.0.json', tree)

    def test_clean_pr_merges_and_tags_fork_commit_with_upstream_notes(self):
        self.discover_release()
        sync.process(1)
        head = self.git(self.origin, 'rev-parse', 'milesians')
        self.assertEqual(self.git(self.origin, 'rev-parse', 'v1.1.0^{commit}'), head)
        self.assertEqual(self.git(self.origin, 'show', 'milesians:custom'), 'fork customization')
        self.assertEqual(self.git(self.origin, 'show', 'milesians:new'), 'upstream release change')
        self.assertNotIn('unreleased', self.git(self.origin, 'ls-tree', '--name-only', 'milesians'))
        self.assertIn(self.release['body'], self.git(self.origin, 'tag', '-l', '--format=%(contents)', 'v1.1.0'))

    def test_conflict_calls_codex_once_and_keeps_target_unchanged(self):
        self.discover_release(conflict=True)
        sync.process(1)
        self.assertEqual(self.git(self.origin, 'rev-parse', 'milesians'), self.before)
        self.assertEqual(self.git(self.origin, 'tag', '-l'), 'v1.0.0')
        self.assertIn('codex-review-required', {label['name'] for label in self.pr['labels']})
        self.assertEqual((self.root / 'outputs').read_text(), 'conflict_pr=1\n')
        sync.process(1)
        self.assertEqual((self.root / 'outputs').read_text(), 'conflict_pr=1\n')
        self.assertEqual(self.git(self.work, 'status', '--porcelain'), '')

    def test_manual_merge_of_conflict_pr_publishes_after_review(self):
        self.discover_release(conflict=True)
        sync.process(1)
        self.git(self.work, 'checkout', '--detach', self.before)
        subprocess.run(['git', 'merge', '--no-ff', '--no-commit', self.pr['head']['sha']],
                       capture_output=True, check=False)
        (self.work / 'shared').write_text('fork customization\nupstream release change')
        self.git(self.work, 'add', 'shared')
        self.git(self.work, 'commit', '-qm', 'reviewed conflict resolution')
        merged = self.git(self.work, 'rev-parse', 'HEAD')
        self.git(self.work, 'push', 'origin', 'HEAD:milesians')
        self.pr.update(merged=True, state='closed', merge_commit_sha=merged)
        sync.process(1)
        self.assertEqual(self.git(self.origin, 'rev-parse', 'v1.1.0^{commit}'), merged)
        # Reprocessing a closed event must not republish or overwrite the tag.
        sync.process(1)
        self.assertEqual(self.git(self.origin, 'rev-parse', 'v1.1.0^{commit}'), merged)

    def test_duplicate_discovery_reuses_pr_and_closed_pr_is_respected(self):
        self.discover_release()
        source_head = self.git(self.origin, 'rev-parse', self.pr['head']['ref'])
        sync.discover('release', str(self.upstream))
        self.assertEqual(len(self.dispatched), 2)
        self.assertEqual(self.git(self.origin, 'rev-parse', self.pr['head']['ref']), source_head)
        self.pr['state'] = 'closed'
        sync.discover('release', str(self.upstream))
        self.assertEqual(len(self.dispatched), 2)

    def test_failed_pr_creation_recovers_without_rewriting_branch(self):
        self.fail_create = True
        with self.assertRaises(RuntimeError):
            self.discover_release()
        old = self.git(self.origin, 'rev-parse', 'sync/upstream-release-v1.1.0')
        self.fail_create = False
        sync.discover('release', str(self.upstream))
        self.assertEqual(self.git(self.origin, 'rev-parse', self.pr['head']['ref']), old)

    def test_release_already_in_main_still_has_a_reviewable_pr(self):
        self.git(self.upstream, 'tag', 'v1.1.0')
        sync.discover('release', str(self.upstream))
        diff = self.git(self.work, 'diff', '--name-only', 'origin/milesians...HEAD')
        self.assertEqual(diff, '.github/upstream-releases/v1.1.0.json')
        sync.process(1)
        self.assertIn('v1.1.0', self.git(self.origin, 'tag', '-l'))

    def test_main_pr_merges_without_creating_a_release(self):
        self.commit(self.upstream, 'new', 'weekly main change')
        sync.discover('main', str(self.upstream))
        self.assertIn('upstream-main', {label['name'] for label in self.pr['labels']})
        sync.process(1)
        self.release_api.assert_not_called()
        self.assertEqual(self.git(self.origin, 'show', 'milesians:new'), 'weekly main change')
        self.assertEqual(self.git(self.origin, 'tag', '-l'), 'v1.0.0')

    def test_no_main_changes_or_no_new_release_creates_nothing(self):
        sync.discover('main', str(self.upstream))
        self.release = None
        sync.discover('release', str(self.upstream))
        self.release = {'tag_name': 'v1.0.0'}
        sync.discover('release', str(self.upstream))
        self.assertIsNone(self.pr)
        self.assertFalse(self.dispatched)

    def test_failed_checks_and_concurrent_pr_closure_block_merging(self):
        self.discover_release()
        self.checks_pass = False
        with self.assertRaises(subprocess.CalledProcessError):
            sync.process(1)
        self.assertEqual(self.git(self.origin, 'rev-parse', 'milesians'), self.before)
        self.git(self.work, 'merge', '--abort')
        self.checks_pass = True
        self.close_during_checks = True
        with self.assertRaisesRegex(ValueError, 'changed during validation'):
            sync.process(1)
        self.assertEqual(self.git(self.origin, 'rev-parse', 'milesians'), self.before)

    def test_untrusted_pr_is_never_merged(self):
        self.discover_release()
        self.pr['user']['login'] = 'someone-else'
        sync.process(1)
        self.assertEqual(self.git(self.origin, 'rev-parse', 'milesians'), self.before)
        self.assertEqual(self.git(self.origin, 'tag', '-l'), 'v1.0.0')

    def test_source_branch_mismatch_is_rejected(self):
        self.discover_release()
        self.pr['labels'] = [{'name': 'upstream-sync'}, {'name': 'upstream-main'}]
        with self.assertRaisesRegex(ValueError, 'source labels'):
            sync.process(1)


if __name__ == '__main__':
    unittest.main()
