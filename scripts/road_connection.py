"""Source-pinned road strategy. Missing painted sets never become a PASS."""
import hashlib
import json
import subject_scale_layout as common
from map_assembly import edge_diagnostics,transform

def apply(config,profile,native,bykey,frames,event):
    from art_quality_strategies import finding
    model=config.get('model')
    if not model:return
    common.need(model.get('schema')=='h3.road-connection-model.v1' and model.get('model_version')==1,'ROAD_MODEL_SCHEMA_INVALID')
    families={k[1] for k in bykey}
    common.need(families and families<=set(('COBBRD','DIRTRD','GRAVRD'))
        and set(bykey)=={('def',f,0,i) for f in families for i in range(17)},'ROAD_MODEL_COMPLETE_OFFICIAL_SCOPE_REQUIRED')
    actual=dict(model);expected=actual.pop('model_sha256',None)
    raw=json.dumps(actual,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode('utf-8')
    common.need(hashlib.sha256(raw).hexdigest()==expected,'ROAD_MODEL_SHA_DRIFT')
    common.need(model['native_inventory']['sha256']==profile['native_inventory']['sha256'],'ROAD_MODEL_INVENTORY_DRIFT')
    common.pin(model['native_inventory'])
    for ref in model['original_map_transform_sources']:common.pin(ref)
    slots={tuple(row['key']):row for row in model['slots']}
    common.need(slots.keys()==bykey.keys(),'ROAD_MODEL_SCOPE_DIFFERS')
    for k,row in slots.items():
        common.pin(row['native_frame'])
        g=row['native_geometry'];n=native[k]
        common.need(g['canvas_1x']==n['canvas'] and g['crop_1x']==n['crop'] and g['origin_1x']==n['anchor'],'ROAD_MODEL_GEOMETRY_DRIFT')
        event('road_native_model',[k],finding('PASS','BOUND_OFFICIAL_SLOT_AND_HV_FIELDS_ONLY',model_sha256=expected,art_acceptance=False))
    source_proof=config.get('editor_source_proof')
    if source_proof:
        import map_source_oracle
        proof=map_source_oracle.validate_proof(source_proof,profile['native_inventory']);frozen=proof['model']
        graph=frozen['editor_emitted_road_neighbors']
        event('road_legal_neighbors',list(bykey),finding('PASS','SOURCE_QUALIFIED_EDITOR_NEIGHBOR_GRAPH_ONLY',
            editor_model_sha256=frozen['model_sha256'],pair_counts={k:len(v['pairs']) for k,v in graph.items()},
            native_runtime_verified=False,painted_seams_verified=False))
    # Native alpha is geometry evidence, not the editor's 3x3 selector.
    # Real maps supply the chosen frame and mirror states for scene QA.
    for ref in config.get('scene_reports',[]):
        report=common.doc(ref)
        common.need(report.get('schema')=='h3.map-assembly-review.report.v1','ROAD_MAP_SCENE_REPORT_REQUIRED')
        common.need(report['native_inventory']['sha256']==profile['native_inventory']['sha256'],'ROAD_SCENE_INVENTORY_DRIFT')
        common.pin(report['map'])
        for pin in report['native_reference_pins']:common.pin(pin)
        keys=[]
        for row in report['actual3x']:
            k=tuple(row['key'])
            common.need(k in bykey and bykey[k]['source'] and row['master_source']['sha256']==bykey[k]['source']['sha256'],'ROAD_SCENE_SOURCE_DRIFT')
            common.pin(row['image']);common.pin(row['master_source']);keys.append(k)
        if keys:event('road_scene_underlay',keys,finding('RISK','PINNED_MAP_SCENE_RENDERED_REQUIRES_VISUAL_ACCEPTANCE',missing_placements=len(report['missing_road_placements']),report=ref))
    arrays={}
    for k,item in bykey.items():
        if not item.get('source') or 'pixels' not in item or frames.registration(k) is None:continue
        arrays[k]=frames.get(k,'native_canvas_3x')
        common.need(arrays[k].shape==(96,96,4),'ROAD_NATIVE_CELL_MUST_BE_96_3X')
    # Shared interface contracts are deliberate inputs. Never guess a material
    # tolerance from the low-resolution contour or a tile's average color.
    for k,a in arrays.items():
        diagnostics=[]
        for bits in range(4):
            b=transform(a,bits,0)
            diagnostics.append({'mirror_bits':bits,'vertical_repeat':edge_diagnostics(b,b,'south'),
                                'horizontal_repeat':edge_diagnostics(b,b,'east')})
        event('road_actual3x_ports',[k],finding('NOT_MEASURED','ACTUAL_3X_DIAGNOSTICS_AWAIT_SHARED_INTERFACE_CONTRACT',diagnostics=diagnostics))
    names=['road_pairwise_assembly','road_surface_seams']
    if not source_proof:names.insert(0,'road_legal_neighbors')
    for name in names:
        event(name,list(bykey),finding('NOT_MEASURED','COMPLETE_PAINTED_SET_AND_SOURCE_BOUND_INTERFACE_CONTRACT_REQUIRED',available_actual3x=len(arrays),required_slots=len(bykey)))
