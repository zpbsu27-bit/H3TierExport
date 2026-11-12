"""Executable production-profile templates, subordinate to the pinned art standard."""
from __future__ import annotations
import collections
import gzip
import hashlib
import io
import json
import math
from pathlib import Path
import numpy as np
from PIL import Image
import subject_scale_layout as common

ROAD_FAMILIES=('COBBRD','DIRTRD','GRAVRD')

ROAD_SIDES=('north','east','south','west')
ROAD_MIRROR_STATES=(
 {'id':'none','horizontal':False,'vertical':False},
 {'id':'horizontal','horizontal':True,'vertical':False},
 {'id':'vertical','horizontal':False,'vertical':True},
 {'id':'horizontal+vertical','horizontal':True,'vertical':True},
)


def _road_edge_runs(bits):
 active=[i for i,value in enumerate(bits) if value=='1']
 if not active:return []
 runs=[];start=previous=active[0]
 for index in active[1:]:
  if index!=previous+1:runs.append([start,previous+1]);start=index
  previous=index
 runs.append([start,previous+1]);return runs


def _road_edge_bits(alpha):
 h,w=alpha.shape
 values={'north':alpha[0,:],'east':alpha[:,-1],'south':alpha[-1,:],'west':alpha[:,0]}
 result={}
 for side,edge in values.items():
  bits=''.join('1' if int(value)>0 else '0' for value in edge)
  result[side]={'alpha_positive_bits':bits,'contact_runs':_road_edge_runs(bits),
                'positive_pixels':bits.count('1')}
 return result


