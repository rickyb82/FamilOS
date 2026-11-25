"""Orbita installer tests: temporary files and mocked Docker only."""
from contextlib import ExitStack, redirect_stderr, redirect_stdout
import copy
import fcntl
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parents[1] / 'updater/orbita_support/wellness.py'
spec = importlib.util.spec_from_file_location('orbita_wellness', SOURCE)
wellness = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wellness)


def existing(networks=None):
    backend = {'image': 'orbita-backend:existing', 'ports': ['127.0.0.1:8000:8000'],
               'volumes': ['./apps/familos/data:/app/data'],
               'environment': {'SECRET_KEY': 'backend-secret-must-not-escape', 'OTHER': 'keep'},
               'depends_on': ['familos-sandbox']}
    if networks is not None:
        backend['networks'] = networks
    return {'services': {wellness.BACKEND: backend,
                         'proxy': {'image': 'caddy:existing', 'ports': ['80:80', '443:443']},
                         'database': {'image': 'db:existing', 'volumes': ['./data/db:/data']},
                         'familos-sandbox': {'image': 'sandbox:existing'}},
            'networks': {'existing_net': {'internal': False}}, 'volumes': {'precious_data': {}}}


class PlanTests(unittest.TestCase):
    def test_preserves_all_unrelated_services_ports_volumes_and_network_options(self):
        original = existing({'existing_net': {'aliases': ['backend-old'], 'ipv4_address': '10.8.0.2'}})
        source = copy.deepcopy(original)
        planned = wellness.plan(source, Path('/opt/orbita/apps/familos'))
        self.assertEqual(source, original)
        for name in ('proxy', 'database', 'familos-sandbox'):
            self.assertEqual(planned['services'][name], original['services'][name])
        backend = planned['services'][wellness.BACKEND]
        for key in ('ports', 'volumes', 'depends_on', 'image'):
            self.assertEqual(backend[key], original['services'][wellness.BACKEND][key])
        self.assertEqual(backend['networks']['existing_net'], original['services'][wellness.BACKEND]['networks']['existing_net'])
        self.assertTrue(planned['networks']['homepulse_net']['internal'])
        worker = planned['services'][wellness.WORKER]
        self.assertFalse(worker.get('ports'))
        self.assertNotIn('network_mode', worker)
        self.assertEqual(len(worker['volumes']), 1)
        self.assertTrue(worker['volumes'][0]['read_only'])
        self.assertNotIn('docker.sock', json.dumps(worker))
        self.assertNotIn('SECRET_KEY', worker['environment'])
        self.assertEqual(worker['user'], '10001:10001')
        self.assertEqual(worker['pids_limit'], 64)
        self.assertEqual(worker['mem_limit'], '512m')
        self.assertEqual(worker['cpus'], 1)
        self.assertEqual(worker['ulimits']['core'], 0)

    def test_network_list_and_implicit_default_are_preserved(self):
        planned = wellness.plan(existing(['existing_net']), Path('/app'))
        self.assertEqual(planned['services'][wellness.BACKEND]['networks'], ['existing_net', 'homepulse_net'])
        implicit = wellness.plan(existing(), Path('/app'))
        self.assertEqual(implicit['services'][wellness.BACKEND]['networks'], ['default', 'homepulse_net'])

    def test_token_preserved_across_repeat_plan_and_shared_exactly(self):
        configuration = existing()
        configuration['services'][wellness.BACKEND]['environment'] = ['OTHER=keep', 'HOME_PULSE_CORE_TOKEN=existing-private-token']
        with patch.object(wellness.secrets, 'token_urlsafe') as generate:
            first = wellness.plan(configuration, Path('/app'))
            second = wellness.plan(first, Path('/app'))
            generate.assert_not_called()
        self.assertEqual(first, second)
        self.assertEqual(first['services'][wellness.WORKER]['environment']['HOME_PULSE_CORE_TOKEN'], 'existing-private-token')

    def test_foreign_private_network_member_is_rejected_without_modifying_it(self):
        configuration = existing()
        configuration['services']['database']['networks'] = ['homepulse_net']
        with self.assertRaises(RuntimeError):
            wellness.plan(configuration, Path('/app'))
        self.assertEqual(configuration['services']['database']['networks'], ['homepulse_net'])


class InstallTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.app = self.root / 'apps/familos'
        self.context = self.app / 'extras/homepulse-core'
        self.context.mkdir(parents=True)
        (self.context / 'Dockerfile').write_text('FROM reviewed-local-image\n')
        fixture = Path(__file__).resolve().with_name('synthetic_fixture.json').read_bytes()
        (self.context / 'synthetic_fixture.json').write_bytes(fixture)
        (self.root / 'compose.yaml').write_text('{}')
        (self.root / '.env').write_text('COMPOSE_FILE="compose.yaml:compose.familos.yaml"\nCOMPOSE_PROJECT_NAME=orbita\nSECRET_KEY=must-not-be-forwarded\n')
        self.cfgpath = self.root / 'compose.familos.yaml'
        self.original = (json.dumps(existing(['existing_net']), indent=2) + '\n').encode()
        self.cfgpath.write_bytes(self.original)
        self.library = self.root / 'staged-safe.so'
        self.library.write_bytes(b'synthetic-library-not-executable')
        self.seed = self.root / 'seed.json'
        self.seed.write_text('{"owner_key":"NEVER_READ_OR_PRINT_THIS_KEY"}')
        self.seed.chmod(0o600)
        self.commands = []
        self.fail_probe = False
        self.fail_provision = False
        self.flat_report = False
        self.running = ['familos-backend', 'proxy', 'database']
        self.output = io.StringIO()

    def tearDown(self):
        self.temporary.cleanup()

    def docker(self, root, *args, **kwargs):
        self.commands.append((args, kwargs))
        stdout = ''
        code = 0
        if args and args[0] == 'build':
            self.assertEqual(self.cfgpath.read_bytes(), self.original)
        if 'ps' in args and '--services' in args:
            stdout = '\n'.join(self.running)
        elif args[:2] == ('ps', '-aq'):
            stdout = 'a' * 64
        elif 'ps' in args and '-q' in args and args[-1] == wellness.BACKEND:
            stdout = 'b' * 64
        elif wellness.WORKER_PROBE in args:
            stdout = json.dumps({'ok': not self.fail_probe})
            code = int(self.fail_probe)
        elif 'import json,os;print(json.dumps([os.getuid(),os.getgid()]))' in args:
            stdout = '[1000,1000]'
        elif 'app.services.homepulse.provision' in args:
            if self.fail_provision:
                raise RuntimeError('Importazione di prova fallita')
            stdout = json.dumps({'owner_name': 'Proprietario', 'events': 5, 'history_records': 9, 'weekly_sessions': 3, 'ring_configured': True}
                                if self.flat_report else {'owner_name': 'Proprietario', 'counts': {'rings': 1, 'records': 3, 'plans': 2,
                                                                                                  'SECRET_KEY': 'do-not-return'}})
        return subprocess.CompletedProcess(args, code, stdout, '')

    def install_context(self):
        stack = ExitStack()
        stack.enter_context(patch.object(wellness.os, 'geteuid', return_value=0))
        stack.enter_context(patch.object(wellness.os, 'chown'))
        stack.enter_context(patch.object(wellness.os, 'fchown'))
        stack.enter_context(patch.object(wellness, 'LIBRARY_SHA256', hashlib.sha256(self.library.read_bytes()).hexdigest()))
        stack.enter_context(patch.object(wellness, '_docker', side_effect=self.docker))
        stack.enter_context(redirect_stdout(self.output))
        return stack

    def test_build_and_worker_probe_precede_backend_recreation_and_seed_never_read(self):
        original_read = Path.read_bytes
        original_text = Path.read_text

        def read_bytes(path, *args, **kwargs):
            self.assertNotEqual(path, self.seed, 'Helper must never open the private seed')
            return original_read(path, *args, **kwargs)

        def read_text(path, *args, **kwargs):
            self.assertNotEqual(path, self.seed, 'Helper must never parse the private seed')
            return original_text(path, *args, **kwargs)

        with self.install_context(), patch.object(Path, 'read_bytes', read_bytes), patch.object(Path, 'read_text', read_text):
            result = wellness.install(self.library, bootstrap=self.seed, root=self.root)
        self.assertEqual(result, {'owner_name': 'Proprietario', 'counts': {'rings': 1, 'records': 3, 'plans': 2}})
        commands = [args for args, kwargs in self.commands]
        build = next(i for i, args in enumerate(commands) if args[0] == 'build')
        probe = next(i for i, args in enumerate(commands) if wellness.WORKER_PROBE in args)
        backend = next(i for i, args in enumerate(commands) if 'up' in args and args[-1] == wellness.BACKEND)
        provision = next(i for i, args in enumerate(commands) if 'app.services.homepulse.provision' in args)
        self.assertLess(build, probe)
        self.assertLess(probe, backend)
        self.assertLess(backend, provision)
        self.assertTrue(any(args[:2] == ('cp', str(self.seed)) for args in commands))
        self.assertIn('os.unlink("/tmp/homepulse-seed.json")', commands[-1][-1])
        self.assertNotIn('NEVER_READ_OR_PRINT_THIS_KEY', repr(commands) + self.output.getvalue() + repr(result))
        self.assertNotIn('backend-secret-must-not-escape', self.output.getvalue())
        self.assertEqual(self.cfgpath.stat().st_mode & 0o777, 0o600)
        target = self.app / 'private/homepulse/libappecore-compat.so'
        self.assertEqual(target.stat().st_mode & 0o777, 0o400)
        self.assertEqual(target.parent.stat().st_mode & 0o777, 0o700)
        snapshots = list(self.root.glob('wellness-*/compose.previous.json'))
        self.assertEqual(len(snapshots), 1)
        self.assertEqual(snapshots[0].read_bytes(), self.original)
        self.assertEqual(snapshots[0].stat().st_mode & 0o777, 0o600)

    def test_worker_failure_restores_config_without_recreating_backend_or_touching_other_containers(self):
        self.fail_probe = True
        with self.install_context(), self.assertRaises(RuntimeError):
            wellness.install(self.library, root=self.root)
        self.assertEqual(self.cfgpath.read_bytes(), self.original)
        commands = [args for args, kwargs in self.commands]
        self.assertFalse(any('up' in args and args[-1] == wellness.BACKEND for args in commands))
        self.assertEqual([args for args in commands if args[0] == 'rm'], [('rm', '-f', 'a' * 64)])
        self.assertFalse(any(args[0] in ('system', 'volume') or 'prune' in args for args in commands))
        self.assertFalse(any('stop' in args for args in commands))

    def test_failure_after_atomic_replace_still_restores_original_config(self):
        original_atomic = wellness._atomic
        triggered = False

        def fail_after_replace(path, content, **kwargs):
            nonlocal triggered
            original_atomic(path, content, **kwargs)
            if path == self.cfgpath and content != self.original and not triggered:
                triggered = True
                raise RuntimeError('Synthetic failure after replace')

        with self.install_context(), patch.object(wellness, '_atomic', side_effect=fail_after_replace), self.assertRaises(RuntimeError):
            wellness.install(self.library, root=self.root)
        self.assertTrue(triggered)
        self.assertEqual(self.cfgpath.read_bytes(), self.original)
        self.assertFalse(any('up' in args and args[-1] == wellness.BACKEND for args, kwargs in self.commands))

    def test_provision_failure_removes_seed_copy_and_restores_backend_config(self):
        self.fail_provision = True
        with self.install_context(), self.assertRaises(RuntimeError):
            wellness.install(self.library, bootstrap=self.seed, root=self.root)
        self.assertEqual(self.cfgpath.read_bytes(), self.original)
        commands = [args for args, kwargs in self.commands]
        self.assertTrue(any('os.unlink("/tmp/homepulse-seed.json")' in args[-1] for args in commands))
        self.assertEqual(sum('up' in args and args[-1] == wellness.BACKEND for args in commands), 2)
        self.assertTrue(self.seed.exists())  # Original private source is preserved.

    def test_flat_provision_report_keeps_only_whitelisted_integer_counts(self):
        self.flat_report = True
        with self.install_context():
            result = wellness.install(self.library, bootstrap=self.seed, root=self.root)
        self.assertEqual(result, {'owner_name': 'Proprietario', 'counts': {'events': 5, 'history_records': 9, 'weekly_sessions': 3}})

    def test_existing_worker_image_tag_is_preserved_for_rollback(self):
        old = wellness.plan(existing(), self.app)
        self.original = (json.dumps(old) + '\n').encode()
        self.cfgpath.write_bytes(self.original)
        self.running.append(wellness.WORKER)
        self.fail_probe = True
        with self.install_context(), self.assertRaises(RuntimeError):
            wellness.install(self.library, root=self.root)
        commands = [args for args, kwargs in self.commands]
        tags = [args for args in commands if args[:2] == ('image', 'tag')]
        self.assertEqual(len(tags), 2)
        self.assertEqual(tags[0][2], wellness.IMAGE)
        self.assertEqual(tags[1][3], wellness.IMAGE)
        self.assertEqual(tags[0][3], tags[1][2])
        self.assertEqual(self.cfgpath.read_bytes(), self.original)
        self.assertFalse(any(args[0] == 'rm' for args in commands))

    def test_nonroot_only_dry_run_and_no_docker_or_mutation(self):
        with patch.object(wellness.os, 'geteuid', return_value=501), patch.object(wellness, 'LIBRARY_SHA256', hashlib.sha256(self.library.read_bytes()).hexdigest()), patch.object(wellness, '_docker') as docker:
            with self.assertRaises(RuntimeError):
                wellness.install(self.library, root=self.root)
            result = wellness.install(self.library, root=self.root, dry_run=True)
            self.assertEqual(result['owner_name'], 'Proprietario')
            docker.assert_not_called()
        self.assertEqual(self.cfgpath.read_bytes(), self.original)
        self.assertFalse((self.root / '.migration.lock').exists())

    def test_shared_lock_blocks_before_any_docker_command(self):
        path = self.root / '.migration.lock'
        with path.open('w') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.install_context(), self.assertRaisesRegex(RuntimeError, 'operazione Orbita'):
                wellness.install(self.library, root=self.root)
        self.assertEqual(self.commands, [])

    def test_symlink_source_or_database_config_is_rejected(self):
        linked = self.root / 'unsafe.so'
        linked.symlink_to(self.library)
        with self.install_context(), self.assertRaises(RuntimeError):
            wellness.install(linked, root=self.root)
        self.assertEqual(self.commands, [])

    def test_docker_child_environment_does_not_inherit_app_secrets(self):
        with patch.dict(os.environ, {'SECRET_KEY': 'sensitive', 'HOME_PULSE_CORE_TOKEN': 'secret-token', 'LD_PRELOAD': 'evil', 'DOCKER_HOST': 'remote'}), patch.object(wellness.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, '', '')) as execute:
            wellness._docker(self.root, 'version')
        arguments, options = execute.call_args
        self.assertEqual(arguments[0][1:3], ['--host', 'unix:///var/run/docker.sock'])
        self.assertEqual(set(options['env']), {'PATH', 'HOME', 'LANG'})
        self.assertNotIn('sensitive', repr(options))


if __name__ == '__main__':
    unittest.main()
