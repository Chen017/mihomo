#!/usr/bin/env python3
"""Build the latest stable upstream plus checked XHTTP, provider-dialer and 0-RTT fixes."""
import argparse
import datetime
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import zipfile

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / '.local'
SOURCE = WORK / 'source'
DIST = ROOT / 'dist'
PLAN = WORK / 'plan.json'
PATCHES = sorted((ROOT / 'patches').glob('*.patch'))
UPSTREAM = 'MetaCubeX/mihomo'
FORK = 'Chen017/mihomo'


def run(*args, cwd=ROOT, capture=False, env=None):
    result = subprocess.run(args, cwd=cwd, env=env, check=True,
                            stdout=subprocess.PIPE if capture else None, text=True)
    return result.stdout.strip() if capture else None


def api(*args):
    return json.loads(run('gh', 'api', *args, capture=True))


def save(plan):
    PLAN.write_text(json.dumps(plan, indent=2) + '\n', encoding='utf-8')


def prepare(tag, verify=False):
    WORK.mkdir(exist_ok=True)
    release = api(f'repos/{UPSTREAM}/releases/tags/{tag}' if tag else f'repos/{UPSTREAM}/releases/latest')
    tag = release['tag_name']
    if release['draft'] or release['prerelease'] or not re.fullmatch(r'v\d+\.\d+\.\d+', tag):
        raise RuntimeError('Only official stable releases are accepted')
    upstream_sha = run('gh', 'api', '-H', 'Accept: application/vnd.github.sha',
                       f'repos/{UPSTREAM}/commits/tags/{tag}', capture=True)
    if not re.fullmatch(r'[0-9a-f]{40}', upstream_sha):
        raise RuntimeError(f'GitHub did not return a commit SHA for {tag}')
    patch_files = sorted((ROOT / 'patches').rglob('*.patch'))
    patch_sha = hashlib.sha256(b''.join(p.relative_to(ROOT).as_posix().encode() + b'\0' + p.read_bytes() for p in patch_files)).hexdigest()
    version = f'{tag}-window.{upstream_sha[:8]}.{patch_sha[:8]}'
    needed = True
    if not verify:
        existing = subprocess.run(['gh', 'api', f'repos/{FORK}/releases/tags/{version}'],
                                  capture_output=True, text=True)
        if existing.returncode and '(HTTP 404)' not in existing.stderr and '(HTTP 404)' not in existing.stdout:
            raise RuntimeError(existing.stderr or existing.stdout)
        needed = existing.returncode != 0 or json.loads(existing.stdout)['draft']
    plan = {'upstream_repository': UPSTREAM, 'upstream_tag': tag,
            'upstream_commit': upstream_sha, 'patch_sha256': patch_sha,
            'maintenance_commit': run('git', 'rev-parse', 'HEAD', capture=True),
            'version': version, 'build_needed': needed, 'verification_only': verify}
    save(plan)
    if (path := os.environ.get('GITHUB_OUTPUT')) and not verify:
        with open(path, 'a', encoding='utf-8') as output:
            output.write(f'build_needed={str(needed).lower()}\nversion={version}\n')
    if not needed:
        print(f'{version} already published; nothing to rebuild.', flush=True)
        return
    # Never reset or delete an existing checkout. Failed work is kept for inspection.
    if SOURCE.exists():
        raise RuntimeError(f'{SOURCE} already exists; inspect it or use a fresh clone')
    run('git', 'fetch', '--no-tags', f'https://github.com/{UPSTREAM}.git', f'refs/tags/{tag}')
    if run('git', 'rev-parse', 'FETCH_HEAD^{commit}', capture=True) != upstream_sha:
        raise RuntimeError(f'Upstream tag {tag} changed while preparing the build; retry')
    run('git', 'worktree', 'add', '--detach', str(SOURCE), upstream_sha)
    plan['patches'] = []
    paths = set()
    for patch in PATCHES:
        candidates = [patch, *sorted((ROOT / 'patches' / 'compat').glob(patch.stem + '-*.patch'))]
        selected = next((p for p in candidates if subprocess.run(['git', 'apply', '--check', str(p)], cwd=SOURCE, capture_output=True).returncode == 0), None)
        present = next((p for p in candidates if subprocess.run(['git', 'apply', '--reverse', '--check', str(p)], cwd=SOURCE, capture_output=True).returncode == 0), None)
        if selected is not None:
            run('git', 'apply', str(selected), cwd=SOURCE)
            mode = 'local patch'
        elif present is not None:
            selected = present
            mode = 'already present upstream'
        else:
            # A conflicted merge exits nonzero and stops this run before tests/build/publish.
            run('git', 'apply', '--3way', str(patch), cwd=SOURCE)
            selected = patch
            mode = 'clean three-way merge'
        for line in run('git', 'apply', '--numstat', str(selected), cwd=SOURCE, capture=True).splitlines():
            paths.add(line.split('\t', 2)[2])
        plan['patches'].append({'name': selected.relative_to(ROOT).as_posix(), 'sha256': hashlib.sha256(selected.read_bytes()).hexdigest(), 'mode': mode})
    plan['modified_paths'] = sorted(paths)
    save(plan)
    print(f'Prepared {version}: {plan["patches"]}.', flush=True)