def _road_connection_model(scope,native,inventory_ref):
 """Freeze original per-cell roadSprite geometry and H/V mirror semantics."""
 common.need(isinstance(inventory_ref,dict),'ROAD_NATIVE_INVENTORY_REQUIRED')
 inv_path,inv_raw=common.pin(inventory_ref)
 inventory=json.loads(gzip.decompress(inv_raw) if inv_path.suffix.lower()=='.gz' else inv_raw)
 marker='codex-map'
 common.need(marker in inv_path.parts and inv_path.name=='native-resource-inventory.json.gz',
             'ROAD_NATIVE_CODEX_MAP_INVENTORY_REQUIRED')
 marker_index=inv_path.parts.index(marker);map_root=Path(*inv_path.parts[:marker_index+1])
 release_root=map_root.parent;project_root=release_root.parents[2]
 frame_root=map_root/'sources'/'complete'/'frames'
 api_sources=[]
 for rel,needles in (
   ('H3Feature/vendor/H3API/include/h3api/H3AdventureMap/H3MapItems.hpp',('roadSprite','roadMirrorH','roadMirrorV')),
   ('H3Feature/vendor/H3API/include/h3api/H3RMG/H3RmgGroundTileData.hpp',('roadSprite','mirrorRoadH','mirrorRoadV'))):
  source=project_root/rel
  common.need(source.is_file(),'ROAD_MAP_TRANSFORM_EVIDENCE_MISSING')
  raw=source.read_bytes();text=raw.decode('utf-8',errors='strict')
  common.need(all(word in text for word in needles),'ROAD_MAP_TRANSFORM_EVIDENCE_CHANGED')
  api_sources.append({'path':str(source.resolve()),'sha256':hashlib.sha256(raw).hexdigest(),
                      'required_markers':list(needles),'role':'ORIGINAL_MAP_PER_TILE_ROAD_SPRITE_AND_MIRROR_FIELDS'})
 families=sorted({key[1] for key in scope})
 common.need(families and set(families)<=set(ROAD_FAMILIES),'ROAD_IDENTITY_OUTSIDE_OFFICIAL_ROAD_FAMILIES')
 expected={('def',identity,0,frame) for identity in families for frame in range(17)}
 common.need({tuple(key) for key in scope}==expected,'ROAD_CONNECTION_REQUIRES_COMPLETE_17_SLOT_FAMILIES')
 lod_entries={}
 for row in inventory.get('all_lod_entries',[]):
  if len(row)>=5 and str(row[0]).upper()=='H3SPRITE.LOD':
   lod_entries.setdefault(str(row[1]).upper(),[]).append(row)
 model_slots=[]
 for identity in families:
  entries=lod_entries.get(identity+'.DEF',[])
  common.need(len(entries)==1,'ROAD_NATIVE_DEF_LOD_ENTRY_AMBIGUOUS')
  entry=entries[0]
  def_binding={'lod_name':entry[0],'resource':entry[1],'offset':entry[2],
               'stored_bytes':entry[3],'sha256':entry[4]}
  for key in sorted((tuple(x) for x in scope if x[1]==identity),key=lambda x:x[3]):
   row=native[key];image=frame_root/identity/f'{key[2]}_{key[3]}.png'
   common.need(image.is_file(),'ROAD_OFFICIAL_ALPHA_FRAME_MISSING')
   raw=image.read_bytes();binding={'path':str(image.resolve()),'sha256':hashlib.sha256(raw).hexdigest()}
   with Image.open(image) as opened:
    opened.load();common.need(opened.format=='PNG' and opened.size==tuple(row['canvas']),'ROAD_NATIVE_ALPHA_GEOMETRY_DIFFERS')
    rgba=np.asarray(opened.convert('RGBA'))
   alpha=rgba[:,:,3];edge_profiles=_road_edge_bits(alpha)
   model_slots.append({'key':list(key),'official_resource':row['resource'],
     'native_geometry':{'canvas_1x':row['canvas'],'crop_1x':row['crop'],'origin_1x':row['anchor']},
     'def_lod_entry':def_binding,'native_frame':binding,
     'native_alpha_sha256':hashlib.sha256(alpha.tobytes()).hexdigest(),
     'edge_profiles':edge_profiles,'role':'OFFICIAL_ALPHA_GEOMETRY_ONLY_RGB_NOT_USED'})
 model={'schema':'h3.road-connection-model.v1','model_version':1,
        'native_inventory':inventory_ref,'inventory_input_digest':inventory.get('input_digest'),
        'original_map_transform_sources':api_sources,
        'map_cell_contract':{'road_type_and_sprite_are_per_cell':True,
          'orientation_flags':['roadMirrorH','roadMirrorV'],
          'allowed_transforms':[dict(row) for row in ROAD_MIRROR_STATES],
          'grid_adjacency':'four-neighbor-cardinal; editor frame selection can also inspect diagonals',
          'port_match':'native alpha is diagnostic; actual3X requires a frozen shared interface contract',
          'logical_cell':[32,32],'road_render_origin_in_cell':[0,16],'road_render_origin_3x':[0,48],
          'road_rendering':'current upper source half at destination y=16; prior lower source half at destination y=0',
          'arbitrary_rotation_allowed':False,'nonuniform_stretch_allowed':False},
        'road_families':families,'slots':model_slots,
        'native_rgb_used_as_paint':False,'native_masks_used_only_for_geometry':True,
        'actual3x_and_underlay_required_for_full_art_acceptance':True}
 canonical=json.dumps(model,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode('utf-8')
 model['model_sha256']=hashlib.sha256(canonical).hexdigest()
 return model

INPUTS={
 'creature-actions':{
  'native_defs':'真实DEF路径和SHA：核同偏移别名，不从图片相似度猜槽位',
  'body_scale':'同姿势/朝向的身体路径、目标、源图SHA与原生注册证据；武器和法术不当身体尺度',
  'sequences':'按官方组帧排序的序列、是否循环、共同坐标系；死亡单独成类',
  'semantic_structure':'身份、完整人物数量、多肢少肢需要经验证的语义模型或一次AI/人眼判断'},
 'town-animation':{
  'static_regions':'绑定底图和固定底座区域，或声明允许变化的动态区域；区域坐标和画布尺寸必须明确',
  'sequences':'同一底图注册后的官方帧顺序与循环属性，不能按文件字典序猜播放',
  'native_registration':'当前源SHA对应的真实native-review报告，只用现有3X视图'},
 'mounted-heroes':{
  'body_scale':'骑手、坐骑分别定义身体路径并合看整体；按侧/正/背朝向分族，不用尾巴和长兵器框测量',
  'ground_anchor':'源图坐标中的真实蹄底接地点和同档案目标，不把DEF origin当脚底',
  'native_registration':'当前源SHA对应的实际注册报告；工作画布与原生裁片分开',
  'semantic_structure':'骑手/坐骑身份、遮挡与肢体检查需语义依据，Alpha连通块不是腿的数量'},
 'roads-rivers':{
  'neighbor_edges':'真实相邻图和端口方向/区间/反向关系；不把任意两幅图当相邻瓦片',
  'sequences':'动态水流的官方顺序与循环标志；静态道路不套用水流检查',
  'flow_sequences':'有纹理区域、期望流向与有界位移；无纹理/匹配歧义要报告未测量'},
 'road-connection':{
  'editor_source_proof':'SHA绑定的VCMI源函数离屏验证回执；仅证明选帧及绘制调用几何，不替代画面接缝验收',
  'road_connection_model':'自动从当前官方inventory、H3sprite DEF映射和原版alpha边界冻结端口模型；每格绑定roadSprite及H/V镜像状态',
  'registered_3x':'每槽SHA绑定的native-review 3X完整画布及原生canvas/crop/origin；缺图不计通过',
  'road_scenes':'明确给出地图格坐标、官方槽位和H/V镜像；横纵相邻格按原版端口模型校验',
  'terrain_underlay':'绑定实际3X底图格并检查合成场景；缺底图只报告未测量',
  'surface_interface_policy':'绑定共享接口构件或经审核的Delta-E容限；缺颜色合同则界面颜色记未测量'},
 'ground':{
  'neighbor_edges':'声明可重复铺贴或具体相邻边关系；不铺贴的素材显式seamless_repeat=false',
  'material_consistency':'同材质真实参照与色差提示；不要求不同地貌都染成一个颜色'},
 'adventure-flags':{
  'flag_canvas_uniformity':'显式声明expected_canvas(如900x1200)，同批8色旗帜使用共同工作坐标；缺目标记未测',
  'flag_pole_axis_alignment':'显式声明expected_pole_x与pole_rows_y测量带(如X=205±10)；带内没有旗杆时记未测，不按bbox猜旗杆',
  'flag_pole_connection_purity':'旗杆与旗面连接处纯净，无白色毛边光晕与镂空缝隙',
  'flag_size_consistency':'旗帜实体总高度与宽度在数学尺度上自洽(方差<=5%)',
  'flag_alpha_transparency_purity':'边缘保留真实透明安全边距，无四周脏像素飞溅'},
 'boats':{
  'boat_canvas_uniformity':'统一工作画布(1448x1086)，各朝向在同一基准画布下排布',
  'boat_waterline_alignment':'声明expected_keel_y及逐槽waterline_anchors，锚点绑定源SHA；Alpha外框底部不是物理吃水线',
  'boat_perspective_volume':'声明gold_master_key和逐槽expected_width_ratios；只报告投影宽度代理，不凭bbox宣称三维体积已验证',
  'boat_alpha_transparency_purity':'吃水线及船体边缘纯净，消除水面杂色与半透明絮状毛刺'},
}


def template(preset,scope,native,inventory_ref=None):
 groups=collections.defaultdict(list)
 for k in scope:
  if k[0]=='def':groups[(k[1],k[2])].append(k)
 sequences=[{'ordered_keys':sorted(keys,key=lambda k:k[3]),'coordinate_space':'source_pixels','loop':None}
            for keys in groups.values() if len(keys)>1]
 # Unreviewed sequence ideas are kept out of executable strategies.
 strategies={}
 if preset=='road-connection':
  model=_road_connection_model(scope,native,inventory_ref)
  strategies['road_connection']={'model':model,'scenes':[],'terrain_underlays':[],
      'surface_interface_policy':None,'actual3x_alpha_threshold':128}
 result={'strategies':strategies,'strategy_setup':{'required_inputs':INPUTS[preset],
          'official_sequence_candidates':sequences,
          'coordinate_spaces':['source_pixels','native_canvas_3x'],
          'native_geometry_is_authoritative':True,
          'road_connection_model_sha256':strategies.get('road_connection',{}).get('model',{}).get('model_sha256')},
         'drawing_contract':{'status':'DRAFT_REQUIRES_REFERENCE_AND_SCALE_TARGETS',
          'one_slot_per_image':True,'nonuniform_stretch_allowed':False,
          'low_resolution_upscale_as_master_allowed':False,
          'working_canvas_pixels':None,'transparent_safety_margin_pixels':None,
          'note':'统一工作画布和身体尺度，保留动作伸展；正式槽位画布/crop/origin按官方逐帧注册，不强制所有最终裁片尺寸相同。'}}
 # New briefs opt in explicitly; frozen legacy profiles retain their old text.
 if preset in ('roads-rivers','road-connection'):
  result['road_authoring_brief_revision']='road-seam-lessons-20261102-v1'
  result['road_drawing_guides']=[]
 return result


def road_seam_brief(profile):
 """User-facing prepainting guidance, never an image or art approval."""
 if profile.get('road_authoring_brief_revision')!='road-seam-lessons-20261102-v1':return []
 if profile.get('preset') not in ('roads-rivers','road-connection'):return []
 families={str(key[1]).upper() for key in profile.get('expected_slots',[]) if len(key)>1}
 if not families.intersection(ROAD_FAMILIES):return []
 lines=['','## 道路系列画前与接缝要求（适用于本档案中的道路）',
  '先确认系列的高清风格参考及其SHA，官方2015 HD 3X只用于参考轮廓和走势，不复制低清笔触；每次生成一张，真实透明RGBA，保留原始生成图。',
  '设计整组直线、转弯、T口、十字和尽头的中心线及接口；直线保持直，弯道弧度自然。统一道路有效宽度与位置，横竖、转弯及不同道路系列使用共同宽度目标；同系列保持材质、颗粒尺度和色调一致，不同系列保留各自颜色。',
  '边缘减少过多锯齿和毛刺，保留自然细微起伏；道路两侧及转弯内侧保留清晰连续的路肩细节，不能抹掉或断掉。',
  '先做可复用描点/轮廓及合法拼接布局，形状和宽度确认后逐张生图；同源弯道可经允许的整格H/V镜像复用，仍逐槽按真实canvas/crop/origin登记与检查。',
  '原图只在独立处理层按固定坐标装配彩绘接口并正式采样；不拉伸石块、不以模糊隐藏接缝、不把处理候选倒灌成高清原始母版。',
  '接口线像素一致只是局部技术证据；还需检查多行过渡、石纹重复、颜色、宽度、两侧路肩、四格交点和合法镜像，使用不同底图及正常3X尺寸。',
  '受控编辑器实际显示与游戏运行分别核验；仅源码模型、离线图或编辑器截图不能互相替代。画前简报和参考图辅助生图，不能保证模型精确遵循。']
 if 'COBBRD' in families:
  lines.append('COBBRD碎石路风格锁定：路面中间有体积感的较大石块与细碎石，两侧保留可见且相对较小的石子路肩；保持已确认参考的欧美写实奇幻2.5D材质、石块形状、色调及路肩样式，只为道路宽度一致和接缝吻合做必要调整。')
 guides=profile.get('road_drawing_guides',[])
 common.need(isinstance(guides,list),'ROAD_GUIDES_LIST_REQUIRED')
 scope={tuple(key) for key in profile.get('expected_slots',[])};seen=set()
 lines.extend(['','## 已冻结的逐槽生图参考（辅助，不是像素锁定）'])
 for guide in guides:
  common.need(isinstance(guide,dict),'ROAD_GUIDE_ENTRY_REQUIRED')
  key=guide.get('key');role=guide.get('role')
  common.need(isinstance(key,list) and len(key)==4 and all(type(v) in (str,int) for v in key)
              and tuple(key) in scope,'ROAD_GUIDE_SLOT_OUTSIDE_SCOPE')
  common.need(role in ('geometry','style'),'ROAD_GUIDE_ROLE_INVALID')
  tag=(tuple(key),role);common.need(tag not in seen,'ROAD_GUIDE_DUPLICATE_ROLE');seen.add(tag)
  binding=guide.get('source');path,raw=common.pin(binding)
  with Image.open(io.BytesIO(raw)) as image:
   common.need(image.format=='PNG' and image.width>0 and image.height>0,'ROAD_GUIDE_PNG_REQUIRED')
   image.verify()
  lines.append(f"- {key[1]}/{key[2]}_{key[3]}.png：{'描点/轮廓' if role=='geometry' else '材质风格'}参考 {path}；SHA-256 {binding['sha256']}。")
 if not guides:lines.append('尚未冻结逐槽描点或风格参考；需要时由制作人绑定路径和SHA。')
 lines.append('这些路径是需显式上传或由已实现的适配器提交的参考；简报不会自动上传，也不保证模型逐点遵守。')
 return lines


def readiness(profile):
 missing=[];preset=profile['preset'];contract=profile.get('drawing_contract',{})
 for ref in profile.get('reference_images',[]):common.pin(ref)
 road_seam_brief(profile)  # Validate optional, slot-bound guides on every readiness check.
 canvas=contract.get('working_canvas_pixels');margin=contract.get('transparent_safety_margin_pixels')
 if canvas is not None:common.need(isinstance(canvas,list) and len(canvas)==2 and all(type(v) is int and 1<=v<=8192 for v in canvas),'WORKING_CANVAS_INVALID')
 if margin is not None:common.need(type(margin) is int and margin>=0 and (not canvas or margin*2<min(canvas)),'WORKING_SAFETY_MARGIN_INVALID')
 for rule in profile.get('directions',{}).values():
  lengths=rule.get('lengths_native',{})
  common.need(lengths and all(isinstance(v,(int,float)) and math.isfinite(v) and v>0 for v in lengths.values()),'POSITIVE_BODY_TARGET_REQUIRED')
  common.need(rule.get('primary_metric') in lengths and rule.get('pose_class'),'COMPARABLE_BODY_PROFILE_REQUIRED')
 if not profile.get('reference_images'):missing.append('冻结真实高清形象参考（路径/SHA）')
 if preset in ('creature-actions','mounted-heroes') and not profile.get('directions'):missing.append('按姿势/朝向核准身体尺度目标与独立接地点')
 if not contract.get('working_canvas_pixels'):missing.append('核准统一工作画布')
 if contract.get('transparent_safety_margin_pixels') is None and preset!='ground':missing.append('核准透明安全边距')
 reviewed=profile.get('review_status')=='assistant_scale_profile_pass'
 return {'status':'DRAFT' if missing else 'READY_FROM_REVIEWED_PROFILE' if reviewed else 'INPUTS_DECLARED_REQUIRES_ART_REVIEW',
         'missing_inputs':missing,'automatic_generation_approval':False}


def brief_lines(profile):
 preset=profile['preset'];r=readiness(profile);d=profile.get('drawing_contract',{})
 lines=['','## 开工条件与同档案复检',f"准备状态：{r['status']}。",
        f"统一工作画布：{d.get('working_canvas_pixels')}；透明安全边距：{d.get('transparent_safety_margin_pixels')}。",
        '工作画布用于生成布局；正式每帧canvas/crop/origin仍按原生槽位，不用拉伸或擦Alpha挤进槽位。']
 lines.extend('- 尚需：'+x for x in r['missing_inputs'])
 lines.extend(['','## 本类别的专属检测依据'])
 lines.extend(f'- {name}：{description}' for name,description in INPUTS[preset].items())
 lines.extend(['','结果分为确定错误、诊断风险、未测量和不适用。缺少输入不记通过；风险不自动删除或重绘。',
               '同一制作档案在画前生成本文、画后冻结当前文件SHA执行检测；修改图片后重新冻结，不覆盖旧报告。'])
 return lines


def authoring_rules(preset):
 return {
  'creature-actions':[
   '按身份、形态、官方动作和朝向绑定真实参考。单张只画一个人物和一个槽位；保持真实透明，完整保留该阶段应有结构。',
   '身体尺度使用同档案的身体路径、可比姿势和独立接地点。武器/翅膀/披风不代表身体；姿势伸展不等于缩放。',
   '死亡/消散/破碎按官方阶段及材质表达，不套用活体肢体数量。肢体和身份需真实语义判断，像素粗筛不冒充通过。'],
  'mounted-heroes':[
   '单张一个骑乘组合、一个官方槽位；同朝向分别保持骑手与坐骑身体比例，装备、马尾和长兵器另计容纳空间。',
   '按身体路径及独立蹄底校准，不能用全图外框或不同图片各自fit满窗口判断大小。姿势/朝向分族，避免非等比拉伸。',
   '保持真实透明、完整坐骑/骑手结构及正确遮挡；身份/多肢少肢需要真实语义判断。'],
  'town-animation':[
   '绑定同一真实建筑底图和官方动画帧；固定底座/静态区域锁定，允许动态区域及外延先声明。',
   '按统一建筑注册坐标保留尺寸、光影和接口。不得逐帧自行fit或移动底座，不将人物身体比例用于建筑。',
   '按官方顺序制作动态变化，显式声明是否循环；循环接头与相邻变化分别检测，不自行编造节奏。'],
  'roads-rivers':[
   '每张对应一个官方资源槽；冻结可连接邻接表、端口方向/区间、透明关系及材质参照，不能靠文件名猜邻接。',
   '保持接口在同一坐标和尺度下连通；不自动拉伸不同长度端口。静态道路明确不适用流动/循环检测。',
   '使用与游戏地图一致的2.5D俯斜视角和共同光照；通过细小石块体积、接触阴影和材质明暗呈现浅浮雕感，画面仍是平面透明精灵，不凭空指定未核实的相机角度。',
   '原版整张RGBA会随合法H/V状态镜像，强单向投影阴影也会翻转。现阶段采用柔和环境光、克制高光和短接触阴影，逐一检查镜像后的观感；未实现法线重打光时不得宣称世界光向完全一致。',
   '碎石路路肩整体干净连续，边缘自然轻微起伏，允许零星小石子但不得密集散落；不要裁成过分平滑的刀切轮廓。',
   '动态水流使用官方顺序、已声明纹理区域及期望流向；无纹理或匹配歧义不得宣称方向正确。'],
   'road-connection':[
    '道路按原版地图格逐格指定roadSprite；只使用原版roadMirrorH/roadMirrorV允许的四种状态，不臆造任意旋转或非等比缩放。',
    '原版Alpha只作几何证据，不逐像素裁坏新图。实际3X接口绑定共享构件合同；道路在地图格内向下偏移半格（3X为48px），按真实地图帧号和镜像检查。',
    '碎石路路肩保持自然细微起伏和整体干净，允许零星小石子但不密集散落；不以刀切般的平滑曲线取代自然轮廓。固定同一地图2.5D俯斜视角、光向和颗粒尺度，以石块体积和克制的接触阴影表现层次。',
    '合法H/V镜像会翻转烘焙光影。使用柔和环境光、短接触阴影和克制高光，并检查四种状态；固定世界光向的法线重打光或独立朝向资源目前未实现，不以提示词冒充。',
    '先设计并验证公共高清接口；原生1X像素边缘不放大成最终轮廓。不同槽共享同一已验证接口，并在原始母版之外的正式处理层装配；不得拉伸石块、涂模糊带或仅凭提示词宣称严丝合缝。',
    '以同一策略枚举全部合法横纵邻接，并可把场景按3X原点叠到底图；底图或共享接口颜色合同缺失时分别记未测量，不猜阈值或假报通过。'],
   'ground':[
   '每张对应一个官方地面槽，使用应有的全不透明覆盖；不要求人物式透明边距、接地点或肢体结构。',
   '统一该地貌工作画布与纹理尺度，声明可平铺或具体邻接边；边界接口按同一坐标比较。',
   '按材质家族冻结真实配色参照和允许变化，不能把不同地貌强行染成同色；重复纹理与色差只作诊断。'],
  'adventure-flags':[
   '8色占领旗帜尺寸在数学尺度上绝对统一；单张只包含单面完整旗帜与立杆。',
   '旗杆主轴X严格固定，旗面与旗杆接缝处必须像素级严密且无白边光晕。',
   '旗面飘动形态自洽，各颜色质感符合奇幻写实风格，严禁低倍率放大混充。'],
  'boats':[
   '以正侧视黄金母版为空间体量基准，等轴测5个朝向遵循物理投影长度守恒。',
   '全朝向吃水线底部严格对齐同一水平基准，保证水面部署时不出现高低颠簸。',
   '船体材质、甲板细节、桅杆缆绳与帆布徽记整组连贯，消除水花噪点与未抠净底色。'],
 }[preset]
