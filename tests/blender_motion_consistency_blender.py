"""Run inside actual Blender; the rig is a diagnostic box, never creature art."""
from pathlib import Path
import hashlib
import json
import sys
import bpy
from mathutils import Vector
sys.dont_write_bytecode=True
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import blender_action_worker as worker
import blender_motion_consistency as consistency


out=Path(sys.argv[sys.argv.index('--')+1]).resolve()
base=worker._self_test_mode(bpy,out)
scene=bpy.context.scene;camera=scene.camera;armature=bpy.data.objects['SyntheticRig']
root=armature.pose.bones['root']
groups=[{'native_group':g,'action_name':name,'action_slot':None,
         'slots':[{'native_frame':0,'action_frame':3,'subframe':0.0}]} for g,name in
        [(0,'SyntheticWalk'),(1,'SyntheticAttack')]]
projected=worker._camera_pixel(bpy,camera,scene,Vector((0,0,0)))
contract={'schema':consistency.SCHEMA,'calibration_key':[0,0],
    'rigid_segments':[{'id':name,'bone':'root','a':[0,0,0],'b':end,'max_relative_change':.01}
                      for name,end in [('vertical',[0,0,1]),('horizontal',[1,0,0])]],
    'root':{'bone':'root','point':[0,0,0],'max_error_native':.05,
            'track':[{'native_group':g,'native_frame':0,'expected_pixel_1x':[v/3 for v in projected]}
                      for g in (0,1)]}}
config={'groups':groups,'motion_consistency':contract}
contract['rigid_segments'].append({'id':'evaluated-body-edge','space':'evaluated_mesh',
                                  'object':'SyntheticMesh','vertices':[0,1],'max_relative_change':.01})
positive=worker._motion_consistency_probe(bpy,scene,camera,armature,config)
consistency.replay_report(contract,groups,positive)
original=worker._sample_action
camera_scale=camera.data.ortho_scale
mesh=bpy.data.objects['SyntheticMesh'].data
rest_vertex=mesh.vertices[1].co.copy()
negative=[]
for mode,expected in [('scale','MOTION_RIGID_SEGMENT_SCALE_DRIFT'),
                       ('root','MOTION_ROOT_TRACK_MISMATCH'),('camera','MOTION_CAMERA_DRIFT'),
                       ('geometry','MOTION_RIGID_SEGMENT_SCALE_DRIFT')]:
    def inject(bpy_,scene_,rig,binding,frame,subframe):
        root.scale=(1,1,1);root.location=(0,0,0);camera.data.ortho_scale=camera_scale
        mesh.vertices[1].co=rest_vertex;mesh.update()
        value=original(bpy_,scene_,rig,binding,frame,subframe)
        if binding['native_group']==1:
            if mode=='scale':root.scale=(1.2,1.2,1.2)
            elif mode=='root':root.location=(.4,0,0)
            elif mode=='camera':camera.data.ortho_scale=camera_scale*1.2
            else:mesh.vertices[1].co.x+=.4;mesh.update()
        bpy.context.view_layer.update()
        return value
    worker._sample_action=inject
    try:
        worker._motion_consistency_probe(bpy,scene,camera,armature,config)
        raise AssertionError('Injected '+mode+' defect was not rejected')
    except worker.WorkerError as error:
        if error.code!=expected:raise
        negative.append(error.code)
    finally:
        worker._sample_action=original;root.scale=(1,1,1);root.location=(0,0,0)
        mesh.vertices[1].co=rest_vertex;mesh.update()
        camera.data.ortho_scale=camera_scale;bpy.context.view_layer.update()
model=out/'synthetic-motion-guard.blend'
bpy.ops.wm.save_as_mainfile(filepath=str(model))
report={'schema':'h3.blender.motion-consistency.integration.v1','status':'PASS',
        'blender_version':bpy.app.version_string,'diagnostic_rig_only':True,'real_creature':False,
        'positive':positive,'injected_defects_rejected':negative,'rendered_diagnostic_frames':base['outputs'],
        'editable_scene':{'path':str(model),'sha256':hashlib.sha256(model.read_bytes()).hexdigest()},
        'game_written':False,'art_approved':False}
(out/'motion-consistency-test.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
print('H3_MOTION_CONSISTENCY_TEST='+json.dumps(report,sort_keys=True))
