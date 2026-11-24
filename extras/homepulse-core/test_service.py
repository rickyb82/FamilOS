import ctypes as C
import hashlib
import http.client
import json
import os
from pathlib import Path
import subprocess
import threading
import unittest
from unittest.mock import patch
from types import SimpleNamespace

import prepare_native
import sandbox
import service
import worker

ROW = '6f050100000060'


class ShapeTests(unittest.TestCase):
    def test_seccomp_program_denies_network_spawn_x32_and_wrong_arch(self):
        instructions = []

        class Filter(C.Structure):
            _fields_ = [('code', C.c_ushort), ('jt', C.c_ubyte), ('jf', C.c_ubyte), ('k', C.c_uint32)]

        class Program(C.Structure):
            _fields_ = [('length', C.c_ushort), ('instructions', C.POINTER(Filter))]

        def prctl(option, arg2, arg3, arg4, arg5):
            if option == 22:
                program = C.cast(arg3, C.POINTER(Program)).contents
                instructions.extend((item.code, item.jt, item.jf, item.k) for item in program.instructions[:program.length])
            return 0

        from unittest.mock import Mock
        library = SimpleNamespace(prctl=Mock(side_effect=prctl))
        with patch.object(sandbox.platform, 'system', return_value='Linux'), patch.object(sandbox.platform, 'machine', return_value='x86_64'), patch.object(sandbox.resource, 'setrlimit'), patch.object(sandbox.C, 'CDLL', return_value=library):
            self.assertIn('seccomp', sandbox.restrict_native_child())

        def verdict(number, arch=0xc000003e):
            accumulator, index = 0, 0
            while True:
                code, jt, jf, k = instructions[index]
                if code == 0x20:
                    accumulator = arch if k == 4 else number
                elif code == 0x15:
                    index += jt if accumulator == k else jf
                elif code == 0x45:
                    index += jt if accumulator & k else jf
                elif code == 0x06:
                    return k
                else:
                    self.fail('Unexpected BPF opcode')
                index += 1

        for number in (41, 42, 44, 46, 49, 53, 56, 57, 59, 288, 322, 425, 435, 0x40000001):
            self.assertEqual(verdict(number) & 0xffff0000, 0x00050000)
        self.assertEqual(verdict(1), 0x7fff0000)  # write remains available.
        self.assertEqual(verdict(1, 0xc00000b7), 0x80000000)

    def test_amd64_shapes(self):
        self.assertEqual(C.sizeof(worker.Record20), 20)
        self.assertEqual(C.alignment(worker.Record20), 4)
        self.assertEqual(C.sizeof(worker.TimePair), 16)
        self.assertEqual(worker.TimePair.utc_ms.offset, 8)
        self.assertEqual(C.sizeof(worker.SpO2Prefix), 40)
        self.assertEqual(worker.SpO2Prefix.average.offset, 24)
        self.assertEqual(worker.SpO2Prefix.quality.offset, 36)

    def test_payload_normalization_and_no_profile(self):
        payload = service.sensor_payload({'records': [ROW.upper()]})
        self.assertEqual(json.loads(payload)['records'], [ROW])
        for body in ({'records': ['5c0400000000']}, {'records': ['fd0400000000']}, {'records': []},
                     {'records': [ROW], 'age': 40}, {'records': ['6f0600000000']}):
            with self.assertRaises(ValueError):
                service.sensor_payload(body)

    def test_count_and_input_bounds_without_large_native_job(self):
        self.assertEqual(worker.MAX_RECORDS, 65536)
        self.assertLess(65536 * 43 + 100, worker.MAX_INPUT_BYTES)
        with patch.object(worker, 'MAX_RECORDS', 2):
            self.assertEqual(len(worker._records({'schema': 'standard-ring-events-raw-hex-v1', 'records': [ROW] * 2})), 2)
            with self.assertRaises(ValueError):
                worker._records({'schema': 'standard-ring-events-raw-hex-v1', 'records': [ROW] * 3})
        with patch.object(service, 'MAX_INPUT_BYTES', 1), self.assertRaises(ValueError):
            service.sensor_payload({'records': [ROW]})

    def test_wrong_original_hash_is_rejected_before_patching(self):
        with self.assertRaisesRegex(ValueError, 'Hash'):
            prepare_native.patch_library(b'not-the-reviewed-ELF')

    def test_mapped_motion_steps_and_breathing_only_use_existing_utc(self):
        for tag in (0x47, 0x50, 0x62):
            original = bytes([tag, 5, 1, 0, 0, 0, 99])
            result = worker._mapped_sensor_record(original + bytes(13), 1577836800000)
            self.assertEqual(result, {'record_sha256': hashlib.sha256(original).hexdigest(),
                                      'utc_ms': 1577836800000, 'tag_raw': tag})
        self.assertIsNone(worker._mapped_sensor_record(bytes.fromhex(ROW) + bytes(13), 1))

    def test_config_missing_does_not_start_native(self):
        with patch.object(service, '_component', side_effect=ValueError('missing')), patch.object(service.subprocess, 'Popen') as launch:
            result, status = service.process_records({'records': [ROW]})
            self.assertEqual((result['status'], status), ('not_configured', 503))
            launch.assert_not_called()


