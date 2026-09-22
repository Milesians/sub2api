#!/usr/bin/env python3
"""Merge upstream main and tag the fork only for a new stable upstream version."""
import argparse
import os
from pathlib import Path
import re
import subprocess


STABLE_TAG = re.compile(r'v(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)')


def git(*args):
    return subprocess.check_output(['git', *args], text=True).strip()


def summary(message):
    print(message)
    if path := os.environ.get('GITHUB_STEP_SUMMARY'):
        with Path(path).open('a') as output:
            output.write(message + '\n')


def stable_tags(remote):
    tags = {}
    for line in git('ls-remote', '--refs', '--tags', remote, 'refs/tags/v*').splitlines():
        _, ref = line.split()
        name = ref.removeprefix('refs/tags/')
        if match := STABLE_TAG.fullmatch(name):
            tags[tuple(map(int, match.groups()))] = name
    return tags


def is_ancestor(ancestor, descendant):
    result = subprocess.run(['git', 'merge-base', '--is-ancestor', ancestor, descendant])
    if result.returncode not in (0, 1):
        result.check_returncode()
    return result.returncode == 0


def sync(upstream):
    git('config', 'user.name', 'github-actions[bot]')
    git('config', 'user.email', '41898282+github-actions[bot]@users.noreply.github.com')
    git('fetch', '--no-tags', 'origin', 'milesians')
    git('merge', '--ff-only', 'FETCH_HEAD')
    git('fetch', '--no-tags', upstream, 'main')
    upstream_commit = git('rev-parse', 'FETCH_HEAD')

    if not is_ancestor(upstream_commit, 'HEAD'):
        try:
            git('merge', '--no-edit', upstream_commit)
        except subprocess.CalledProcessError:
            conflicts = git('diff', '--name-only', '--diff-filter=U')
            summary('Upstream merge failed; resolve conflicts manually.\n```\n' + conflicts + '\n```')
            subprocess.run(['git', 'merge', '--abort'], check=False)
            raise

    # Check tags even when main did not change: a release can be tagged later.
    upstream_tags = stable_tags(upstream)
    fork_tags = stable_tags('origin')
    release_tag = None
    if upstream_tags:
        latest = max(upstream_tags)
        if not fork_tags or latest > max(fork_tags):
            candidate = upstream_tags[latest]
            # Fetch only into FETCH_HEAD; fork tags point to our own merged code.
            git('fetch', '--no-tags', upstream, f'refs/tags/{candidate}')
            tagged_commit = git('rev-parse', 'FETCH_HEAD^{commit}')
            if is_ancestor(tagged_commit, 'HEAD'):
                git('tag', '-a', candidate, 'HEAD', '-m',
                    f'Sync upstream {candidate} into milesians\n\nUpstream commit: {tagged_commit}')
                release_tag = candidate
            else:
                summary(f'Upstream {candidate} is not merged into milesians yet; release deferred.')

    refs = ['HEAD:refs/heads/milesians']
    if release_tag:
        refs.append(f'refs/tags/{release_tag}')
    # Publish the branch and its release tag together, without force-pushing.
    git('push', '--atomic', 'origin', *refs)
    summary(f'milesians contains upstream main ({upstream_commit}).')
    if release_tag:
        summary(f'Created {release_tag} on milesians; its tag push triggers the Release workflow.')
    else:
        summary('No new stable upstream version to publish; no image build triggered.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--upstream', default='https://github.com/Wei-Shaw/sub2api.git')
    sync(parser.parse_args().upstream)
