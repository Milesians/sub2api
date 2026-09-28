#!/usr/bin/env python3
"""Discover upstream release PRs, then process them in a separate trusted workflow."""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import time

UPSTREAM = 'Wei-Shaw/sub2api'
UPSTREAM_URL = f'https://github.com/{UPSTREAM}.git'
VERSION = re.compile(r'v(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)')
MARKER = re.compile(r'<!-- upstream-sync: (\{[^\n]+\}) -->')


def run(*args, input=None):
    return subprocess.check_output(args, input=input, text=True).strip()


def git(*args, **kwargs):
    return run('git', *args, **kwargs)


def repo():
    return os.environ.get('GITHUB_REPOSITORY', 'Milesians/sub2api')


def api(path, data=None):
    args = ['gh', 'api', path]
    if data is not None:
        args += ['--method', 'POST', '--input', '-']
    return json.loads(run(*args, input=json.dumps(data) if data is not None else None))


def summary(message):
    print(message)
    if path := os.environ.get('GITHUB_STEP_SUMMARY'):
        with Path(path).open('a') as output:
            output.write(message + '\n')


def output(name, value):
    if path := os.environ.get('GITHUB_OUTPUT'):
        with Path(path).open('a') as stream:
            stream.write(f'{name}={value}\n')


def identity():
    git('config', 'user.name', 'github-actions[bot]')
    git('config', 'user.email', '41898282+github-actions[bot]@users.noreply.github.com')


def ancestor(older, newer):
    result = subprocess.run(['git', 'merge-base', '--is-ancestor', older, newer])
    if result.returncode not in (0, 1):
        result.check_returncode()
    return result.returncode == 0


def covered(tag):
    version = tuple(map(int, VERSION.fullmatch(tag).groups()))
    for line in git('ls-remote', '--refs', '--tags', 'origin', 'refs/tags/v*').splitlines():
        name = line.split()[1].removeprefix('refs/tags/')
        match = VERSION.fullmatch(name)
        if match and tuple(map(int, match.groups())) >= version:
            return True
    return False


def release_info(tag='latest'):
    endpoint = 'latest' if tag == 'latest' else f'tags/{tag}'
    response = subprocess.run(['gh', 'api', f'repos/{UPSTREAM}/releases/{endpoint}'],
                              text=True, capture_output=True)
    if response.returncode:
        try:
            if str(json.loads(response.stdout).get('status')) == '404':
                return None
        except (ValueError, AttributeError):
            pass
        response.check_returncode()
    release = json.loads(response.stdout)
    if release.get('draft') or release.get('prerelease') or not release.get('published_at'):
        return None
    if not VERSION.fullmatch(release['tag_name']):
        raise ValueError('Upstream release must use a stable vX.Y.Z version')
    return release


def branch_for(source):
    return f"sync/upstream-release-{source['tag']}"


def dispatch(number):
    run('gh', 'workflow', 'run', 'upstream-pr.yml', '-R', repo(), '--ref', 'milesians',
        '-f', f'pr_number={number}')


def discover(upstream=UPSTREAM_URL):
    release = release_info()
    if not release or covered(release['tag_name']):
        summary('No new published upstream release; no PR or image build needed.')
        return
    tag = release['tag_name']
    git('fetch', '--no-tags', upstream, f'refs/tags/{tag}')
    sha = git('rev-parse', 'FETCH_HEAD^{commit}')
    source = {'repository': UPSTREAM, 'mode': 'release', 'tag': tag, 'sha': sha}
    branch = branch_for(source)
    prs = json.loads(run('gh', 'pr', 'list', '-R', repo(), '--base', 'milesians', '--head', branch,
                         '--state', 'all', '--json', 'number,state'))
    if prs:
        pr = prs[0]
        if pr['state'] != 'CLOSED':
            dispatch(pr['number'])
        summary(f"Existing sync PR #{pr['number']}: {pr['state']}; no duplicate created.")
        return
    if git('ls-remote', '--heads', 'origin', f'refs/heads/{branch}'):
        # Recover if the preceding run pushed the branch but PR creation failed.
        git('fetch', '--no-tags', 'origin', branch)
        if not ancestor(sha, 'FETCH_HEAD'):
            raise ValueError('Existing sync branch has a different upstream source')
    else:
        identity()
        git('checkout', '--detach', sha)
        # The receipt gives every release its own reviewable PR, and its commit (unlike
        # upstream's "[skip ci]" VERSION commits) triggers CI when the deploy key pushes it.
        # A unique file per release avoids an add/add conflict on the next release.
        receipt = Path('.github/upstream-releases') / f'{tag}.json'
        receipt.parent.mkdir(parents=True, exist_ok=True)
        receipt.write_text(json.dumps(source, indent=2) + '\n')
        git('add', str(receipt))
        git('commit', '-m', f'chore: track upstream release {tag}')
        git('push', 'origin', f'HEAD:refs/heads/{branch}')
    body = (f"Source: https://github.com/{UPSTREAM}\n\n"
            f"Upstream release {tag}: `{sha}`\n\n"
            f"<!-- upstream-sync: {json.dumps(source)} -->\n\n"
            'Clean sync PRs are merged automatically after checks. Conflicts receive one Codex attempt '
            'and then require human review.\n'
            f"\n## Upstream release notes\n\n{(release.get('body') or '')[:50000]}\n")
    pr = api(f'repos/{repo()}/pulls', {
        'title': f"Sync upstream {tag} into milesians", 'head': branch,
        'base': 'milesians', 'body': body,
    })
    api(f"repos/{repo()}/issues/{pr['number']}/labels", {'labels': ['upstream-sync', 'upstream-release']})
    # Explicit dispatch also works when the PR was created with GITHUB_TOKEN.
    dispatch(pr['number'])
    summary(f"Created sync PR #{pr['number']} from {UPSTREAM}.")