class JobTests(unittest.TestCase):
    def run_fake(self, *, success=True, mismatch=False, timeout=False):
        observations = {}

        class Process:
            pid = 4321234
            returncode = 0 if success else 2
            calls = 0

            def __init__(self, command, **kwargs):
                observations.update(command=command, **kwargs)
                source = Path(command[command.index('--input') + 1])
                destination = Path(command[command.index('--output') + 1])
                self.destination = destination
                observations['input_mode'] = source.stat().st_mode & 0o777
                observations['job_mode'] = source.parent.stat().st_mode & 0o777
                result = {'status': 'ok', 'process_true': success, 'deinit_returned': success,
                          'source_input_sha256': 'wrong' if mismatch else hashlib.sha256(source.read_bytes()).hexdigest(),
                          'input_record_count': 1, 'spo2_outputs': [{'average_raw': 999, 'quality_raw': 0.0}],
                          'mapped_temperatures': [{'utc_ms': 1}], 'mapped_sensor_records': [{'utc_ms': 1}]}
                destination.write_text(json.dumps(result))

            def wait(self, timeout):
                self.calls += 1
                if self.calls == 1 and observations.get('force_timeout'):
                    raise subprocess.TimeoutExpired('worker', timeout)
                return self.returncode

        observations['force_timeout'] = timeout
        component = lambda path, **kwargs: (Path('/private/lib.so' if kwargs else '/private/libx.so'), 'digest')
        with patch.object(service, '_component', side_effect=component), patch.object(service.subprocess, 'Popen', Process), patch.object(service.os, 'killpg') as kill, patch.dict(os.environ, {'HOME_PULSE_CORE_TOKEN': 'never-in-child', 'LD_PRELOAD': 'never-in-child'}):
            result, status = service.process_records({'records': [ROW]})
            observations['kill'] = kill.call_args
        return result, status, observations

    def test_child_environment_private_files_and_no_docker_or_fallback(self):
        result, status, observed = self.run_fake()
        self.assertEqual(status, 200)
        self.assertFalse(result['published_as_valid_measurement'])
        self.assertEqual(result['spo2_outputs'][0]['average_raw'], 999)  # No clamp or quality promotion.
        self.assertNotIn('HOME_PULSE_CORE_TOKEN', observed['env'])
        self.assertNotIn('LD_PRELOAD', observed['env'])
        self.assertTrue(observed['close_fds'])
        self.assertTrue(observed['start_new_session'])
        self.assertNotIn('--outer-offline-test', observed['command'])
        self.assertNotIn('docker', observed['command'])
        self.assertEqual(observed['input_mode'], 0o600)
        self.assertEqual(observed['job_mode'], 0o700)
        self.assertFalse(Path(observed['cwd']).exists())

    def test_failure_removes_all_measurement_outputs(self):
        result, status, _ = self.run_fake(success=False)
        self.assertFalse(result['process_true'])
        for field in ('spo2_outputs', 'mapped_temperatures', 'mapped_sensor_records'):
            self.assertEqual(result[field], [])

    def test_input_identity_mismatch_is_rejected(self):
        result, status, _ = self.run_fake(mismatch=True)
        self.assertEqual((result['status'], status), ('result_mismatch', 502))
        self.assertEqual(result['spo2_outputs'], [])

    def test_timeout_kills_only_its_process_group(self):
        result, status, observed = self.run_fake(timeout=True)
        self.assertEqual((result['status'], status), ('timeout', 504))
        self.assertEqual(observed['kill'].args[0], 4321234)
        self.assertFalse(service._JOB_LOCK.locked())


class HttpTests(unittest.TestCase):
    def setUp(self):
        self.server = service.PrivateServer(('127.0.0.1', 0), service.Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def request(self, method, path, body=None, headers=None):
        client = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=3)
        client.request(method, path, body=body, headers=headers or {})
        response = client.getresponse()
        result = response.status, json.loads(response.read())
        client.close()
        return result

    def test_health_does_not_load_native(self):
        with patch.object(service, 'configuration_status', return_value={'configured': True, 'native_loaded_in_http_parent': False}), patch.object(service, 'process_records') as run:
            status, result = self.request('GET', '/health')
            self.assertEqual(status, 200)
            self.assertFalse(result['native_loaded_in_http_parent'])
            run.assert_not_called()

    def test_private_token_and_request_limits(self):
        with patch.dict(os.environ, {'HOME_PULSE_CORE_TOKEN': 'local-test-only'}), patch.object(service, 'process_records') as run:
            self.assertEqual(self.request('POST', '/process', '{}')[0], 401)
            status, result = self.request('POST', '/process', headers={'Authorization': 'Bearer local-test-only', 'Content-Length': '4000001'})
            self.assertEqual((status, result['status']), (413, 'input_limit'))
            run.assert_not_called()

    def test_process_response_preserves_native_quality_raw(self):
        data = {'status': 'ok', 'process_true': True, 'spo2_outputs': [{'average_raw': 97, 'quality_raw': 0.0}], 'published_as_valid_measurement': False}
        with patch.dict(os.environ, {'HOME_PULSE_CORE_TOKEN': ''}), patch.object(service, 'process_records', return_value=(data, 200)):
            status, result = self.request('POST', '/process', json.dumps({'records': [ROW]}))
            self.assertEqual(status, 200)
            self.assertEqual(result['spo2_outputs'][0]['quality_raw'], 0.0)
            self.assertFalse(result['published_as_valid_measurement'])


if __name__ == '__main__':
    unittest.main()
