"""Read-only, preset-specific checks in explicitly declared pixel coordinates.

These algorithms never infer anatomy from an alpha envelope, invent neighbor
relations, approve native timing, or modify painted images.
"""
from __future__ import annotations
import collections
import io
import math
import struct
import numpy as np
from PIL import Image
import subject_scale_layout as common
from asset_repair import cie_lab

CHECKS={
 'creature-actions':['native_aliases','body_scale','ordered_motion','loop_boundary','native_registration','semantic_structure','action_continuity','background_integrity'],
 'town-animation':['static_region_lock','ordered_motion','loop_boundary','native_registration'],
 'mounted-heroes':['rider_and_mount_body_scale','ground_anchor','native_registration','semantic_structure'],
 'roads-rivers':['registered_neighbor_seams','native_alpha_pixelation','ordered_motion','loop_boundary','semantic_flow_direction'],
 'road-connection':['road_native_model','road_legal_neighbors','road_actual3x_ports','road_pairwise_assembly','road_scene_underlay','road_surface_seams','native_alpha_pixelation'],
 'ground':['registered_neighbor_seams','material_consistency'],
 'adventure-flags':['flag_canvas_uniformity','flag_pole_axis_alignment','flag_pole_connection_purity','flag_size_consistency','flag_alpha_transparency_purity'],
 'boats':['boat_canvas_uniformity','boat_waterline_alignment','boat_perspective_volume','boat_alpha_transparency_purity'],
}
REGION_LIMIT=16*1024*1024


def token(key):return ':'.join(map(str,key))


def finding(status,reason,**values):
 return {'status':status,'reason':reason,**values}


def rgba(ref):
 _,raw=common.pin(ref)
 with Image.open(io.BytesIO(raw)) as im:
  common.need(im.format=='PNG' and getattr(im,'n_frames',1)==1,'SINGLE_PNG_REQUIRED')
  common.need(im.width*im.height<=REGION_LIMIT,'DOMAIN_ANALYSIS_PIXEL_BUDGET')
  im.load();return np.asarray(im.convert('RGBA'))


def rectangle(value,size):
 common.need(isinstance(value,list) and len(value)==4 and all(type(x) is int for x in value),'INTEGER_PIXEL_RECT_REQUIRED')
 x,y,r,b=value;w,h=size
 common.need(0<=x<r<=w and 0<=y<b<=h,'REGION_RECT_OUTSIDE_SOURCE')
 return x,y,r,b


def mask(spec,size,space='source_pixels'):
 if 'path' in spec:spec=common.doc(spec)
 common.need(spec.get('coordinate_space')==space and spec.get('pixels')==list(size),'BOUND_MASK_COORDINATE_CANVAS_REQUIRED')
 out=np.zeros((size[1],size[0]),dtype=bool)
 for rect in spec.get('rectangles',[]):
  x,y,r,b=rectangle(rect,size);out[y:b,x:r]=True
 for rect in spec.get('exclude_rectangles',[]):
  x,y,r,b=rectangle(rect,size);out[y:b,x:r]=False
 common.need(out.any(),'EMPTY_ANALYSIS_REGION')
 return out


def visual(array):
 value=array.astype(np.float32)/255.;return np.dstack((value[:,:,:3]*value[:,:,3:4],value[:,:,3]))


def native_alpha_pixelation(candidate,native_alpha):
 """Detect the exact pixel-centre nearest-neighbour alpha expansion only."""
 h,w=candidate.shape[:2];mh,mw=native_alpha.shape
 common.need(w>=3*mw and h>=3*mh,'NATIVE_ALPHA_DIAGNOSTIC_SOURCE_BELOW_3X')
 xs=np.floor((np.arange(w,dtype=np.float64)+.5)*mw/w).astype(np.int32).clip(0,mw-1)
 ys=np.floor((np.arange(h,dtype=np.float64)+.5)*mh/h).astype(np.int32).clip(0,mh-1)
 expected=native_alpha[ys[:,None],xs[None,:]]
 actual=candidate[:,:,3]
 return {'exact_nearest_native_alpha':bool(np.array_equal(actual,expected)),
         'matching_alpha_fraction':float(np.mean(actual==expected)),
         'candidate_pixels':[w,h],'native_pixels':[mw,mh],
         'only_this_mask_pattern_checked':True,'art_or_seams_approved':False}


def compare_region(anchor,candidate,selected,exact=False,tolerance=0):
 common.need(anchor.shape==candidate.shape and selected.shape==anchor.shape[:2],'SAME_REGISTERED_PIXEL_CANVAS_REQUIRED')
 a=anchor.astype(np.int16) if exact else visual(anchor)*255
 b=candidate.astype(np.int16) if exact else visual(candidate)*255
 delta=np.max(np.abs(a-b),axis=2)[selected]
 return {'selected_pixels':int(selected.sum()),'changed_pixels':int((delta>tolerance).sum()),
         'changed_fraction':float((delta>tolerance).mean()),'maximum_channel_difference':float(delta.max(initial=0)),
         'comparison':'exact_rgba' if exact else 'premultiplied_rgba_srgb_diagnostic'}


def def_aliases(ref,identity,geometry=None):
 """Read the real DEF header/offset table, not resource-name or PNG heuristics."""
 _,blob=common.pin(ref);common.need(len(blob)>=784,'DEF_HEADER_TRUNCATED')
 _,cw,ch,count=struct.unpack_from('<4I',blob)
 common.need(0<cw<=8192 and 0<ch<=8192 and 0<count<=256,'DEF_HEADER_LIMIT')
 pos=784;classes=collections.defaultdict(list);groups=set()
 for _ in range(count):
  common.need(pos+16<=len(blob),'DEF_GROUP_TRUNCATED')
  group,n,_,_=struct.unpack_from('<4I',blob,pos);pos+=16
  common.need(group not in groups and 0<n<=4096,'DEF_GROUP_DUPLICATE_OR_LIMIT');groups.add(group)
  common.need(pos+n*17<=len(blob),'DEF_NAMES_OFFSETS_TRUNCATED');pos+=n*13
  offsets=struct.unpack_from('<'+'I'*n,blob,pos);pos+=n*4
  for frame,offset in enumerate(offsets):
   common.need(offset+32<=len(blob),'DEF_FRAME_OFFSET_INVALID')
   _,_,fw,fh,w,h,x,y=struct.unpack_from('<6I2i',blob,offset)
   common.need(fw>0 and fh>0 and w>0 and h>0,'DEF_FRAME_GEOMETRY_INVALID')
   if geometry is not None:
    k=('def',identity,group,frame)
    if k in geometry:
     row=geometry[k]
     common.need(row['canvas']==[fw,fh] and row['crop']==[w,h] and row['anchor']==[x,y],'DEF_FRAME_GEOMETRY_DIFFERS_FROM_INVENTORY')
   classes[offset].append(['def',identity,group,frame])
 return list(classes.values())


