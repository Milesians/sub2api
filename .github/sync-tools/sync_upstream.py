#!/usr/bin/env python3
"""Sync published upstream releases and weekly main updates independently."""
import argparse
import json
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


def latest_release(repository):
    response = subprocess.run(['gh', 'api', f'repos/{repository}/releases/latest'],
                              text=True, capture_output=True)
    if response.returncode:
        # GitHub returns 404 when the repository has no published stable release.
        try:
            missing = str(json.loads(response.stdout).get('status')) == '404'
        except (ValueError, AttributeError):
            missing = False
        if missing:
            return None
        response.check_returncode()
    release = json.loads(response.stdout)
    if release.get('draft') or release.get('prerelease') or not release.get('published_at'):
        return None
    tag = release['tag_name']
    if not STABLE_TAG.fullmatch(tag):
        raise ValueError(f'Unsupported upstream release tag: {tag!r}')
    return tag


def sync(upstream, mode, repository):
    release_tag = None
    if mode == 'release':
        release_tag = latest_release(repository)
        if not release_tag:
            summary('No published stable upstream release; nothing to sync or build.')
            return
        version = tuple(map(int, STABLE_TAG.fullmatch(release_tag).groups()))
        fork_tags = stable_tags('origin')
        if fork_tags and version <= max(fork_tags):
            summary(f'Upstream release {release_tag} is already covered; no sync or image build triggered.')
            return

    git('config', 'user.name', 'github-actions[bot]')
    git('config', 'user.email', '41898282+github-actions[bot]@users.noreply.github.com')
    git('fetch', '--no-tags', 'origin', 'milesians')
    git('merge', '--ff-only', 'FETCH_HEAD')
    source_ref = f'refs/tags/{release_tag}' if release_tag else 'refs/heads/main'
    # Never copy upstream tags directly: fork release tags include custom commits.
    git('fetch', '--no-tags', upstream, source_ref)
    upstream_commit = git('rev-parse', 'FETCH_HEAD^{commit}')

    if not is_ancestor(upstream_commit, 'HEAD'):
        try:
            git('merge', '--no-edit', upstream_commit)
        except subprocess.CalledProcessError:
            conflicts = git('diff', '--name-only', '--diff-filter=U')
            summary('Upstream merge failed; resolve conflicts manually.\n```\n' + conflicts + '\n```')
            subprocess.run(['git', 'merge', '--abort'], check=False)
            raise

    refs = ['HEAD:refs/heads/milesians']
    if release_tag:
        git('tag', '-a', release_tag, 'HEAD', '-m',
            f'Merge upstream release {release_tag} into milesians\n\nUpstream commit: {upstream_commit}')
        refs.append(f'refs/tags/{release_tag}')
    # Publish the branch and its release tag together, without force-pushing.
    git('push', '--atomic', 'origin', *refs)
    summary(f'milesians contains upstream {source_ref} ({upstream_commit}).')
    if release_tag:
        summary(f'Created {release_tag} on milesians; its tag push triggers the Release workflow.')
    else:
        summary('Weekly main sync does not create release tags or trigger image builds.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode', choices=('main', 'release'), required=True)
    parser.add_argument('--upstream', default='https://github.com/Wei-Shaw/sub2api.git')
    parser.add_argument('--upstream-repo', default='Wei-Shaw/sub2api')
    args = parser.parse_args()
    sync(args.upstream, args.mode, args.upstream_repo)