def source_for(pr):
    labels = {item['name'] for item in pr['labels']}
    match = MARKER.search(pr.get('body') or '')
    if ('upstream-sync' not in labels or not match or pr['user']['login'] != 'github-actions[bot]'
            or pr['base']['ref'] != 'milesians' or not pr['head'].get('repo')
            or pr['head']['repo']['full_name'] != repo()):
        return None
    source = json.loads(match.group(1))
    if (source.get('repository') != UPSTREAM or source.get('mode') != 'release'
            or not re.fullmatch(r'[0-9a-f]{40}', source.get('sha', ''))):
        raise ValueError('Invalid upstream PR source')
    if not VERSION.fullmatch(source.get('tag', '')):
        raise ValueError('Invalid upstream release version')
    if pr['head']['ref'] != branch_for(source) or 'upstream-release' not in labels:
        raise ValueError('PR branch and source labels do not match')
    return source


def wait_for_checks(head, attempts=20, interval=15):
    # Pushed CI can take a moment to register; `gh pr checks` fails on "no checks reported".
    # Require push-triggered runs: skipped pull_request_target jobs also leave check runs.
    for _ in range(attempts):
        if (api(f'repos/{repo()}/actions/runs?head_sha={head}&event=push&per_page=1')['total_count']
                and api(f'repos/{repo()}/commits/{head}/check-runs?per_page=1')['total_count']):
            return
        time.sleep(interval)
    raise ValueError(f'No CI checks appeared on {head}; refusing to merge unverified code')


def publish_tag(source, commit):
    if covered(source['tag']):
        return
    release = release_info(source['tag'])
    if not release:
        raise ValueError('The upstream release is no longer published')
    git('fetch', '--no-tags', 'origin', 'milesians')
    if not ancestor(commit, 'FETCH_HEAD'):
        raise ValueError('Release commit is not on milesians')
    identity()
    git('tag', '-a', source['tag'], commit, '-F', '-',
        input=f"Upstream release {source['tag']}\n\n{release.get('body') or ''}\n")
    git('push', 'origin', f"refs/tags/{source['tag']}")
    summary(f"Created {source['tag']} on milesians; Release will build the matching image version.")


def process(number):
    pr = api(f'repos/{repo()}/pulls/{number}')
    source = source_for(pr)
    if source is None:
        summary('Not a trusted upstream sync PR; no action taken.')
        return
    git('fetch', '--no-tags', 'origin', 'milesians', pr['head']['ref'])
    if pr['merged']:
        publish_tag(source, pr['merge_commit_sha'])
        return
    if pr['state'] != 'open':
        return
    labels = {item['name'] for item in pr['labels']}
    if 'codex-review-required' in labels:
        summary(f'PR #{number} awaits human review; it will not be merged automatically.')
        return
    head = pr['head']['sha']
    if not ancestor(source['sha'], head):
        raise ValueError('PR no longer contains its recorded upstream source')
    identity()
    git('checkout', '--detach', 'origin/milesians')
    merge = subprocess.run(['git', 'merge', '--no-ff', '--no-commit', head])
    if merge.returncode:
        conflicts = git('diff', '--name-only', '--diff-filter=U')
        subprocess.run(['git', 'merge', '--abort'], check=False)
        if not conflicts:
            merge.check_returncode()
        api(f'repos/{repo()}/issues/{number}/labels', {'labels': ['codex-review-required', 'codex-attempted']})
        if 'codex-attempted' not in labels:
            output('conflict_pr', number)
        summary(f'PR #{number} has conflicts; Codex runs at most once automatically, then human review is required.')
        return
    # No branch protection is assumed: wait for the actual PR checks explicitly.
    wait_for_checks(head)
    run('gh', 'pr', 'checks', str(number), '-R', repo(), '--watch', '--fail-fast', '--interval', '20')
    # The PR may have changed while checks ran. Never merge an unverified head.
    current = api(f'repos/{repo()}/pulls/{number}')
    if current['head']['sha'] != head or current['state'] != 'open':
        raise ValueError('PR changed during validation; retry with its new head')
    git('commit', '-m', f"Merge pull request #{number} from {repo().split('/')[0]}/{pr['head']['ref']}")
    commit = git('rev-parse', 'HEAD')
    git('push', 'origin', 'HEAD:refs/heads/milesians')
    summary(f'Merged PR #{number} after checks passed.')
    publish_tag(source, commit)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode', choices=('release', 'pr'), required=True)
    parser.add_argument('--upstream', default=UPSTREAM_URL)
    parser.add_argument('--pr-number', type=int)
    args = parser.parse_args()
    if args.mode == 'pr':
        if not args.pr_number or args.pr_number < 1:
            parser.error('--pr-number must be positive')
        process(args.pr_number)
    else:
        discover(args.upstream)