def edge_profile(array,side,depth=1,span=None):
 common.need(side in ('north','south','west','east') and type(depth) is int and 1<=depth<=16,'EDGE_SPEC_INVALID')
 h,w=array.shape[:2];common.need(depth<=min(h,w),'EDGE_DEPTH_OUTSIDE_SOURCE')
 strip={'north':lambda:array[:depth,:,:],'south':lambda:array[-depth:,:,:],
        'west':lambda:array[:,:depth,:],'east':lambda:array[:,-depth:,:]}[side]()
 axis=0 if side in ('north','south') else 1
 v=visual(strip).mean(axis=axis)
 if span is not None:
  common.need(isinstance(span,list) and len(span)==2 and all(type(x) is int for x in span)
              and 0<=span[0]<span[1]<=len(v),'EDGE_PORT_SPAN_INVALID');v=v[span[0]:span[1]]
 return v


def compare_edges(a,b,spec):
 left=edge_profile(a,spec['a_edge'],spec.get('depth',1),spec.get('a_span'))
 right=edge_profile(b,spec['b_edge'],spec.get('depth',1),spec.get('b_span'))
 if spec.get('reverse',False):right=right[::-1]
 common.need(left.shape==right.shape,'EDGE_PORT_LENGTH_MISMATCH_NO_AUTOMATIC_STRETCH')
 alpha=np.abs(left[:,3]-right[:,3]);visible=(left[:,3]>=.5)&(right[:,3]>=.5)
 delta=None
 if visible.any():
  rgb_a=left[visible,:3]/left[visible,3:4]*255
  rgb_b=right[visible,:3]/right[visible,3:4]*255
  delta=float(np.linalg.norm(cie_lab(rgb_a)-cie_lab(rgb_b),axis=1).mean())
 measures={'samples':len(left),'mean_alpha_difference':float(alpha.mean()),
  'alpha_discontinuity_samples':int((alpha>spec.get('alpha_hint',.15)).sum()),
  'lab_mean_distance':delta,'a_opaque_coverage':float((left[:,3]>=.95).mean()),
  'b_opaque_coverage':float((right[:,3]>=.95).mean())}
 disconnected=spec.get('require_opaque_port',False) and min(measures['a_opaque_coverage'],measures['b_opaque_coverage'])<spec.get('minimum_port_coverage',.9)
 risk=measures['mean_alpha_difference']>spec.get('alpha_hint',.15) or delta is not None and delta>spec.get('color_hint',12)
 return finding('FAIL' if disconnected else 'RISK' if risk else 'PASS',
  'DECLARED_PORT_DISCONNECTED' if disconnected else 'EDGE_DISCONTINUITY_DIAGNOSTIC' if risk else 'DECLARED_EDGES_COMPARED',**measures)


def motion_metrics(a,b):
 common.need(a.shape==b.shape,'SAME_REGISTERED_PIXEL_CANVAS_REQUIRED')
 # Bounded, in-memory diagnostic view; no PNG or tier is produced.
 def small(value):
  image=Image.fromarray(value);image.thumbnail((512,512),Image.Resampling.BOX)
  return np.asarray(image)
 x=small(a);y=small(b);va=visual(x);vb=visual(y);aa=x[:,:,3]>=16;bb=y[:,:,3]>=16
 union=int((aa|bb).sum());intersection=int((aa&bb).sum())
 def center(v):
  ys,xs=np.nonzero(v);return [float(xs.mean()/max(1,v.shape[1])),float(ys.mean()/max(1,v.shape[0]))] if len(xs) else None
 ca,cb=center(aa),center(bb)
 return {'premultiplied_rgba_mean_difference':float(np.abs(va-vb).mean()),
  'alpha_support_iou':intersection/union if union else None,
  'alpha_support_center_distance_fraction':math.dist(ca,cb) if ca and cb else None,
  'diagnostic_only':True,'not_anatomical_scale':True,'images_written':0}


def flow_metrics(a,b,selected,direction,max_shift=6):
 common.need(a.shape==b.shape and selected.shape==a.shape[:2],'SAME_REGISTERED_PIXEL_CANVAS_REQUIRED')
 common.need(type(max_shift) is int and 1<=max_shift<=12 and len(direction)==2 and math.hypot(*direction)>0,'FLOW_DIRECTION_OR_SEARCH_INVALID')
 ys,xs=np.nonzero(selected)
 if not len(xs):return finding('NOT_MEASURED','EMPTY_FLOW_REGION')
 left,right,top,bottom=int(xs.min()),int(xs.max()+1),int(ys.min()),int(ys.max()+1)
 if (right-left)*(bottom-top)>262144:return finding('NOT_MEASURED','FLOW_ANALYSIS_REGION_TOO_LARGE_SELECT_SMALLER_TEXTURE_REGION')
 a=a[top:bottom,left:right];b=b[top:bottom,left:right];selected=selected[top:bottom,left:right]
 x=visual(a)[:,:,:3].mean(2);y=visual(b)[:,:,:3].mean(2);h,w=x.shape;matches=[]
 if float(x[selected].std())<.005:return finding('NOT_MEASURED','FLOW_REGION_HAS_NO_TRACKABLE_TEXTURE')
 for dy in range(-max_shift,max_shift+1):
  for dx in range(-max_shift,max_shift+1):
   ax0=max(0,-dx);ax1=min(w,w-dx);ay0=max(0,-dy);ay1=min(h,h-dy)
   if ax0>=ax1 or ay0>=ay1:continue
   take=selected[ay0:ay1,ax0:ax1]&selected[ay0+dy:ay1+dy,ax0+dx:ax1+dx]
   if int(take.sum())<16:continue
   va=x[ay0:ay1,ax0:ax1][take];vb=y[ay0+dy:ay1+dy,ax0+dx:ax1+dx][take]
   va=va-va.mean();vb=vb-vb.mean();den=float(np.linalg.norm(va)*np.linalg.norm(vb))
   if den>1e-8:matches.append((float(np.dot(va,vb)/den),dx,dy))
 if not matches:return finding('NOT_MEASURED','FLOW_CORRELATION_UNAVAILABLE')
 matches.sort(reverse=True);score,dx,dy=matches[0];distance=math.hypot(dx,dy)
 alignment=(dx*direction[0]+dy*direction[1])/distance/math.hypot(*direction) if distance else 0
 ambiguous=len(matches)>1 and score-matches[1][0]<.01
 state='NOT_MEASURED' if score<.5 or ambiguous else 'PASS' if distance>0 and alignment>=.5 else 'RISK'
 return finding(state,'FLOW_MATCH_AMBIGUOUS' if state=='NOT_MEASURED' else 'DECLARED_FLOW_DIRECTION_COMPARED',
   measured_shift_pixels=[dx,dy],correlation=score,direction_alignment=alignment,native_timing_verified=False)