def build():
    plan = json.loads(PLAN.read_text(encoding='utf-8'))
    if not plan['build_needed']:
        return
    if 'modified_paths' not in plan:
        raise RuntimeError('Patch preparation did not finish; inspect the prepare log')
    DIST.mkdir(exist_ok=True)
    if any(DIST.iterdir()):
        raise RuntimeError('dist is not empty; use a fresh clone to avoid mixing build outputs')
    env = dict(os.environ, CGO_ENABLED='0', GOTOOLCHAIN='local', GOFLAGS='-mod=readonly')
    run('go', 'test', '-tags', 'with_gvisor', './transport/xhttp', './adapter/outbound',
        './adapter', './hub/route', './adapter/provider', './component/proxydialer',
        './transport/vless/encryption', './config', '-count=1', cwd=SOURCE, env=env)
    run('git', 'add', '--', *plan['modified_paths'], cwd=SOURCE)
    changed = subprocess.run(['git', 'diff', '--cached', '--quiet'], cwd=SOURCE).returncode
    if changed not in (0, 1):
        raise RuntimeError('Unable to inspect patched source index')
    if changed:
        date = run('git', 'show', '-s', '--format=%cI', plan['upstream_commit'], cwd=SOURCE, capture=True)
        commit_env = dict(env, GIT_AUTHOR_DATE=date, GIT_COMMITTER_DATE=date)
        run('git', '-c', 'user.name=github-actions[bot]',
            '-c', 'user.email=41898282+github-actions[bot]@users.noreply.github.com',
            'commit', '-m', f'feat: apply maintained transport patches ({plan["upstream_tag"]})',
            cwd=SOURCE, env=commit_env)
    plan['source_commit'] = run('git', 'rev-parse', 'HEAD', cwd=SOURCE, capture=True)
    plan['go_version'] = run('go', 'version', cwd=SOURCE, capture=True)
    plan['build_time_utc'] = datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    flags = (f'-s -w -X github.com/metacubex/mihomo/constant.Version={plan["version"]} '
             f'-X github.com/metacubex/mihomo/constant.BuildTime={plan["build_time_utc"]}')
    binaries = WORK / 'binaries'
    binaries.mkdir(exist_ok=True)
    for goos in ('windows', 'linux'):
        binary = binaries / ('mihomo.exe' if goos == 'windows' else 'mihomo')
        target_env = dict(env, GOOS=goos, GOARCH='amd64', GOAMD64='v1')
        run('go', 'build', '-trimpath', '-tags', 'with_gvisor', '-ldflags', flags,
            '-o', str(binary), '.', cwd=SOURCE, env=target_env)
        if goos == 'windows':
            with zipfile.ZipFile(DIST / f'mihomo-windows-amd64-{plan["version"]}.zip', 'w', zipfile.ZIP_DEFLATED) as archive:
                archive.write(binary, 'mihomo.exe')
        else:
            with binary.open('rb') as source, gzip.open(DIST / f'mihomo-linux-amd64-{plan["version"]}.gz', 'wb') as output:
                while block := source.read(1024 * 1024):
                    output.write(block)
            if os.name != 'nt':
                binary.chmod(0o755)
                reported = run(str(binary), '-v', cwd=SOURCE, capture=True)
                if plan['version'] not in reported:
                    raise RuntimeError('Built core reports the wrong version')
    (DIST / 'provenance.json').write_text(json.dumps(plan, indent=2) + '\n', encoding='utf-8')
    (DIST / 'version.txt').write_text(plan['version'] + '\n', encoding='utf-8')
    (DIST / 'WINDOW-CORE.md').write_bytes((ROOT / 'WINDOW-CORE.md').read_bytes())
    (DIST / 'checksums.txt').write_text(checksums(release_payloads(plan['version'])), encoding='utf-8')
    save(plan)


def release_payloads(version):
    return sorted(DIST / name for name in (
        f'mihomo-windows-amd64-{version}.zip', f'mihomo-linux-amd64-{version}.gz',
        'provenance.json', 'version.txt', 'WINDOW-CORE.md',
    ))


