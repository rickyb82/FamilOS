"""Offline ecore C ABI worker; launched only inside the restricted container.

Uses the user's APK-derived private library. No JNI, RF, keys, profile or
network. Typed SpO2 averages, native sleep periods/stages and native-mapped
sensor times leave this worker. This is not medical validation.
"""
import argparse
from collections import Counter
import ctypes as C
import hashlib
import json
import math
import os
import struct
from gen3_sleep import extract as extract_gen3_sleep

LIBRARY_SHA256 = '163e10037c93ef70bd939641e0d391441e74cb4dd339946cd0a5421b91703f21'
MAX_RECORDS = 65536
MAX_INPUT_BYTES = 4_000_000
MAX_SLEEP_OUTPUTS = 1024
MAX_SLEEP_ERRORS = 16
SLEEP_OUTPUT_BYTES = 200
SLEEP_ABI = 'ecore-3.6.2-linux-x86_64-sleep-output-200-v1'
SLEEP_CALLBACK = C.CFUNCTYPE(None, C.c_void_p, C.c_void_p)


def _bounded_sleep_string(address, max_chars):
    if not address:
        return None
    value = bytearray()
    # Native allocations include a terminator. Read incrementally so an early
    # NUL never causes a bulk read past that allocation. All reads occur inside
    # the native callback: the producer lends stack-backed storage.
    for offset in range(max_chars + 1):
        char = C.string_at(address + offset, 1)[0]
        if char == 0:
            return value.decode('ascii')
        if char not in b'1234':
            raise ValueError('Unknown native sleep phase symbol')
        value.append(char)
    raise ValueError('Native sleep phase string exceeds bound')


def _copy_sleep_output(address):
    """Copy the statically verified amd64 ABI during its callback only.

    Addresses must come from the pinned native core, never request data.
    Unknown fields/pointers and profile-dependent derived metrics are omitted.
    """
    if not address:
        raise ValueError('Null native sleep output')
    header = C.string_at(address, SLEEP_OUTPUT_BYTES)
    i32 = lambda offset: struct.unpack_from('<i', header, offset)[0]
    i64 = lambda offset: struct.unpack_from('<q', header, offset)[0]
    pointer = lambda offset: struct.unpack_from('<Q', header, offset)[0]
    start, end, duration = i64(0x18), i64(0x28), i32(0x38)
    if not (0 < start < end and end - start <= 57600 and 0 < duration <= 57600):
        raise ValueError('Native sleep period outside verified bounds')
    summary = None
    if pointer(0x40):
        raw = C.string_at(pointer(0x40), 44)
        fields = {'wake_seconds': 0, 'rem_seconds': 4, 'light_seconds': 8,
                  'deep_seconds': 12, 'score_raw': 0x18, 'wake_up_count_raw': 0x1c,
                  'latency_seconds': 0x20, 'total_sleep_seconds': 0x24,
                  'efficiency_raw': 0x28}
        summary = {name: struct.unpack_from('<i', raw, offset)[0]
                   for name, offset in fields.items()}
    return {'timestamp_utc_seconds': i64(0), 'time_zone_minutes': i32(8),
            'sleep_period_id': i32(0x10), 'bedtime_start_utc_seconds': start,
            'bedtime_start_time_zone_minutes': i32(0x20),
            'bedtime_end_utc_seconds': end, 'bedtime_end_time_zone_minutes': i32(0x30),
            'time_in_bed_seconds': duration, 'alert_info_raw': i32(0x3c),
            'is_longest_sleep_period_raw': i32(0x78),
            'phases_30_seconds': _bounded_sleep_string(pointer(0x68), 1920),
            'phases_5_minutes': _bounded_sleep_string(pointer(0x60), 192),
            'summary2': summary, 'native_version': [3, 6, 2], 'native_abi': SLEEP_ABI,
            'user_profile_used': False, 'published_as_valid_measurement': False,
            'medical_validation_claimed': False}


def _sleep_capture(callback_counts):
    """Own the callback lifetime and bounded copied results for one native job."""
    outputs, errors = [], []

    @SLEEP_CALLBACK
    def capture(context, output):
        callback_counts['sleep_outputs_received'] += 1
        try:
            if callback_counts['sleep_outputs_received'] > MAX_SLEEP_OUTPUTS:
                raise ValueError('Native sleep callback count exceeds bound')
            outputs.append(_copy_sleep_output(output))
        except Exception as exc:
            # Separate sleep diagnostics preserve valid unrelated sensor data.
            # Diagnostics are fixed decoder messages, never pointer values.
            if len(errors) < MAX_SLEEP_ERRORS:
                errors.append(str(exc) if isinstance(exc, ValueError) else 'Native sleep decoding failed')

    return capture, outputs, errors