class Frames:
 def __init__(self,profile,native,bykey):
  self.profile=profile;self.native=native;self.bykey=bykey;self.registered={}
  if profile.get('native_review_report'):
   doc=common.doc(profile['native_review_report'])
   common.need(doc.get('schema')=='h3.native-review.batch-report.v1' and doc['official_inventory']['sha256']==profile['native_inventory']['sha256']
       and doc['official_inventory'].get('geometry_verified_by_companion') is True,'NATIVE_REVIEW_BOUND_INVENTORY_REQUIRED')
   self.registered={tuple(r['key']):r for r in doc['items']}
 def registration(self,key):
  k=tuple(key);item=self.bykey[k];r=self.registered.get(k)
  if not r:return None
  common.need(item['source'] and r['source_sha256']==item['source']['sha256'],'NATIVE_VIEW_SOURCE_SHA_DRIFT')
  common.need(r['source_pixels']==item['pixels'],'NATIVE_VIEW_SOURCE_DIMENSION_DRIFT')
  target=r['targets'];native=self.native[k]
  common.need(target['canvas_1x']==native['canvas'] and target['crop_1x']==native['crop'] and target['origin_1x']==native['anchor'],'NATIVE_VIEW_GEOMETRY_CHANGED')
  common.need(r.get('source_view_proof',{}).get('all_positive_preserved') is True,'NATIVE_VIEW_MUST_PRESERVE_POSITIVE_ALPHA')
  kx=r['registered_pixels'][0]/native['canvas'][0];ky=r['registered_pixels'][1]/native['canvas'][1]
  common.need(kx>0 and abs(kx-ky)<1e-7,'ISOTROPIC_NATIVE_VIEW_REQUIRED')
  return r,kx,r['registration_offset']
 def get(self,key,space='source_pixels'):
  item=self.bykey[tuple(key)];common.need(item['source'] is not None and 'pixels' in item,'STRATEGY_REQUIRES_VALID_SOURCE')
  if space=='source_pixels':return rgba(item['source'])
  common.need(space=='native_canvas_3x','STRATEGY_COORDINATE_SPACE_UNSUPPORTED')
  reg=self.registration(key);common.need(reg is not None,'NATIVE_VIEW_REGISTRATION_REQUIRED')
  output=reg[0]['outputs']['candidate_3x'];array=rgba({'path':output['path'],'sha256':output['png_sha256']})
  common.need([array.shape[1],array.shape[0]]==[v*3 for v in self.native[tuple(key)]['canvas']],'NATIVE_3X_CANVAS_DIMENSIONS_DIFFER')
  return array


def flag_metrics(array, config=None):
 if config is None: config = {}
 h, w = array.shape[:2]
 expected_canvas = config.get('expected_canvas')
 canvas_pass = ([w, h] == expected_canvas) if expected_canvas is not None else None
 alpha = array[:, :, 3]
 ys, xs = np.nonzero(alpha > 15)
 if not len(xs): return {'status': 'FAIL', 'reason': 'FLAG_EMPTY_CANVAS'}
 min_x, max_x = int(xs.min()), int(xs.max())
 min_y, max_y = int(ys.min()), int(ys.max())
 width, height = max_x - min_x + 1, max_y - min_y + 1
 # The source-pixel measurement band is caller evidence, not a universal
 # 900x1200-canvas assumption. Missing poles must not be invented from bbox.
 band=config.get('pole_rows_y')
 if band is not None:
  common.need(isinstance(band,list) and len(band)==2 and all(type(v) is int for v in band)
      and 0<=band[0]<band[1]<=h,'FLAG_POLE_BAND_OUTSIDE_SOURCE')
 pole_rows=(alpha>40)[band[0]:band[1],:] if band is not None else np.zeros((0,w),bool)
 pole_xs = []
 for row in pole_rows:
  r_xs = np.nonzero(row)[0]
  if len(r_xs):
   pole_xs.append((r_xs.min() + r_xs.max()) / 2.0)
 pole_x = float(np.mean(pole_xs)) if len(pole_xs) else None
 target_pole_x = config.get('expected_pole_x')
 if target_pole_x is not None:common.need(type(target_pole_x) in (int,float) and math.isfinite(target_pole_x),'FLAG_POLE_TARGET_INVALID')
 pole_axis_dev = abs(pole_x-target_pole_x) if pole_x is not None and target_pole_x is not None else None
 pole_axis_pass = (pole_axis_dev <= config.get('pole_x_tolerance', 10)) if pole_axis_dev is not None else None
 # 旗杆与旗面连接处检测（在 pole_x 附近检测非抗锯齿的白色毛刺）
 conn_left = max(0, int(pole_x - 30)) if pole_x is not None else 0
 conn_right = min(w, int(pole_x + 80)) if pole_x is not None else 0
 connection_zone = array[:, conn_left:conn_right]
 cz_rgb = connection_zone[:, :, :3]
 cz_a = connection_zone[:, :, 3]
 white_halo_pixels = int(((cz_a > 10) & (cz_a < 240) & (cz_rgb.mean(axis=2) > 200)).sum())
 connection_purity_pass = (white_halo_pixels <= config.get('max_halo_pixels', 80)) if pole_x is not None else None
 # 画布边缘透明留白纯净度检测（顶部10px、左侧10px、右侧10px、底部10px除立杆接地区间外）
 top_stray = int((alpha[:10, :] > 10).sum())
 left_stray = int((alpha[:, :10] > 10).sum())
 right_stray = int((alpha[:, -10:] > 10).sum())
 bot_mask = alpha[-10:, :].copy()
 if pole_x is not None:bot_mask[:, max(0, int(pole_x - 80)):min(w, int(pole_x + 80))] = 0
 bot_stray = int((bot_mask > 10).sum())
 stray_pixels = top_stray + left_stray + right_stray + bot_stray
 purity_pass = (stray_pixels == 0) if pole_x is not None else None
 return {
  'canvas_pass': canvas_pass, 'pole_axis_pass': pole_axis_pass, 'pole_axis_deviation': pole_axis_dev,
  'pole_x': pole_x, 'target_pole_x': target_pole_x,'pole_measured':pole_x is not None,
  'connection_purity_pass': connection_purity_pass, 'white_halo_pixels': white_halo_pixels,
  'purity_pass': purity_pass, 'margin_stray_pixels': stray_pixels,
  'entity_size': [width, height], 'entity_bbox': [min_x, min_y, max_x, max_y]
 }


