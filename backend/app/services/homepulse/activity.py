"""Read-only feature diagnostics recovered from GetFeatureLatestValues.java."""
from .protocol import OuraProtocolError,_complete

def parse_status(frame,feature):
    frame=bytes(frame);_complete(frame,8,0x2f,0x21)
    if frame[3]!=feature:raise OuraProtocolError('Stato di una funzione diversa')
    return {'feature_id':feature,'mode_raw':frame[4],'flags_raw':frame[5],
            'state_raw':frame[6],'result_raw':frame[7],'raw_hex':frame.hex()}

def parse_exercise_latest(frame):
    frame=bytes(frame);_complete(frame,17,0x2f,0x25)
    if frame[3]!=3:raise OuraProtocolError('Valori di una funzione diversa dal cardio attività')
    return {'source':'oura-ble-exercise-latest','result_raw':frame[4],'flags_raw':frame[5],
            'state_raw':frame[6],'age_raw':int.from_bytes(frame[7:9],'little'),
            'signal_quality_raw':frame[9],'acm_raw':frame[10],'motion_frequency_raw':frame[11],
            'regularity_raw':frame[12],'heart_rate_value_raw':frame[13],
            'heart_rate_approximation_raw':frame[14],
            'temperature_raw':int.from_bytes(frame[15:17],'little',signed=True),
            'raw_hex':frame.hex(),'cadence_rpm':None,'workout_type':None}