class Version(C.Structure):
    _fields_ = [('major', C.c_uint32), ('middle', C.c_uint32), ('minor', C.c_uint32)]


class TimePair(C.Structure):
    _fields_ = [('ring_ticks', C.c_uint32), ('utc_ms', C.c_uint64)]


class Record20(C.Structure):
    _fields_ = [('words', C.c_uint32 * 5)]


class SpO2Prefix(C.Structure):
    _fields_ = [('kind', C.c_uint32), ('pad0', C.c_uint32), ('utc_ms', C.c_uint64),
                ('timezone', C.c_int32), ('pad1', C.c_uint32), ('average', C.c_uint8),
                ('reserved', C.c_uint8 * 11), ('quality', C.c_float)]


def _records(data):
    if not isinstance(data, dict) or data.get('schema') != 'standard-ring-events-raw-hex-v1':
        raise ValueError('Unexpected sensor input schema')
    rows = data.get('records')
    if not isinstance(rows, list) or not 1 <= len(rows) <= MAX_RECORDS:
        raise ValueError('Record count outside supported bound')
    frames = []
    for row in rows:
        if not isinstance(row, str) or not 12 <= len(row) <= 40:
            raise ValueError('Records must be hex strings')
        raw = bytes.fromhex(row)
        if not 6 <= len(raw) <= 20 or not 0x41 <= raw[0] <= 0x85 or raw[1] + 2 != len(raw):
            raise ValueError('Unsupported standard record')
        if raw[0] == 0x5c:
            raise ValueError('User-profile records are excluded from this sensor-only worker')
        frames.append(raw + bytes(20 - len(raw)))
    return frames


def _mapped_sensor_record(raw, utc):
    if raw[0] not in (0x47, 0x50, 0x62):
        return None
    return {'record_sha256': hashlib.sha256(raw[:raw[1] + 2]).hexdigest(),
            'utc_ms': utc, 'tag_raw': raw[0]}