def boat_metrics(array, slot_key, gold_master_arr=None, config=None):
 if config is None: config = {}
 h, w = array.shape[:2]
 expected_canvas = config.get('expected_canvas')
 canvas_pass = ([w, h] == expected_canvas) if expected_canvas is not None else None
 alpha = array[:, :, 3]
 ys, xs = np.nonzero(alpha > 15)
 if not len(xs): return {'status': 'FAIL', 'reason': 'BOAT_EMPTY_CANVAS'}
 min_x, max_x = int(xs.min()), int(xs.max())
 min_y, max_y = int(ys.min()), int(ys.max())
 width, height = max_x - min_x + 1, max_y - min_y + 1
 bottom_y = max_y
 target_bottom_y = config.get('expected_keel_y')
 if target_bottom_y is not None:common.need(type(target_bottom_y) in (int,float) and math.isfinite(target_bottom_y),'BOAT_KEEL_TARGET_INVALID')
 # A bottom-envelope diagnostic is not a physical waterline measurement.
 envelope_deviation=abs(bottom_y-target_bottom_y) if target_bottom_y is not None else None
 waterline_dev=None;waterline_pass=None
 expected_ratio=config.get('expected_width_ratios',{}).get(token(slot_key))
 if expected_ratio is not None:common.need(type(expected_ratio) in (int,float) and math.isfinite(expected_ratio) and expected_ratio>0,'BOAT_DECLARED_WIDTH_RATIO_INVALID')
 base_width=None
 if gold_master_arr is not None:
  gm_alpha = gold_master_arr[:, :, 3]
  gm_ys, gm_xs = np.nonzero(gm_alpha > 15)
  if len(gm_xs): base_width = int(gm_xs.max() - gm_xs.min() + 1)
 observed_ratio=float(width)/float(base_width) if base_width is not None else None
 volume_dev=abs(observed_ratio-expected_ratio) if observed_ratio is not None and expected_ratio is not None else None
 volume_pass=(volume_dev<=config.get('volume_tolerance',.16)) if volume_dev is not None else None
 bottom_strip = alpha[max(0, max_y - 15):min(h, max_y + 15), :]
 fuzzy_water_pixels = int(((bottom_strip > 5) & (bottom_strip < 160)).sum())
 purity_pass = (fuzzy_water_pixels <= config.get('max_water_fuzzy_pixels', 600))
 return {
  'canvas_pass': canvas_pass, 'waterline_pass': waterline_pass, 'waterline_deviation': waterline_dev,
  'volume_pass': volume_pass, 'observed_ratio': observed_ratio, 'expected_ratio': expected_ratio,
  'volume_deviation': volume_dev, 'purity_pass': purity_pass, 'fuzzy_water_pixels': fuzzy_water_pixels,
  'bottom_envelope_deviation_diagnostic':envelope_deviation,'physical_waterline_verified':False,'three_dimensional_volume_verified':False,
  'entity_size': [width, height], 'entity_bbox': [min_x, min_y, max_x, max_y]
 }


