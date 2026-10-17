#!/usr/bin/env python3
"""Build the latest stable upstream plus a checked, opt-in XHTTP patch."""
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


def api(path):
    return json.loads(run('gh', 'api', path, capture=True))


def save(plan):
    PLAN.write_text(json.dumps(plan, indent=2) + '\n', encoding='utf-8')


def prepare(tag):
    WORK.mkdir(exist_ok=True)
    release = api(f'repos/{UPSTREAM}/releases/tags/{tag}' if tag else f'repos/{UPSTREAM}/releases/latest')
    tag = release['tag_name']
    if release['draft'] or release['prerelease'] or not re.fullmatch(r'v\d+\.\d+\.\d+', tag):
        raise RuntimeError('Only official stable releases are accepted')
    run('git', 'fetch', '--no-tags', f'https://github.com/{UPSTREAM}.git', f'refs/tags/{tag}')
    upstream_sha = run('git', 'rev-parse', 'FETCH_HEAD^{commit}', capture=True)
    patch_files = sorted((ROOT / 'patches').rglob('*.patch'))
    patch_sha = hashlib.sha256(b''.join(p.relative_to(ROOT).as_posix().encode() + b'\0' + p.read_bytes() for p in patch_files)).hexdigest()
    version = f'{tag}-window.{upstream_sha[:8]}.{patch_sha[:8]}'
    existing = subprocess.run(['gh', 'api', f'repos/{FORK}/releases/tags/{version}'],
                              capture_output=True, text=True)
    if existing.returncode and '404' not in existing.stderr:
        raise RuntimeError(existing.stderr)
    needed = existing.returncode != 0
    plan = {'upstream_repository': UPSTREAM, 'upstream_tag': tag,
            'upstream_commit': upstream_sha, 'patch_sha256': patch_sha,
            'maintenance_commit': run('git', 'rev-parse', 'HEAD', capture=True),
            'version': version, 'build_needed': needed}
    save(plan)
    if path := os.environ.get('GITHUB_OUTPUT'):
        with open(path, 'a', encoding='utf-8') as output:
            output.write(f'build_needed={str(needed).lower()}\nversion={version}\n')
    if not needed:
        print(f'{version} already published; nothing to rebuild.', flush=True)
        return
    # Never reset or delete an existing checkout. Failed work is kept for inspection.
    if SOURCE.exists():
        raise RuntimeError(f'{SOURCE} already exists; inspect it or use a fresh clone')
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
    plan = json.loads(PLAN.read_text())
    if not plan['build_needed']:
        return
    env = dict(os.environ, CGO_ENABLED='0', GOTOOLCHAIN='local', GOFLAGS='-mod=readonly')
    run('go', 'test', '-tags', 'with_gvisor', './transport/xhttp', './adapter/outbound',
        './adapter', './hub/route', '-count=1', cwd=SOURCE, env=env)
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
    DIST.mkdir(exist_ok=True)
    if any(DIST.iterdir()):
        raise RuntimeError('dist is not empty; use a fresh clone to avoid mixing build outputs')
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
    (DIST / 'WINDOW-CORE.md').write_bytes((ROOT / 'WINDOW-CORE.md').read_bytes())
    checksums = ''.join(f'{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.name}\n'
                        for p in sorted(DIST.iterdir()) if p.is_file())
    (DIST / 'checksums.txt').write_text(checksums, encoding='utf-8')
    save(plan)


def publish():
    plan = json.loads(PLAN.read_text())
    if not plan['build_needed']:
        return
    version = plan['version']
    run('git', 'tag', version, plan['source_commit'])
    run('git', '-c', 'credential.helper=', '-c', 'credential.helper=!gh auth git-credential',
        'push', 'origin', f'refs/tags/{version}')
    notes = (f'Unofficial build of {UPSTREAM} {plan["upstream_tag"]}: configurable XHTTP H2 window and a unified-delay fallback fix. '
             'Window defaults are unchanged; no extra retries or timeout extensions. See WINDOW-CORE.md for setup and rollback.\n\n'
             f'Upstream: `{plan["upstream_commit"]}`\nPatch SHA256: `{plan["patch_sha256"]}`\n'
             f'Source: `{plan["source_commit"]}`\nCompiler: `{plan["go_version"]}`\n')
    notes_file = WORK / 'release-notes.md'
    notes_file.write_text(notes, encoding='utf-8')
    run('gh', 'release', 'create', version, '--repo', FORK, '--target', plan['source_commit'],
        '--title', f'{plan["upstream_tag"]} + XHTTP window and delay fixes', '--latest',
        '--notes-file', str(notes_file), *[str(p) for p in sorted(DIST.iterdir())])


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('prepare', 'build', 'publish'))
    parser.add_argument('--tag', default='')
    args = parser.parse_args()
    if args.tag and not re.fullmatch(r'v\d+\.\d+\.\d+', args.tag):
        parser.error('--tag must be an official stable vX.Y.Z tag')
    if args.action == 'prepare': prepare(args.tag)
    elif args.action == 'build': build()
    else: publish()
