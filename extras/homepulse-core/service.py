"""Private HTTP gateway to a fresh, offline native child per sensor-only job.

No native code is loaded into this HTTP process. Do not publish this service's
port: route through the authenticated FamilOS backend on a private network.
"""
import hashlib
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import threading

from worker import LIBRARY_SHA256, MAX_INPUT_BYTES, MAX_RECORDS, _records

MAX_RESULT_BYTES = 32_000_000
_JOB_LOCK = threading.Lock()


def failure(status, message, *, digest=None, count=0):
    return {'status': status, 'error': message, 'process_true': False,
            'source_input_sha256': digest, 'input_record_count': count,
            'processed_record_count': 0, 'spo2_outputs': [], 'mapped_temperatures': [],
            'mapped_sensor_records': [], 'deinit_returned': False,
            'published_as_valid_measurement': False, 'medical_validation_claimed': False,
            'user_profile_used': False}


def sensor_payload(value):
    if not isinstance(value, dict) or set(value) != {'records'}:
        raise ValueError('Body must contain only records: a list of sensor raw-hex strings')
    data = {'schema': 'standard-ring-events-raw-hex-v1', 'records': value['records']}
    frames = _records(data)  # Pure validation, never loads the ELF.
    data['records'] = [frame[:frame[1] + 2].hex() for frame in frames]
    payload = json.dumps(data, separators=(',', ':')).encode()
    if len(payload) > MAX_INPUT_BYTES:
        raise ValueError('Sensor input exceeds4MB')
    return payload


def _component(path, *, expected_hash=None):
    candidate = Path(path)
    if not candidate.is_absolute() or candidate.is_symlink() or not candidate.is_file():
        raise ValueError('Native component must be an existing absolute regular file')
    if candidate.stat().st_size > 10_000_000:
        raise ValueError('Native component exceeds the verified bound')
    digest = hashlib.sha256(candidate.read_bytes()).hexdigest()
    if expected_hash is not None and digest != expected_hash:
        raise ValueError('Native library hash does not match the reviewed APK')
    return candidate, digest


def configuration_status():
    try:
        library, library_hash = _component(os.getenv('HOME_PULSE_NATIVE_LIBRARY', '/native/libappecore-compat.so'), expected_hash=LIBRARY_SHA256)
        shim, shim_hash = _component(os.getenv('HOME_PULSE_NATIVE_SHIM', '/compat/libx.so'))
        return {'configured': True, 'architecture': 'x86_64', 'expected_version': [3, 6, 2],
                'library_sha256': library_hash, 'shim_sha256': shim_hash,
                'native_loaded_in_http_parent': False, 'seccomp_required_for_process': True}
    except (OSError, ValueError):
        return {'configured': False, 'native_loaded_in_http_parent': False,
                'error': 'Private native library/shim missing or not verified'}


