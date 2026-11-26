"""Only synthetic worker responses; never run the native library or radio."""
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch
from urllib.error import HTTPError

SOURCE=Path(__file__).resolve().parents[1]/'updater/orbita_support/wellness.py'
spec=importlib.util.spec_from_file_location('wellness_probe_test',SOURCE)
wellness=importlib.util.module_from_spec(spec);spec.loader.exec_module(wellness)
HEALTH={'configured':True,'library_sha256':wellness.LIBRARY_SHA256}
VALID={'process_true':True,'deinit_returned':True,'version':[3,6,2],'input_record_count':16,'mapping':{'complete':True,'record_identity_verified':True},'sleep_native_abi':'ecore-3.6.2-linux-x86_64-sleep-output-200-v1','sleep_capture_complete':True,'sleep_outputs':[],'sensor_mapping_abi':'native-mapped-sensors-47-50-62-6a-v2','gen3_sleep':{'contract':'gen3-bedtime76-native-clock-v1','periods':[]}}

def response(value):return io.BytesIO(json.dumps(value).encode())
def error(code,status):return HTTPError('http://127.0.0.1:8081/process',code,'synthetic',{},response({'status':status}))

class ProbeTests(unittest.TestCase):
    def run_probe(self,replies):
        out=io.StringIO();payload=Path(__file__).with_name('synthetic_fixture.json').read_text()
        with patch.dict(os.environ,{'HOME_PULSE_CORE_TOKEN':'synthetic-token-never-display'}),patch('sys.stdin',io.StringIO(payload)),patch('urllib.request.urlopen',side_effect=replies) as transport,patch('time.sleep') as sleep,redirect_stdout(out):
            try:exec(wellness.WORKER_PROBE,{})
            except SystemExit as exc:self.assertEqual(exc.code,1)
        self.assertNotIn('synthetic-token-never-display',out.getvalue())
        return json.loads(out.getvalue()),transport.call_count,sleep.call_count

    def test_busy_worker_is_retried_without_weakening_the_native_contract(self):
        result,calls,sleeps=self.run_probe([response(HEALTH),error(503,'busy'),response(VALID)])
        self.assertTrue(result['ok']);self.assertEqual(calls,3);self.assertEqual(sleeps,1)

    def test_busy_retry_budget_terminates_without_a_false_success(self):
        result,calls,sleeps=self.run_probe([response(HEALTH)]+[error(503,'busy') for _ in range(40)])
        self.assertFalse(result['ok']);self.assertEqual(result['reason'],'busy_timeout')
        self.assertEqual(calls,41);self.assertEqual(sleeps,40)

    def test_non_busy_http_error_is_reported_without_retries_or_secrets(self):
        result,calls,sleeps=self.run_probe([response(HEALTH),error(401,'unauthorized')])
        self.assertFalse(result['ok']);self.assertEqual(result['http_status'],401)
        self.assertEqual(calls,2);self.assertEqual(sleeps,0)

    def test_native_sleep_contract_failure_is_still_rejected(self):
        result,_,_=self.run_probe([response(HEALTH),response({**VALID,'sleep_capture_complete':False})])
        self.assertFalse(result['ok']);self.assertEqual(result['reason'],'native_contract')
        self.assertFalse(result['checks']['sleep_capture_complete'])

    def test_outer_error_only_contains_whitelisted_technical_metadata(self):
        report={'ok':False,'reason':'http','http_status':503,'worker_status':'not_configured','token':'do-not-display'}
        with patch.object(wellness,'_dc',return_value=subprocess.CompletedProcess([],1,json.dumps(report),'')):
            with self.assertRaises(RuntimeError) as failure:wellness._probe(Path('/unused'),'{}')
        self.assertIn('not_configured',str(failure.exception));self.assertNotIn('do-not-display',str(failure.exception))