def checksums(paths):
    lines = []
    for path in paths:
        with path.open('rb') as stream:
            digest = hashlib.file_digest(stream, 'sha256').hexdigest()
        lines.append(f'{digest}  {path.name}\n')
    return ''.join(lines)


def validate_release(plan):
    payloads = release_payloads(plan['version'])
    checksum_file = DIST / 'checksums.txt'
    assets = [*payloads, checksum_file]
    if ('source_commit' not in plan or not DIST.is_dir() or set(DIST.iterdir()) != set(assets)
            or any(not asset.is_file() or asset.stat().st_size == 0 for asset in assets)):
        raise RuntimeError('Build did not produce the complete release asset set')
    if (checksum_file.read_text(encoding='utf-8') != checksums(payloads)
            or json.loads((DIST / 'provenance.json').read_text(encoding='utf-8')) != plan
            or (DIST / 'version.txt').read_text(encoding='utf-8').strip() != plan['version']):
        raise RuntimeError('Release asset checksums, provenance or updater version do not match the build plan')
    return assets


def publish():
    plan = json.loads(PLAN.read_text(encoding='utf-8'))
    if plan.get('verification_only'):
        raise RuntimeError('A verification-only build cannot be published')
    if not plan['build_needed']:
        return
    version = plan['version']
    assets = validate_release(plan)
    # The REST lookup by tag only finds published releases. A failed upload can leave a draft.
    query = '''query($owner: String!, $name: String!, $tag: String!) {
      repository(owner: $owner, name: $name) {
        release(tagName: $tag) {
          isDraft
          releaseAssets(first: 20) { nodes { name } pageInfo { hasNextPage } }
        }
      }
    }'''
    owner, name = FORK.split('/')
    response = api('graphql', '-f', f'query={query}', '-f', f'owner={owner}', '-f', f'name={name}', '-f', f'tag={version}')
    release = response['data']['repository']['release']
    if release and not release['isDraft']:
        print(f'{version} already published; leaving the release intact.', flush=True)
        return
    if release:
        existing = release['releaseAssets']
        unexpected = {asset['name'] for asset in existing['nodes']} - {asset.name for asset in assets}
        if unexpected or existing['pageInfo']['hasNextPage']:
            raise RuntimeError('Draft contains unexpected assets; inspect it before retrying')
    # A push of the same commit is safe to retry. A different existing tag is rejected by Git.
    run('git', '-c', 'credential.helper=', '-c', 'credential.helper=!gh auth git-credential',
        'push', f'https://github.com/{FORK}.git', f'{plan["source_commit"]}:refs/tags/{version}')
    notes = (f'Unofficial build of {UPSTREAM} {plan["upstream_tag"]}: configurable XHTTP H2 window, independent download dialers, scoped provider chaining, 0-RTT ticket recovery and unified-delay fallback fixes. '
             'Window defaults are unchanged; no extra retries or timeout extensions. See WINDOW-CORE.md for setup and rollback.\n\n'
             f'Upstream: `{plan["upstream_commit"]}`\nPatch SHA256: `{plan["patch_sha256"]}`\n'
             f'Source: `{plan["source_commit"]}`\nCompiler: `{plan["go_version"]}`\n')
    notes_file = WORK / 'release-notes.md'
    notes_file.write_text(notes, encoding='utf-8')
    title = f'{plan["upstream_tag"]} + XHTTP NAT split and transport fixes'
    if not release:
        # With assets, gh uploads them to a draft before publishing the release.
        run('gh', 'release', 'create', version, '--repo', FORK, '--verify-tag', '--latest',
            '--title', title, '--notes-file', str(notes_file), *map(str, assets))
        return
    run('gh', 'release', 'upload', version, '--repo', FORK, '--clobber', *map(str, assets))
    run('gh', 'release', 'edit', version, '--repo', FORK, '--verify-tag', '--draft=false', '--latest',
        '--title', title, '--notes-file', str(notes_file))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('prepare', 'build', 'publish'))
    parser.add_argument('--tag', default='')
    parser.add_argument('--verify', action='store_true', help='Build an already released input locally without publishing')
    args = parser.parse_args()
    if args.tag and not re.fullmatch(r'v\d+\.\d+\.\d+', args.tag):
        parser.error('--tag must be an official stable vX.Y.Z tag')
    if args.action != 'prepare' and (args.tag or args.verify):
        parser.error('--tag and --verify apply only to prepare')
    if args.action == 'prepare': prepare(args.tag, args.verify)
    elif args.action == 'build': build()
    else: publish()
