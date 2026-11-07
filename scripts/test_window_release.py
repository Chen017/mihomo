import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import window_release as release


class ReleaseRecipeTest(unittest.TestCase):
    def setUp(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        work = root / '.local'
        self.enterContext(patch.multiple(
            release, ROOT=root, WORK=work, SOURCE=work / 'source', DIST=root / 'dist',
            PLAN=work / 'plan.json', PATCHES=[],
        ))
        self.enterContext(patch.dict(os.environ, {'GITHUB_OUTPUT': ''}))
        self.upstream_commit = 'a' * 40
        self.plan = {
            'version': 'v1.19.32-window.aaaaaaaa.bbbbbbbb',
            'build_needed': True,
            'upstream_tag': 'v1.19.32',
            'upstream_commit': self.upstream_commit,
            'source_commit': 'b' * 40,
            'patch_sha256': 'c' * 64,
            'go_version': 'go version go1.26.8 linux/amd64',
        }

    def write_package(self):
        release.WORK.mkdir()
        release.DIST.mkdir()
        release.save(self.plan)
        payloads = release.release_payloads(self.plan['version'])
        for path in payloads:
            path.write_bytes(b'artifact')
        (release.DIST / 'provenance.json').write_text(json.dumps(self.plan), encoding='utf-8')
        (release.DIST / 'version.txt').write_text(self.plan['version'] + '\n', encoding='utf-8')
        (release.DIST / 'checksums.txt').write_text(release.checksums(payloads), encoding='utf-8')

    def test_published_input_skips_source_fetch(self):
        existing = subprocess.CompletedProcess([], 0, stdout='{"draft": false}', stderr='')
        upstream = {'tag_name': 'v1.19.32', 'draft': False, 'prerelease': False}
        with patch.object(release, 'api', return_value=upstream), \
                patch.object(release, 'run', side_effect=[self.upstream_commit, 'maintenance']) as run, \
                patch.object(release.subprocess, 'run', return_value=existing):
            release.prepare('')
        self.assertEqual([call.args[:2] for call in run.call_args_list], [('gh', 'api'), ('git', 'rev-parse')])
        self.assertFalse(json.loads(release.PLAN.read_text(encoding='utf-8'))['build_needed'])

    def test_changed_tag_stops_before_worktree(self):
        upstream = {'tag_name': 'v1.19.32', 'draft': False, 'prerelease': False}
        with patch.object(release, 'api', return_value=upstream), \
                patch.object(release, 'run', side_effect=[self.upstream_commit, 'maintenance', None, 'd' * 40]) as run:
            with self.assertRaisesRegex(RuntimeError, 'changed while preparing'):
                release.prepare('', verify=True)
        self.assertEqual(run.call_args.args, ('git', 'rev-parse', 'FETCH_HEAD^{commit}'))
        with self.assertRaisesRegex(RuntimeError, 'did not finish'):
            release.build()
        with self.assertRaisesRegex(RuntimeError, 'verification-only'):
            release.publish()

    def test_tampered_asset_stops_before_tag_or_release_changes(self):
        self.write_package()
        (release.DIST / 'version.txt').write_text('wrong version\n', encoding='utf-8')
        with patch.object(release, 'api') as api, patch.object(release, 'run') as run:
            with self.assertRaisesRegex(RuntimeError, 'do not match the build plan'):
                release.publish()
        api.assert_not_called()
        run.assert_not_called()

    def test_resumes_draft_without_recreating_it(self):
        self.write_package()
        draft = {'isDraft': True, 'releaseAssets': {
            'nodes': [{'name': 'version.txt'}], 'pageInfo': {'hasNextPage': False},
        }}
        response = {'data': {'repository': {'release': draft}}}
        with patch.object(release, 'api', return_value=response), patch.object(release, 'run') as run:
            release.publish()
        push, upload, edit = [call.args for call in run.call_args_list]
        self.assertEqual(push[-3:], (
            'push', f'https://github.com/{release.FORK}.git',
            f'{self.plan["source_commit"]}:refs/tags/{self.plan["version"]}',
        ))
        self.assertEqual(upload[:3], ('gh', 'release', 'upload'))
        self.assertIn('--clobber', upload)
        self.assertEqual(edit[:3], ('gh', 'release', 'edit'))
        self.assertIn('--draft=false', edit)

    def test_upload_failure_leaves_release_unpublished(self):
        self.write_package()
        draft = {'isDraft': True, 'releaseAssets': {
            'nodes': [], 'pageInfo': {'hasNextPage': False},
        }}
        response = {'data': {'repository': {'release': draft}}}
        with patch.object(release, 'api', return_value=response), \
                patch.object(release, 'run', side_effect=[None, subprocess.CalledProcessError(1, 'upload')]) as run:
            with self.assertRaises(subprocess.CalledProcessError):
                release.publish()
        _, upload = [call.args for call in run.call_args_list]
        self.assertEqual(upload[:3], ('gh', 'release', 'upload'))

    def test_new_release_uses_verified_tag_and_all_assets(self):
        self.write_package()
        response = {'data': {'repository': {'release': None}}}
        with patch.object(release, 'api', return_value=response), patch.object(release, 'run') as run:
            release.publish()
        _, create = [call.args for call in run.call_args_list]
        self.assertEqual(create[:3], ('gh', 'release', 'create'))
        self.assertIn('--verify-tag', create)
        self.assertIn('--latest', create)
        self.assertEqual(set(create[-6:]), {str(p) for p in release.DIST.iterdir()})


if __name__ == '__main__':
    unittest.main()
