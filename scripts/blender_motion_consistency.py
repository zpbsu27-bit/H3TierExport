"""Explicit rigid-size, fixed-camera and root-track checks for authored Actions.

Pure measurement logic; Blender supplies evaluated world-space segment lengths
and native-pixel projections. Pose semantics and artwork are never inferred.
"""
import copy
import hashlib
import json
import math

SCHEMA='h3.blender.motion-consistency.v1'


class ConsistencyError(ValueError):
    def __init__(self,code,message=''):
        self.code=code
        super().__init__(code+(': '+message if message else ''))


def need(value,code,message=''):
    if not value:raise ConsistencyError(code,message)


def number(value):
    return type(value) in (int,float) and math.isfinite(value)


def vector(value,n):
    return isinstance(value,list) and len(value)==n and all(number(x) for x in value)


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()


def sample_rows(groups):
    return [{'native_group':g['native_group'],**s} for g in groups for s in g['slots']]


def validate(value,groups):
    if value is None:return None
    need(isinstance(value,dict) and value.get('schema')==SCHEMA,'MOTION_CONSISTENCY_SCHEMA_REQUIRED')
    keys={(r['native_group'],r['native_frame']) for r in sample_rows(groups)}
    calibration=value.get('calibration_key')
    need(isinstance(calibration,list) and len(calibration)==2 and all(type(x) is int for x in calibration)
         and tuple(calibration) in keys,'MOTION_CALIBRATION_SLOT_INVALID')
    metrics=value.get('rigid_segments')
    need(isinstance(metrics,list) and 2<=len(metrics)<=64,'MOTION_TWO_RIGID_SEGMENTS_REQUIRED')
    ids=set()
    for m in metrics:
        need(isinstance(m,dict) and isinstance(m.get('id'),str) and m['id']
             and m['id'] not in ids,'MOTION_SEGMENT_ID_OR_BONE_INVALID')
        ids.add(m['id'])
        space=m.get('space','bone_local')
        need(space in ('bone_local','evaluated_mesh'),'MOTION_SEGMENT_SPACE_INVALID')
        if space=='bone_local':
            need(isinstance(m.get('bone'),str) and m['bone'],'MOTION_SEGMENT_ID_OR_BONE_INVALID')
            need(vector(m.get('a'),3) and vector(m.get('b'),3)
                 and math.dist(m['a'],m['b'])>1e-9,'MOTION_SEGMENT_POINTS_INVALID')
        else:
            indices=m.get('vertices')
            need(isinstance(m.get('object'),str) and m['object'] and isinstance(indices,list)
                 and len(indices)==2 and all(type(i) is int and 0<=i<10_000_000 for i in indices)
                 and indices[0]!=indices[1],'MOTION_MESH_SEGMENT_INVALID')
        t=m.get('max_relative_change')
        need(number(t) and 0<t<=.2,'MOTION_SEGMENT_TOLERANCE_INVALID')
    root=value.get('root',{})
    need(isinstance(root.get('bone'),str) and root['bone'] and vector(root.get('point'),3),
         'MOTION_ROOT_BONE_OR_POINT_INVALID')
    t=root.get('max_error_native')
    need(number(t) and 0<=t<=4,'MOTION_ROOT_TOLERANCE_INVALID')
    track=root.get('track')
    need(isinstance(track,list) and len(track)==len(keys),'MOTION_COMPLETE_ROOT_TRACK_REQUIRED')
    seen=set()
    for row in track:
        key=(row.get('native_group'),row.get('native_frame'))
        need(all(type(x) is int for x in key) and key in keys and key not in seen
             and vector(row.get('expected_pixel_1x'),2),'MOTION_ROOT_TRACK_SLOT_INVALID')
        seen.add(key)
    return copy.deepcopy(value)


def evaluate(contract,groups,sample):
    """Evaluate mapped Action samples before rendering. Never changes the scene."""
    contract=validate(contract,groups)
    if contract is None:
        return {'status':'NOT_MEASURED','reason':'No explicit motion-consistency contract',
                'rigid_segment_scale_pass':False,'root_track_pass':False,'art_approved':False}
    rows=sample_rows(groups)
    bykey={(r['native_group'],r['native_frame']):r for r in rows}
    reference=sample(bykey[tuple(contract['calibration_key'])])
    camera=reference['camera']
    need(isinstance(camera,list) and camera and all(number(v) for v in camera),'MOTION_CAMERA_MEASUREMENT_INVALID')
    baselines=reference['lengths']
    need(all(number(baselines.get(m['id'])) and baselines[m['id']]>1e-9
             for m in contract['rigid_segments']),'MOTION_BASELINE_LENGTH_INVALID')
    expected={(r['native_group'],r['native_frame']):r['expected_pixel_1x'] for r in contract['root']['track']}
    records=[]
    for row in rows:
        key=(row['native_group'],row['native_frame'])
        observed=sample(row)
        actual_camera=observed['camera']
        need(len(actual_camera)==len(camera) and all(number(a) and abs(a-b)<=1e-9
                    for a,b in zip(actual_camera,camera)),'MOTION_CAMERA_DRIFT',str(key))
        deviations={}
        for m in contract['rigid_segments']:
            length=observed['lengths'].get(m['id'])
            need(number(length) and length>1e-9,'MOTION_SEGMENT_MEASUREMENT_INVALID',m['id'])
            deviation=length/baselines[m['id']]-1
            need(abs(deviation)<=m['max_relative_change'],'MOTION_RIGID_SEGMENT_SCALE_DRIFT',f'{key}/{m["id"]}: {deviation:g}')
            deviations[m['id']]=deviation
        root=observed['root_native']
        need(vector(root,2),'MOTION_ROOT_MEASUREMENT_INVALID')
        error=math.dist(root,expected[key])
        need(error<=contract['root']['max_error_native'],'MOTION_ROOT_TRACK_MISMATCH',f'{key}: {error:g} native pixels')
        records.append({'native_group':key[0],'native_frame':key[1],
                        'camera':actual_camera,
                        'lengths_world':observed['lengths'],'relative_scale_deviation':deviations,
                        'root_native':root,'root_error_native':error})
    return {'status':'PASS','contract_sha256':digest(contract),'samples':records,
            'sample_count':len(records),'calibration_lengths_world':baselines,
            'camera_measurement':camera,'rigid_segment_scale_pass':True,'root_track_pass':True,
            'coverage':'explicit mapped native frame/subframe samples only',
            'absolute_artistic_body_target_verified':False,'anatomy_automatically_recognized':False,
            'action_semantics_verified':False,'loop_timing_verified':False,'art_approved':False}


def replay_report(contract,groups,report):
    """Recompute report arithmetic/coverage, not an independent Blender render."""
    need(isinstance(report,dict) and report.get('status')=='PASS','MOTION_CONSISTENCY_RECEIPT_REQUIRED')
    rows=report.get('samples',[])
    bykey={(r['native_group'],r['native_frame']):r for r in rows}
    expected={(r['native_group'],r['native_frame']) for r in sample_rows(groups)}
    need(len(bykey)==len(rows) and bykey.keys()==expected,'MOTION_CONSISTENCY_RECEIPT_COVERAGE')
    def sample(row):
        found=bykey[(row['native_group'],row['native_frame'])]
        return {'camera':found['camera'],'lengths':found['lengths_world'],'root_native':found['root_native']}
    need(evaluate(contract,groups,sample)==report,'MOTION_CONSISTENCY_RECEIPT_REPLAY_MISMATCH')
    return {'status':'PASS','scope':'recorded measurement arithmetic and mapped-slot coverage; no Blender rerun'}