def calculate(data, *, library_path='/native/libappecore-compat.so', source_input_sha256=None):
    """Container-only function. The host must never load this ELF via ctypes."""
    if C.sizeof(Record20) != 20 or C.alignment(Record20) != 4:
        raise RuntimeError('Record-by-value ABI mismatch')
    if C.sizeof(TimePair) != 16 or TimePair.utc_ms.offset != 8:
        raise RuntimeError('Time-mapping ABI mismatch')
    if C.sizeof(SpO2Prefix) != 40 or SpO2Prefix.average.offset != 24 or SpO2Prefix.quality.offset != 36:
        raise RuntimeError('SpO2 ABI mismatch')
    with open(library_path, 'rb') as handle:
        digest = hashlib.sha256(handle.read()).hexdigest()
    if digest != LIBRARY_SHA256:
        raise RuntimeError('Unverified native library')
    frames = _records(data)
    input_count = len(frames)
    mapped, spo2, callback_errors = [], [], []
    callback_counts = Counter()
    capture_sleep, sleep_outputs, sleep_errors = _sleep_capture(callback_counts)
    initialized = False
    lib = C.CDLL(library_path, mode=os.RTLD_NOW | os.RTLD_LOCAL)
    lib.ecore_get_version.argtypes = []
    lib.ecore_get_version.restype = Version
    value = lib.ecore_get_version()
    version = [value.major, value.middle, value.minor]
    if version != [3, 6, 2]:
        raise RuntimeError('Unverified native version')
    CALLBACK = C.CFUNCTYPE(None, C.c_void_p, C.c_void_p)
    TIMEMAP = C.CFUNCTYPE(None, C.c_void_p, C.c_uint64, Record20)

    @CALLBACK
    def discard(context, output):
        callback_counts['other_outputs_discarded'] += 1

    @CALLBACK
    def capture_spo2(context, output):
        if not output:
            return
        kind = C.c_uint32.from_address(output).value
        callback_counts['spo2_type_' + str(kind)] += 1
        if kind != 6:
            return
        if len(spo2) >= MAX_RECORDS:
            callback_errors.append('SpO2 output bound exceeded')
            return
        item = C.cast(output, C.POINTER(SpO2Prefix)).contents
        quality = float(item.quality)
        spo2.append({'data_type': 6, 'timestamp_utc_ms_native': int(item.utc_ms),
                     'timezone_raw': int(item.timezone), 'average_raw': int(item.average),
                     'quality_raw': quality if math.isfinite(quality) else None,
                     'quality_finite': math.isfinite(quality)})

    @TIMEMAP
    def map_event(context, utc_ms, record):
        if len(mapped) >= MAX_RECORDS:
            callback_errors.append('Time-mapping output bound exceeded')
            return
        raw = bytes(record)
        mapped.append((raw, int(utc_ms)))

    # Keep all closures and argument buffers referenced until deinit returns.
    noop = C.cast(discard, C.c_void_p)
    mac = (C.c_uint8 * 6)(2, 0, 0, 0, 0, 1)
    user_info = (C.c_uint32 * 7)()  # No guessed age, gender, weight or profile.
    lib.ecore_init.argtypes = [C.c_void_p, C.c_size_t, C.POINTER(C.c_uint8)] + [C.c_void_p] * 9
    lib.ecore_init.restype = C.c_int32
    lib.ecore_deinit.argtypes = []
    lib.ecore_deinit.restype = None
    result = None
    try:
        # ABI evidence: argument7 is the sleep callback, after three raw outputs.
        init_result = lib.ecore_init(None, 0, mac, noop, noop, noop,
                                     C.cast(capture_sleep, C.c_void_p), noop, noop, None, None, None)
        if init_result < 0:
            raise RuntimeError('Native fresh initialization failed')
        initialized = True
        lib.ecore_set_spo2_handler.argtypes = [CALLBACK]
        lib.ecore_set_spo2_handler.restype = None
        lib.ecore_set_spo2_handler(capture_spo2)
        lib.ecore_set_feature.argtypes = [C.c_int, C.c_int]
        lib.ecore_set_feature.restype = C.c_int
        lib.ecore_get_feature.argtypes = [C.c_int, C.POINTER(C.c_int)]
        lib.ecore_get_feature.restype = C.c_int
        if lib.ecore_set_feature(19, 1) != 0:
            raise RuntimeError('Native SpO2 feature setter failed')
        enabled = C.c_int(-1)
        if lib.ecore_get_feature(19, C.byref(enabled)) != 0 or enabled.value != 1:
            raise RuntimeError('Native SpO2 feature getter verification failed')
        lib.post_process_ring_events.argtypes = [C.c_void_p, C.c_void_p, C.c_uint, C.c_uint32,
                                                C.c_uint64, TIMEMAP, C.POINTER(C.c_int32), C.POINTER(C.c_uint8)]
        lib.post_process_ring_events.restype = TimePair

        def map_frames(selected):
            mapped.clear()
            payload = b''.join(selected)
            buffer = (C.c_uint8 * len(payload)).from_buffer_copy(payload)
            missing = C.c_int32(-1)
            reset = C.c_uint8(0)
            anchor = lib.post_process_ring_events(None, buffer, len(selected), 0, 0, map_event,
                                                  C.byref(missing), C.byref(reset))
            mapping = {'ring_anchor': int(anchor.ring_ticks), 'utc_anchor_ms': int(anchor.utc_ms),
                       'first_missing_index': missing.value, 'reset_seen': reset.value,
                       'mapped_count': len(mapped),
                       'record_identity_verified': Counter(raw for raw, utc in mapped) == Counter(selected)}
            complete = bool(anchor.ring_ticks or anchor.utc_ms) and missing.value == -1 and mapping['record_identity_verified'] and len(mapped) == len(selected) and all(utc > 0 for raw, utc in mapped)
            return buffer, mapping, complete

        buffer, mapping, complete = map_frames(frames)
        excluded = []
        if not complete and mapping['first_missing_index'] == -1 and mapping['utc_anchor_ms'] > 0:
            # Proven case: pre-RingStart debug metadata lacking UTC. Never drop
            # a physiological event or silently repair a middle-of-stream gap.
            seen = Counter(hashlib.sha256(raw).digest() for raw, utc in mapped)
            missing_indices = []
            for index, raw in enumerate(frames):
                key = hashlib.sha256(raw).digest()
                if seen[key]:
                    seen[key] -= 1
                else:
                    missing_indices.append(index)
            if missing_indices and len(missing_indices) <= 64 and missing_indices == list(range(len(missing_indices))) and all(frames[i][0] in (0x43, 0x61) for i in missing_indices):
                excluded = [{'index': i, 'tag': frames[i][0],
                             'ring_ticks': int.from_bytes(frames[i][2:6], 'little'),
                             'reason': 'pre-RingStart metadata without native UTC'} for i in missing_indices]
                frames = frames[len(missing_indices):]
                if frames:
                    buffer, mapping, complete = map_frames(frames)
        process_result = None
        if complete and not callback_errors:
            lib.ecore_process_events.argtypes = [C.c_void_p, C.c_void_p, C.c_int, C.c_void_p, C.c_uint]
            lib.ecore_process_events.restype = C.c_bool
            process_result = bool(lib.ecore_process_events(None, user_info, -1, buffer, len(frames)))
        temperatures = []
        sensor_times = []
        if process_result is True and not callback_errors:
            for raw, utc in mapped:
                sensor = _mapped_sensor_record(raw, utc)
                if sensor is not None:
                    sensor_times.append(sensor)
                if raw[0] != 0x46:
                    continue
                payload = raw[6:raw[1] + 2]
                if len(payload) not in range(4, 15, 2):
                    continue
                field = int.from_bytes(payload[:2], 'little', signed=True)
                temperatures.append({'record_sha256': hashlib.sha256(raw[:raw[1] + 2]).hexdigest(),
                                     'utc_ms': utc, 'ring_ticks': int.from_bytes(raw[2:6], 'little'),
                                     'field_1_celsius': None if field == -32768 else field / 100.0,
                                     'field_index': 1, 'sensor_identity': None,
                                     'quality': None, 'core_body_temperature': False})
        result = {'status': 'ok' if process_result is True and not callback_errors else 'mapping_unresolved' if not complete else 'native_processing_failed',
                  'process_true': process_result is True and not callback_errors,
                  'version': version, 'source_input_sha256': source_input_sha256,
                  'input_record_count': input_count, 'processed_record_count': len(frames) if process_result is True else 0,
                  'mapping': {**mapping, 'complete': complete,
                              'unresolved_count': max(0, len(frames) - mapping['mapped_count'])},
                  'excluded_metadata': excluded, 'callback_counts': dict(callback_counts),
                  'callback_errors': callback_errors,
                  'sleep_outputs': [], 'sleep_callback_errors': sleep_errors,
                  'sleep_capture_complete': False, 'sleep_native_abi': SLEEP_ABI,
                  'spo2_outputs': spo2 if process_result is True and not callback_errors else [],
                  'mapped_temperatures': temperatures,'mapped_sensor_records':sensor_times, 'deinit_returned': False,
                  'medical_validation_claimed': False, 'user_profile_used': False, 'published_as_valid_measurement': False, 'architecture': 'x86_64',
                  'quality_rule': 'raw native quality; caller chooses a documented display policy',
                  'limitations': ['Post-session history, not live SpO2', 'Only the tested ecore 3.6.2 ABI',
                                  'No general Bionic FILE/stdio compatibility',
                                  'No calories or metrics requiring a personal profile']}
    finally:
        if initialized:
            lib.ecore_deinit()
            if result is not None:
                result['deinit_returned'] = True
    if result is not None:
        # Deinit may itself invoke callbacks. Finalize only after it returns;
        # failed process/mapping/global callbacks cannot release measurements.
        success = (result['process_true'] and result['mapping']['complete']
                   and result['deinit_returned'] and not callback_errors)
        result['callback_counts'] = dict(callback_counts)
        result['process_true'] = bool(success)
        if not success:
            for field in ('spo2_outputs', 'mapped_temperatures', 'mapped_sensor_records'):
                result[field] = []
            if result['status'] == 'ok':
                result['status'] = 'native_processing_failed'
        result['sleep_capture_complete'] = bool(success and not sleep_errors)
        result['sleep_outputs'] = sleep_outputs if result['sleep_capture_complete'] else []
        result['gen3_sleep'] = extract_gen3_sleep(frames, mapped, verified=success)
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--input', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--outer-offline-test', action='store_true', help='CLI witness only; requires container network none, never used by HTTP')
    args = parser.parse_args()
    try:
        with open(args.input, 'rb') as handle:
            payload = handle.read(MAX_INPUT_BYTES + 1)
        if len(payload) > MAX_INPUT_BYTES:
            raise ValueError('Input size bound exceeded')
        from sandbox import restrict_native_child
        sandbox_mechanism = restrict_native_child(outer_offline_test=args.outer_offline_test)
        result = calculate(json.loads(payload), library_path=os.environ.get('HOME_PULSE_NATIVE_LIBRARY', '/native/libappecore-compat.so'), source_input_sha256=hashlib.sha256(payload).hexdigest())
        result['sandbox_mechanism'] = sandbox_mechanism
    except BaseException as exc:
        result = {'status': 'worker_error', 'process_true': False,
                  'error_type': type(exc).__name__, 'error': str(exc),
                  'spo2_outputs': [], 'sleep_outputs': [], 'sleep_capture_complete': False,
                  'mapped_temperatures': [], 'mapped_sensor_records': [], 'deinit_returned': False, 'medical_validation_claimed': False}
    try:
        descriptor = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, 'w') as handle:
            json.dump(result, handle, ensure_ascii=False, allow_nan=False)
            handle.flush()
            os.fsync(handle.fileno())
        print(json.dumps({'status': result['status'], 'process_true': result['process_true'],
                          'spo2_output_count': len(result['spo2_outputs'])}), flush=True)
        # General C++ std-stream destructors depend on the unported FILE ABI.
        os._exit(0 if result['process_true'] else 2)
    except Exception:
        os._exit(1)


if __name__ == '__main__':
    main()