def apply(profile,native,items):
 """Run every selected preset check or state the exact missing input."""
 preset=profile['preset'];bykey={tuple(x['key']):x for x in items};config=profile.get('strategies',{})
 checks={name:finding('NOT_MEASURED','REQUIRED_BOUND_INPUT_NOT_SUPPLIED') for name in CHECKS[preset]}
 events=[]
 frames=Frames(profile,native,bykey)
 for item in items:item.setdefault('domain_checks',{})
 def event(name,keys,result):
  keys=list(dict.fromkeys(tuple(k) for k in keys));common.need(set(keys)<=bykey.keys(),'STRATEGY_KEY_OUTSIDE_SCOPE')
  events.append({'check':name,'keys':[list(k) for k in keys],**result})
  for k in keys:bykey[k]['domain_checks'].setdefault(name,[]).append(result)
 def array(k,space='source_pixels'):return frames.get(k,space)
 def available(keys):
  common.need(all(tuple(k) in bykey for k in keys),'STRATEGY_KEY_OUTSIDE_SCOPE')
  return all(bykey[tuple(k)]['source'] and 'pixels' in bykey[tuple(k)] for k in keys)
 if preset in ('roads-rivers','road-connection'):
  bindings={}
  for binding in config.get('native_alpha_masks',[]):
   k=tuple(binding.get('key',[]))
   common.need(k in bykey and k not in bindings,'NATIVE_ALPHA_MASK_KEY_INVALID_OR_DUPLICATE')
   bindings[k]=binding['source']
  for item in items:
   k=tuple(item['key'])
   if k not in bindings or not available([k]):
    event('native_alpha_pixelation',[k],finding('NOT_MEASURED','SOURCE_OR_DECLARED_NATIVE_MASK_MISSING'))
    continue
   original=rgba(bindings[k]);common.need(list(original.shape[:2][::-1])==native[k]['canvas'],'NATIVE_ALPHA_MASK_CANVAS_DIFFERS')
   candidate=array(k);h,w=candidate.shape[:2];mh,mw=original.shape[:2]
   if w<3*mw or h<3*mh:
    event('native_alpha_pixelation',[k],finding('NOT_MEASURED','SOURCE_BELOW_3X_FOR_MASK_DIAGNOSTIC'))
    continue
   metrics=native_alpha_pixelation(candidate,original[:,:,3]);risk=metrics['exact_nearest_native_alpha']
   event('native_alpha_pixelation',[k],finding('RISK' if risk else 'PASS',
    'EXACT_NATIVE_MASK_NEAREST_UPSCALE' if risk else 'EXACT_NEAREST_MASK_PATTERN_NOT_FOUND',
    native_mask=bindings[k],**metrics))
 if preset=='road-connection':
  import road_connection
  road_connection.apply(config.get('road_connection',{}),profile,native,bykey,frames,event)
 # This consumes a real source-pinned AI/user review; it is not an anatomy
 # classifier and never turns a bounding box into a limb count.
 for binding in config.get('semantic_reviews',[]):
  review=common.doc(binding)
  common.need(review.get('schema')=='h3.art.semantic-review.v1' and review.get('review_author') in ('assistant','user'),'SOURCE_BOUND_SEMANTIC_REVIEW_REQUIRED')
  for row in review['items']:
   k=tuple(row['key']);common.need(k in bykey and bykey[k]['source'] and row['image_sha256']==bykey[k]['source']['sha256'],'SEMANTIC_REVIEW_SOURCE_SHA_DRIFT')
   required={'identity','character_count','anatomy','pose'}
   common.need(required<=row['checks'].keys(),'SEMANTIC_REVIEW_CRITERIA_INCOMPLETE')
   states=[row['checks'][n] for n in required]
   common.need(all(s in ('PASS','FAIL','RISK','NOT_APPLICABLE','NOT_MEASURED') for s in states),'SEMANTIC_REVIEW_STATUS_INVALID')
   state=next(s for s in ('FAIL','RISK','NOT_MEASURED','PASS','NOT_APPLICABLE') if s in states)
   event('semantic_structure',[k],finding(state,'BOUND_EXISTING_REVIEW_NOT_LOCAL_MODEL_INFERENCE',
      review_author=review['review_author'],review_source=binding,criteria=row['checks']))
   if 'background_integrity' in row['checks']:
    background=row['checks']['background_integrity']
    common.need(background in ('PASS','FAIL','RISK','NOT_APPLICABLE','NOT_MEASURED'),
                'BACKGROUND_REVIEW_STATUS_INVALID')
    event('background_integrity',[k],finding(background,'BOUND_MANUAL_BACKGROUND_INTEGRITY_REVIEW',
         review_author=review['review_author'],review_source=binding,image_sha256=row['image_sha256']))
 # Pixel-difference diagnostics above do not establish that an action makes
 # sense. This separate, source-bound evidence records a reviewer decision for
 # every adjacent official frame pair without imposing native-pose semantics.
 motion_check_names=('identity_continuity','anatomy_continuity',
                     'equipment_continuity','color_material_continuity','action_logic')
 motion_states=('PASS','RISK','FAIL','NOT_APPLICABLE','NOT_MEASURED')
 seen_motion_groups=set()
 for binding in config.get('motion_reviews',[]):
  review=common.doc(binding)
  common.need(review.get('schema')=='h3.creature.motion-review.v1' and review.get('review_author') in ('assistant','user'),
              'SOURCE_BOUND_CREATURE_MOTION_REVIEW_REQUIRED')
  identity=review.get('identity')
  common.need(isinstance(identity,str) and any(k[0]=='def' and k[1]==identity for k in bykey),
              'MOTION_REVIEW_IDENTITY_OUTSIDE_SCOPE')
  inventory=profile.get('native_inventory',{})
  common.need(review.get('native_inventory_sha256')==inventory.get('sha256'),
              'MOTION_REVIEW_NATIVE_INVENTORY_DRIFT')
  seen_groups=set()
  for sequence in review.get('groups',[]):
   group=sequence.get('group')
   common.need(type(group) is int and group>=0 and group not in seen_groups,'MOTION_REVIEW_GROUP_INVALID_OR_DUPLICATE')
   seen_groups.add(group)
   group_identity=(identity,group)
   common.need(group_identity not in seen_motion_groups,'MOTION_REVIEW_GROUP_BOUND_MORE_THAN_ONCE')
   seen_motion_groups.add(group_identity)
   expected=sorted((k for k in bykey if k[0]=='def' and k[1]==identity and k[2]==group),key=lambda k:k[3])
   common.need(len(expected)>=2,'MOTION_REVIEW_GROUP_OUTSIDE_OR_TOO_SMALL')
   ordered=[tuple(k) for k in sequence.get('ordered_keys',[])]
   common.need(ordered==expected and [k[3] for k in ordered]==list(range(ordered[0][3],ordered[-1][3]+1)),
               'MOTION_REVIEW_MUST_COVER_COMPLETE_CONTIGUOUS_OFFICIAL_GROUP')
   frame_rows=sequence.get('frames',[])
   common.need([tuple(row.get('key',[])) for row in frame_rows]==expected,'MOTION_REVIEW_FRAME_BINDINGS_INCOMPLETE')
   for row,k in zip(frame_rows,expected):
    source=bykey[k].get('source')
    common.need(source is not None and row.get('image_sha256')==source.get('sha256'),
                'MOTION_REVIEW_SOURCE_SHA_DRIFT')
   mode=sequence.get('loop_mode')
   common.need(mode in ('loop','non_loop','NOT_MEASURED'),'MOTION_REVIEW_LOOP_MODE_INVALID')
   edges=list(zip(expected,expected[1:]))
   transitions=sequence.get('transitions',[])
   common.need(len(transitions)==len(edges),'MOTION_REVIEW_TRANSITION_COVERAGE_INCOMPLETE')
   def bound_edge(edge,transition):
    left,right=edge
    common.need(tuple(transition.get('from_key',[]))==left and tuple(transition.get('to_key',[]))==right,
                'MOTION_REVIEW_TRANSITION_ORDER_INVALID')
    common.need(transition.get('from_image_sha256')==bykey[left]['source']['sha256']
                and transition.get('to_image_sha256')==bykey[right]['source']['sha256'],
                'MOTION_REVIEW_TRANSITION_SOURCE_SHA_DRIFT')
    criteria=transition.get('checks',{})
    common.need(set(motion_check_names)<=criteria.keys(),'MOTION_REVIEW_CRITERIA_INCOMPLETE')
    states=[criteria[name] for name in motion_check_names]
    common.need(all(state in motion_states for state in states),'MOTION_REVIEW_STATUS_INVALID')
    note=transition.get('notes','')
    common.need(isinstance(note,str),'MOTION_REVIEW_NOTE_INVALID')
    return left,right,criteria,states,note
   def review_edge(edge,transition,loop_edge=False):
    left,right,criteria,states,note=bound_edge(edge,transition)
    if any(state in ('FAIL','RISK') for state in states):
     common.need(bool(note.strip()),'MOTION_REVIEW_FINDING_REQUIRES_NOTE')
    status=next(s for s in ('FAIL','RISK','NOT_MEASURED','PASS','NOT_APPLICABLE') if s in states)
    event('action_continuity',[left,right],finding(status,'BOUND_SEMANTIC_ACTION_TRANSITION_REVIEW',
         review_author=review['review_author'],identity=identity,group=group,
         from_key=list(left),to_key=list(right),loop_boundary=loop_edge,
         criteria=criteria,notes=note))
   for edge,transition in zip(edges,transitions):review_edge(edge,transition)
   loop_edge=(expected[-1],expected[0]);loop_review=sequence.get('loop_transition')
   if mode=='loop':
    common.need(isinstance(loop_review,dict),'MOTION_REVIEW_LOOP_EDGE_REQUIRED')
    review_edge(loop_edge,loop_review,True)
   elif mode=='non_loop':
    common.need(isinstance(loop_review,dict),'MOTION_REVIEW_NON_LOOP_EDGE_REQUIRED')
    left,right,criteria,states,note=bound_edge(loop_edge,loop_review)
    common.need(all(state=='NOT_APPLICABLE' for state in states),'MOTION_REVIEW_NON_LOOP_EDGE_MUST_BE_NOT_APPLICABLE')
    event('action_continuity',[left,right],finding('NOT_APPLICABLE','REVIEWED_NON_LOOP_GROUP',
         review_author=review['review_author'],identity=identity,group=group,loop_boundary=True,criteria=criteria,notes=note))
   else:
    if isinstance(loop_review,dict):bound_edge(loop_edge,loop_review)
    event('action_continuity',[loop_edge[0],loop_edge[1]],finding('NOT_MEASURED','LOOP_MODE_NOT_REVIEWED',
         review_author=review['review_author'],identity=identity,group=group,loop_boundary=True))
 alias_inputs=config.get('native_defs',[])
 if alias_inputs:
  for binding in alias_inputs:
   identity=binding['identity'];classes=def_aliases(binding['source'],identity,native)
   native_keys={tuple(k) for group in classes for k in group};scope={k for k in bykey if k[0]=='def' and k[1]==identity}
   common.need(scope and scope<=native_keys,'DEF_SCOPE_DOES_NOT_MATCH_INVENTORY')
   for k in scope:
    slot=native[k];common.need(slot['identity']==identity,'DEF_IDENTITY_CHANGED')
   for members in classes:
    selected=[k for k in members if tuple(k) in bykey]
    if not selected:continue
    sources=[bykey[tuple(k)]['source'] for k in selected]
    valid=all(sources) and len({s['sha256'] for s in sources if s})==1
    event('native_aliases',selected,finding('PASS' if valid else 'FAIL','REAL_DEF_OFFSET_CLASS_BYTES_MATCH' if valid else 'REAL_DEF_ALIAS_BYTES_DIFFER',native_offset_class_size=len(members)))
 for entry in config.get('static_regions',[]):
  keys=entry['keys'];space=entry.get('coordinate_space','source_pixels')
  if entry.get('reference_key') and not available([entry['reference_key']]):
   event('static_region_lock',keys,finding('NOT_MEASURED','REFERENCE_SLOT_MISSING_OR_UNDECODABLE'));continue
  anchor=array(entry['reference_key'],space) if entry.get('reference_key') else rgba(entry['reference'])
  selected=mask(entry['region'],(anchor.shape[1],anchor.shape[0]),space)
  common.need(entry.get('region_role','static') in ('static','dynamic_allowed'),'STATIC_REGION_ROLE_INVALID')
  if entry.get('region_role','static')=='dynamic_allowed':selected=~selected;common.need(selected.any(),'EMPTY_STATIC_REMAINDER')
  for k in keys:
   if not available([k]):
    event('static_region_lock',[k],finding('NOT_MEASURED','SOURCE_SLOT_MISSING_OR_UNDECODABLE'));continue
   candidate=array(k,space)
   if candidate.shape!=anchor.shape:result=finding('NOT_MEASURED','STATIC_REGION_ALIGNMENT_REQUIRED')
   else:
    exact=entry.get('exact',False);m=compare_region(anchor,candidate,selected,exact,entry.get('channel_tolerance',0 if exact else 6))
    bad=m['changed_fraction']>entry.get('changed_fraction_hint',0 if exact else .01)
    result=finding('FAIL' if bad and exact else 'RISK' if bad else 'PASS','LOCKED_STATIC_REGION_CHANGED' if bad else 'STATIC_REGION_COMPARED',**m)
   event('static_region_lock',[k],result)
 seam_specs=list(config.get('neighbor_edges',[]))
 if preset=='ground' and config.get('seamless_repeat') is True:
  for k in bykey:
   seam_specs.extend([{'a_key':list(k),'b_key':list(k),'a_edge':'east','b_edge':'west'},
                      {'a_key':list(k),'b_key':list(k),'a_edge':'south','b_edge':'north'}])
 for seam in seam_specs:
  a,b=seam['a_key'],seam['b_key'];space=seam.get('coordinate_space','source_pixels')
  result=compare_edges(array(a,space),array(b,space),seam) if available([a,b]) else finding('NOT_MEASURED','SEAM_SOURCE_MISSING_OR_UNDECODABLE')
  event('registered_neighbor_seams',[a,b],result)
 if preset=='ground' and not seam_specs and config.get('seamless_repeat') is False:
  checks['registered_neighbor_seams']=finding('NOT_APPLICABLE','EXPLICIT_NON_TILING_GROUND')
 for sequence in config.get('sequences',[]):
  keys=sequence['ordered_keys'];common.need(len(keys)>=2 and len({tuple(k) for k in keys})==len(keys),'EXPLICIT_ORDERED_SEQUENCE_REQUIRED')
  common.need(all(k[:3]==keys[0][:3] for k in keys) and [k[3] for k in keys]==sorted(k[3] for k in keys),'ORIGINAL_GROUP_FRAME_ORDER_REQUIRED')
  common.need([k[3] for k in keys]==list(range(keys[0][3],keys[-1][3]+1)),'SEQUENCE_CANNOT_SKIP_ORIGINAL_INTERMEDIATE_FRAMES')
  pairs=[];space=sequence.get('coordinate_space','source_pixels')
  for left,right in zip(keys,keys[1:]):
   if not available([left,right]):
    event('ordered_motion',[left,right],finding('NOT_MEASURED','MOTION_SOURCE_MISSING_OR_UNDECODABLE'));continue
   a,b=array(left,space),array(right,space)
   if a.shape!=b.shape:
    event('ordered_motion',[left,right],finding('NOT_MEASURED','MOTION_REGISTRATION_REQUIRED'));continue
   m=motion_metrics(a,b);risk=m['premultiplied_rgba_mean_difference']>sequence.get('frame_difference_hint',.2)
   pairs.append(m['premultiplied_rgba_mean_difference'])
   event('ordered_motion',[left,right],finding('RISK' if risk else 'PASS','FRAME_CHANGE_DIAGNOSTIC',**m))
  if sequence.get('loop') is False:event('loop_boundary',keys,finding('NOT_APPLICABLE','EXPLICIT_NON_LOOP_SEQUENCE'))
  elif sequence.get('loop') is True:
   if not available([keys[-1],keys[0]]):
    event('loop_boundary',keys,finding('NOT_MEASURED','LOOP_SOURCE_MISSING_OR_UNDECODABLE'));continue
   a,b=array(keys[-1],space),array(keys[0],space)
   if a.shape!=b.shape:event('loop_boundary',keys,finding('NOT_MEASURED','LOOP_REGISTRATION_REQUIRED'))
   else:
    m=motion_metrics(a,b);baseline=float(np.median(pairs)) if pairs else 0
    risk=m['premultiplied_rgba_mean_difference']>max(sequence.get('loop_absolute_hint',.08),baseline*sequence.get('loop_relative_hint',3))
    event('loop_boundary',keys,finding('RISK' if risk else 'PASS','LOOP_EDGE_CHANGE_DIAGNOSTIC',adjacent_median_difference=baseline,native_timing_verified=False,**m))
 for sequence in config.get('flow_sequences',[]):
  keys=sequence['ordered_keys'];space=sequence.get('coordinate_space','source_pixels')
  common.need(len(keys)>=2 and len({tuple(k) for k in keys})==len(keys),'FLOW_SEQUENCE_KEYS_REQUIRED')
  common.need(all(k[0]=='def' and k[:3]==keys[0][:3] for k in keys) and [k[3] for k in keys]==list(range(keys[0][3],keys[-1][3]+1)),'ORIGINAL_FLOW_FRAME_ORDER_REQUIRED')
  for left,right in zip(keys,keys[1:]):
   if not available([left,right]):
    event('semantic_flow_direction',[left,right],finding('NOT_MEASURED','FLOW_SOURCE_MISSING_OR_UNDECODABLE'));continue
   a,b=array(left,space),array(right,space)
   if a.shape!=b.shape:result=finding('NOT_MEASURED','FLOW_REGISTRATION_REQUIRED')
   else:
    region=mask(sequence['region'],(a.shape[1],a.shape[0]),space)
    result=flow_metrics(a,b,region,sequence['expected_direction'],sequence.get('maximum_shift_pixels',6))
   event('semantic_flow_direction',[left,right],result)
 for item in items:
  if 'native_registration' in checks and frames.registered and tuple(item['key']) in frames.registered and item['source']:
   array3=frames.get(item['key'],'native_canvas_3x');geometry=native[tuple(item['key'])];ox,oy=geometry['anchor'];w,h=geometry['crop']
   positive=array3[:,:,3]>0;inside=np.zeros(positive.shape,dtype=bool);inside[oy*3:(oy+h)*3,ox*3:(ox+w)*3]=True
   outside=int((positive&~inside).sum())
   event('native_registration',[item['key']],finding('RISK' if outside else 'PASS','BOUND_NATIVE_VIEW_CROP_CHECK',positive_alpha_outside_crop=outside,
     original_canvas=geometry['canvas'],original_crop=geometry['crop'],original_origin=geometry['anchor'],formal_3x_export_verified=False,game_verified=False))
 for measurement in profile.get('body_measurements',[]):
  if 'ground_anchor' not in checks or 'anchor_source' not in measurement:continue
  k=measurement['key'];reg=frames.registration(k)
  if not reg:continue
  common.need(measurement['image_sha256']==bykey[tuple(k)]['source']['sha256'],'GROUND_ANCHOR_SOURCE_SHA_DRIFT')
  point=common.finite_pair(measurement['anchor_source'],'PHYSICAL_ANCHOR_REQUIRED')
  target=profile['directions'][measurement['profile_direction']]['anchor_native_canvas']
  source_rect=reg[0].get('source_rect',[0,0,*bykey[tuple(k)]['pixels']])
  observed=[(point[i]-source_rect[i]+reg[2][i])/reg[1] for i in (0,1)];deviation=[observed[i]-target[i] for i in (0,1)]
  risk=max(abs(v) for v in deviation)>profile.get('rules',{}).get('ground_anchor_hint_native',1)
  event('ground_anchor',[k],finding('RISK' if risk else 'PASS','INDEPENDENT_PHYSICAL_ANCHOR_COMPARED',observed_native=observed,target_native=target,deviation_native=deviation,origin_y_not_used_as_ground=True))
 if preset=='adventure-flags':
  for item in items:
   k=item['key']
   if not available([k]):
    for chk in CHECKS['adventure-flags']:event(chk,[k],finding('NOT_MEASURED','SOURCE_SLOT_MISSING_OR_UNDECODABLE'))
    continue
   arr=array(k,'source_pixels');fm=flag_metrics(arr,config)
   c_st=('PASS' if fm.get('canvas_pass') else 'FAIL') if 'expected_canvas' in config else 'NOT_MEASURED'
   event('flag_canvas_uniformity',[k],finding(c_st,'FLAG_CANVAS_GEOMETRY_EVALUATED',canvas_pass=fm.get('canvas_pass')))
   p_st=('PASS' if fm.get('pole_axis_pass') else 'RISK') if 'expected_pole_x' in config and fm.get('pole_measured') else 'NOT_MEASURED'
   event('flag_pole_axis_alignment',[k],finding(p_st,'FLAG_POLE_AXIS_EVALUATED',deviation=fm.get('pole_axis_deviation'),target=config.get('expected_pole_x',205)))
   conn_st=('PASS' if fm.get('connection_purity_pass') else 'RISK') if fm.get('pole_measured') else 'NOT_MEASURED'
   event('flag_pole_connection_purity',[k],finding(conn_st,'FLAG_CONNECTION_PURITY_EVALUATED',white_halo_pixels=fm.get('white_halo_pixels')))
   sz=fm.get('entity_size',[0,0]);target_h=config.get('expected_entity_height',1100);h_dev=abs(sz[1]-target_h)
   sz_st=('PASS' if h_dev<=config.get('height_tolerance',60) else 'RISK') if 'expected_entity_height' in config else 'NOT_MEASURED'
   event('flag_size_consistency',[k],finding(sz_st,'FLAG_SIZE_CONSISTENCY_EVALUATED',entity_size=sz,target_height=target_h,height_deviation=h_dev))
   pur_st=('PASS' if fm.get('purity_pass') else 'RISK') if fm.get('pole_measured') else 'NOT_MEASURED'
   event('flag_alpha_transparency_purity',[k],finding(pur_st,'FLAG_ALPHA_PURITY_EVALUATED',margin_stray_pixels=fm.get('margin_stray_pixels')))
 if preset=='boats':
  gm_arr=None;gm_key=config.get('gold_master_key')
  if gm_key and available([gm_key]):gm_arr=array(gm_key,'source_pixels')
  elif any(k[-1] in (2,'2_0.png') for k in bykey):
   gm_k=next(k for k in bykey if k[-1] in (2,'2_0.png'))
   if available([gm_k]):gm_arr=array(gm_k,'source_pixels')
  for item in items:
   k=item['key']
   if not available([k]):
    for chk in CHECKS['boats']:event(chk,[k],finding('NOT_MEASURED','SOURCE_SLOT_MISSING_OR_UNDECODABLE'))
    continue
   arr=array(k,'source_pixels');bm=boat_metrics(arr,k,gold_master_arr=gm_arr,config=config)
   c_st=('PASS' if bm.get('canvas_pass') else 'FAIL') if 'expected_canvas' in config else 'NOT_MEASURED'
   event('boat_canvas_uniformity',[k],finding(c_st,'BOAT_CANVAS_GEOMETRY_EVALUATED',canvas_pass=bm.get('canvas_pass'),canvas=list(arr.shape[:2][::-1])))
   # The bottom alpha envelope may include wakes or decorative pixels. It is
   # not a physical waterline; bind an explicit source-SHA anchor instead.
   anchors=[a for a in config.get('waterline_anchors',[]) if a.get('key')==k]
   common.need(len(anchors)<=1,'BOAT_WATERLINE_ANCHOR_AMBIGUOUS')
   deviation=None
   if anchors:
    anchor=anchors[0];common.need(anchor.get('image_sha256')==bykey[tuple(k)]['source']['sha256'],'BOAT_WATERLINE_ANCHOR_SHA_DRIFT')
    point=common.finite_pair(anchor.get('point_source'),'BOAT_PHYSICAL_ANCHOR_REQUIRED')
    common.need(0<=point[0]<arr.shape[1] and 0<=point[1]<arr.shape[0],'BOAT_WATERLINE_ANCHOR_OUTSIDE_SOURCE')
    if 'expected_keel_y' in config:deviation=abs(point[1]-config['expected_keel_y'])
   w_st=('PASS' if deviation<=config.get('keel_y_tolerance',60) else 'RISK') if deviation is not None else 'NOT_MEASURED'
   event('boat_waterline_alignment',[k],finding(w_st,'SOURCE_BOUND_WATERLINE_ANCHOR' if deviation is not None else 'PHYSICAL_WATERLINE_ANCHOR_OR_TARGET_MISSING',deviation=deviation))
   ratios=config.get('expected_width_ratios',{});expected=ratios.get(token(k));observed=bm.get('observed_ratio') if gm_arr is not None else None
   if expected is not None:common.need(type(expected) in (int,float) and math.isfinite(expected) and expected>0,'BOAT_DECLARED_WIDTH_RATIO_INVALID')
   v_st=('PASS' if abs(observed-expected)<=config.get('volume_tolerance',.16) else 'RISK') if observed is not None and expected is not None else 'NOT_MEASURED'
   event('boat_perspective_volume',[k],finding(v_st,'DECLARED_WIDTH_RATIO_PROXY_NOT_3D_VOLUME',observed_ratio=observed,expected_ratio=expected,three_dimensional_volume_verified=False))
   pur_st='PASS' if bm.get('purity_pass') else 'RISK'
   event('boat_alpha_transparency_purity',[k],finding(pur_st,'BOAT_ALPHA_PURITY_EVALUATED',fuzzy_water_pixels=bm.get('fuzzy_water_pixels')))
 for item in items:
  if item.get('body_scale'):
   n='rider_and_mount_body_scale' if preset=='mounted-heroes' else 'body_scale'
   result=item['body_scale'];event(n,[item['key']],finding('RISK' if result['status']=='BODY_SCALE_REVIEW_REQUIRED' else 'PASS','SOURCE_BOUND_ANATOMICAL_PATHS',**{k:v for k,v in result.items() if k!='status'}))
  for name,results in item['domain_checks'].items():
   if any(r['status']=='FAIL' for r in results):item['errors'].append('DOMAIN_'+name.upper())
   elif any(r['status']=='RISK' for r in results):item['risks'].append('DOMAIN_'+name.upper())
 for name in checks:
  matching=[x for x in events if x['check']==name]
  if matching:
   states={x['status'] for x in matching};status=next(s for s in ('FAIL','RISK','NOT_MEASURED','PASS','NOT_APPLICABLE') if s in states)
   checks[name]=finding(status,'EXECUTED_BOUND_STRATEGY',events=len(matching))
 if preset=='roads-rivers' and config.get('animation_applicable') is False:
  common.need(not config.get('sequences') and not config.get('flow_sequences'),'STATIC_ROAD_CANNOT_DECLARE_ANIMATED_SEQUENCES')
  for name in ('ordered_motion','loop_boundary','semantic_flow_direction'):
   checks[name]=finding('NOT_APPLICABLE','EXPLICIT_STATIC_ROAD_OR_RIVER_RESOURCE')
 if preset=='ground':
  measured=[x for x in items if 'visible_lab_mean' in x]
  if measured:
   risk=any(any('COLOR' in r for r in x['risks']) for x in measured)
   checks['material_consistency']=finding('RISK' if risk else 'PASS','MATERIAL_STATISTICS_COMPUTED_NOT_SEMANTIC_APPROVAL',measured_slots=len(measured),total_slots=len(items))
 # A successful subset cannot certify frames which received no event.
 for name in checks:
  measured={tuple(k) for e in events if e['check']==name and e['status']!='NOT_MEASURED' for k in e['keys']}
  unknown={tuple(k) for e in events if e['check']==name and e['status']=='NOT_MEASURED' for k in e['keys']}
  measured-=unknown
  if name=='material_consistency':measured={tuple(x['key']) for x in items if 'visible_lab_mean' in x}
  if checks[name]['reason'] in ('EXPLICIT_NON_TILING_GROUND','EXPLICIT_STATIC_ROAD_OR_RIVER_RESOURCE'):measured=set(bykey)
  missing=[list(k) for k in bykey if k not in measured]
  checks[name].update(covered_slots=len(measured),total_slots=len(items),unmeasured_keys=missing)
  if missing and checks[name]['status'] in ('PASS','NOT_APPLICABLE'):checks[name]['status']='NOT_MEASURED'
 return {'preset':preset,'checks':checks,'events':events,'source_images_modified':False,'images_written':0,
         'native_timing_verified':False,'semantic_model_used':False}