def process_records(value):
    try:
        payload = sensor_payload(value)
    except (ValueError, TypeError):
        return failure('invalid_input', 'Expected1..65536 standard sensor records, maximum20bytes each; no user-profile records'), 400
    digest = hashlib.sha256(payload).hexdigest()
    count = len(value['records'])
    if not _JOB_LOCK.acquire(blocking=False):
        return failure('busy', 'A native job is already running', digest=digest, count=count), 503
    try:
        try:
            library, _ = _component(os.getenv('HOME_PULSE_NATIVE_LIBRARY', '/native/libappecore-compat.so'), expected_hash=LIBRARY_SHA256)
            shim, _ = _component(os.getenv('HOME_PULSE_NATIVE_SHIM', '/compat/libx.so'))
        except (OSError, ValueError):
            return failure('not_configured', 'Private native library/shim missing or not verified', digest=digest, count=count), 503
        # Only sensor input/output is written. No account/key/profile directory
        # is available to the native child, and no Docker socket is used.
        with tempfile.TemporaryDirectory(prefix='homepulse-core-job-') as temporary:
            job = Path(temporary)
            os.chmod(job, 0o700)
            source = job / 'records.json'
            with os.fdopen(os.open(source, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'wb') as output:
                output.write(payload)
                output.flush()
                os.fsync(output.fileno())
            destination = job / 'result.json'
            script = Path(__file__).resolve().with_name('worker.py')
            # Deliberately exclude service secrets and inherited loader hooks.
            environment = {'PATH': '/usr/local/bin:/usr/bin:/bin', 'LANG': 'C.UTF-8',
                           'PYTHONDONTWRITEBYTECODE': '1', 'LD_LIBRARY_PATH': str(shim.parent),
                           'HOME_PULSE_NATIVE_LIBRARY': str(library), 'HOME_PULSE_NATIVE_SHIM': str(shim)}
            command = [sys.executable, '-B', str(script), '--input', str(source), '--output', str(destination)]
            process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                       stderr=subprocess.DEVNULL, cwd=job, env=environment,
                                       close_fds=True, start_new_session=True)
            try:
                process.wait(timeout=30)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait(timeout=5)
                return failure('timeout', 'Native job exceeded30seconds', digest=digest, count=count), 504
            if not destination.is_file() or destination.is_symlink():
                return failure('native_job_failed', 'Native child did not produce a result', digest=digest, count=count), 502
            if destination.stat().st_size > MAX_RESULT_BYTES:
                return failure('output_limit', 'Native result exceeds32MB; previous valid result must be retained', digest=digest, count=count), 502
            result = json.loads(destination.read_bytes())
            if not isinstance(result, dict):
                return failure('invalid_result', 'Unexpected native result format', digest=digest, count=count), 502
            if result.get('status') == 'worker_error':
                # Error strings originate in our reviewed loader/validator,
                # never include request bytes, and cannot promote a measure.
                answer = failure('worker_error', result.get('error', 'Native child error'), digest=digest, count=count)
                answer['error_type'] = result.get('error_type')
                return answer, 502
            if result.get('source_input_sha256') != digest or result.get('input_record_count') != count:
                return failure('result_mismatch', 'Native result/input identity mismatch', digest=digest, count=count), 502
            if process.returncode != 0 or result.get('process_true') is not True or result.get('deinit_returned') is not True:
                result['process_true'] = False
                for key in ('spo2_outputs', 'mapped_temperatures', 'mapped_sensor_records'):
                    result[key] = []
            result['published_as_valid_measurement'] = False
            return result, 200 if process.returncode in (0, 2) else 502
    except (OSError, ValueError, subprocess.SubprocessError):
        return failure('native_job_failed', 'Local native job failed', digest=digest, count=count), 502
    finally:
        _JOB_LOCK.release()


class Handler(BaseHTTPRequestHandler):
    server_version = 'HomePulseCore/1'
    sys_version = ''

    def setup(self):
        super().setup()
        self.connection.settimeout(10)

    def log_message(self, format, *arguments):
        pass  # No URL, credentials or sensor payload in default access logs.

    def _send(self, status, result):
        payload = json.dumps(result, allow_nan=False, separators=(',', ':')).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(payload)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Connection', 'close')
        self.end_headers()
        self.wfile.write(payload)
        self.close_connection = True

    def do_GET(self):
        if self.path != '/health':
            self._send(404, failure('not_found', 'Unknown endpoint'))
            return
        result = configuration_status()
        self._send(200 if result['configured'] else 503, result)

    def do_POST(self):
        if self.path != '/process':
            self._send(404, failure('not_found', 'Unknown endpoint'))
            return
        token = os.getenv('HOME_PULSE_CORE_TOKEN')
        if token and not hmac.compare_digest(self.headers.get('Authorization', ''), 'Bearer ' + token):
            self._send(401, failure('unauthorized', 'Private service token required'))
            return
        if self.headers.get('Transfer-Encoding'):
            self._send(411, failure('length_required', 'Use a bounded Content-Length'))
            return
        try:
            length = int(self.headers.get('Content-Length', ''))
            if not 1 <= length <= MAX_INPUT_BYTES:
                self._send(413, failure('input_limit', 'Request body must fit within4MB'))
                return
            body = self.rfile.read(length)
            if len(body) != length:
                raise ValueError('Incomplete body')
            value = json.loads(body)
        except (ValueError, OSError):
            self._send(400, failure('invalid_json', 'Expected a complete bounded JSON body'))
            return
        result, status = process_records(value)
        self._send(status, result)


class PrivateServer(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 8
    _requests = threading.BoundedSemaphore(4)

    def process_request(self, request, client_address):
        if not self._requests.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self._requests.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._requests.release()


if __name__ == '__main__':
    address = os.getenv('HOME_PULSE_CORE_BIND', '0.0.0.0')
    port = int(os.getenv('HOME_PULSE_CORE_PORT', '8081'))
    PrivateServer((address, port), Handler).serve_forever()
