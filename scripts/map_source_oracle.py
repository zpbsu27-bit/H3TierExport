"""Headless call-geometry oracle from pinned VCMI editor source units.

The extracted C++ function bodies are unmodified. Tiny Qt/map stand-ins record
draw calls; they do not claim to execute Qt rasterization or the whole editor.
"""
from __future__ import annotations
from tool_paths import resolve_tool_root
import argparse
import hashlib
import itertools
import json
from pathlib import Path
import re
import subprocess
import sys
import numpy as np
from PIL import Image
import map_assembly as assembly
import subject_scale_layout as common

def extract_function(text,signature):
    start=text.index(signature);opening=text.index('{',start);depth=0
    for i in range(opening,len(text)):
        if text[i]=='{':depth+=1
        elif text[i]=='}':
            depth-=1
            if depth==0:return text[start:i+1]
    raise ValueError('SOURCE_FUNCTION_NOT_CLOSED')

def parse_patterns(text):
    start=text.index('const std::vector<typename CDrawLinesOperation<T>::LinePattern>')
    end=text.index('};',start)+2;declaration=text[start:end]
    pattern=re.compile(r'\{\s*\{\s*((?:"[?+\-]"\s*,?\s*){9})\}\s*,\s*\{\s*(-?\d+)\s*,\s*(-?\d+)\s*\}\s*,\s*\{\s*(-?\d+)\s*,\s*(-?\d+)\s*\}\s*,\s*(true|false)\s*,\s*(true|false)\s*\}')
    rows=[]
    for match in pattern.finditer(declaration):
        cells=re.findall(r'"([?+\-])"',match[1])
        rows.append({'cells':cells,'road_frames':[int(match[2]),int(match[3])],
            'river_frames':[int(match[4]),int(match[5])],'allow_h':match[6]=='true','allow_v':match[7]=='true'})
    common.need(len(rows)==10,'SOURCE_LINE_PATTERN_TABLE_CHANGED')
    return rows,'template <typename T>\n'+declaration

def select_pattern(mask,patterns,kind='road'):
    occupied=[False]*9;occupied[4]=True
    for bit,index in enumerate((0,1,2,3,5,6,7,8)):occupied[index]=bool(mask&(1<<bit))
    for index,p in enumerate(patterns):
        frames=p[kind+'_frames']
        if frames[0]<0:continue
        for flip in range(4):
            if (flip&1 and not p['allow_h']) or (flip&2 and not p['allow_v']):continue
            a=np.array(p['cells']).reshape(3,3)
            if flip&1:a=a[:,::-1]
            if flip&2:a=a[::-1]
            if all(rule=='?' or (rule=='+')==occupied[i] for i,rule in enumerate(a.flat) if i!=4):
                return [index,flip,*frames]
    return [-1,0,-1,-1]

def neighborhood_mask(occupied,x,y):
    positions=((0,0),(1,0),(2,0),(0,1),(2,1),(0,2),(1,2),(2,2))
    return sum(1<<bit for bit,(dx,dy) in enumerate(positions) if (x+dx-1,y+dy-1) in occupied)

def neighbor_graph(patterns,kind='road'):
    """Enumerate the two overlapping 3x3 neighborhoods for adjacent cells.

    This is the editor-emitted graph. A consumer can render additional explicit
    map states; this graph does not reject them solely for being non-default.
    """
    graph={}
    for direction,width,height,b in (('east',4,3,(2,1)),('south',3,4,(1,2))):
        a=(1,1);external=[(x,y) for y in range(height) for x in range(width) if (x,y) not in (a,b)]
        pairs=set();unmatched=0
        for mask in range(1<<len(external)):
            occupied={a,b}|{p for bit,p in enumerate(external) if mask&(1<<bit)}
            one=select_pattern(neighborhood_mask(occupied,*a),patterns,kind)
            two=select_pattern(neighborhood_mask(occupied,*b),patterns,kind)
            if one[0]<0 or two[0]<0:unmatched+=1;continue
            for fa in range(one[2],one[3]+1):
                for fb in range(two[2],two[3]+1):pairs.add((fa,one[1],fb,two[1]))
        graph[direction]={'overlapping_neighbor_windows':1<<len(external),'unmatched_windows':unmatched,
            'pairs':[list(p) for p in sorted(pairs)]}
    return graph

def crosscheck_native_graph(spec,model):
    native=assembly.geometry_rows(spec['native_inventory']);root=Path(spec['native_frame_root'])
    pins=[];families={}
    for family in spec['road_families']:
        common.need(family in ('COBBRD','DIRTRD','GRAVRD'),'NATIVE_GRAPH_ROAD_FAMILY_INVALID')
        arrays={}
        for frame in range(17):
            k=('def',family,0,frame);common.need(k in native and native[k]['canvas']==[32,32],'NATIVE_GRAPH_SLOT_INVALID')
            p=root/family/f'0_{frame}.png';raw=p.read_bytes()
            with Image.open(p) as im:
                common.need(im.size==(32,32),'NATIVE_GRAPH_CANVAS_DIFFERS');arrays[frame]=np.asarray(im.convert('RGBA'))
            pins.append({'key':list(k),'path':str(p.resolve()),'sha256':assembly.digest(raw)})
        result={}
        for direction,graph in model['editor_emitted_road_neighbors'].items():
            differences=[];closed=[]
            for fa,ba,fb,bb in graph['pairs']:
                a=assembly.transform(arrays[fa],ba,0)[:,:,3];b=assembly.transform(arrays[fb],bb,0)[:,:,3]
                ea,eb=(a[:,-1],b[:,0]) if direction=='east' else (a[-1],b[0])
                delta=int(np.count_nonzero((ea>0)!=(eb>0)))
                if delta:differences.append({'pair':[fa,ba,fb,bb],'alpha_support_xor_pixels':delta})
                if not (ea>0).any() and not (eb>0).any():closed.append([fa,ba,fb,bb])
            result[direction]={'pairs':len(graph['pairs']),'legacy_alpha_differences':differences,
                'closed_both':closed,'exact_alpha_equality_is_art_acceptance':False}
        families[family]=result
    for ref in pins:common.pin(ref)
    return {'native_inventory':spec['native_inventory'],'native_frames':pins,'families':families,
        'interpretation':'Legacy pixel contours may differ within legal source-selected neighbors; never use exact legacy alpha equality as universal painted-art acceptance.'}

def freeze_sources(spec):
    refs=spec['sources'];texts={k:common.pin(v)[1].decode('utf-8') for k,v in refs.items()}
    for marker,value in (('HORIZONTAL',1),('VERTICAL',2),('BOTH',3)):
        common.need(re.search(r'FLIP_PATTERN_'+marker+r'\s*=\s*'+str(value)+r'\s*;',texts['map_operation_header']), 'SOURCE_FLIP_CONSTANTS_CHANGED')
    patterns,table=parse_patterns(texts['draw_lines'])
    units={}
    for signature in ('QImage flippedImage(', 'void MapHandler::drawTerrainTile(', 'void MapHandler::drawRoad(', 'void MapHandler::drawRiver('):
        units[signature]=extract_function(texts['map_handler'],signature)
    for signature in ('static bool ruleIsNone(', 'static bool ruleIsSomething(', 'static bool ruleIsAny('):
        units[signature]=extract_function(texts['draw_lines'],signature)
    for signature in ('void CDrawLinesOperation<T>::flipPattern(', 'typename CDrawLinesOperation<T>::ValidationResult CDrawLinesOperation<T>::validateTile('):
        units[signature]='template <typename T>\n'+extract_function(texts['draw_lines'],signature)
    units['pattern_table']=table
    model={'schema':'h3.map-editor-rule-model.v1','upstream_revision':spec['upstream_revision'],
        'sources':refs,'patterns':patterns,'draw_contract':assembly.DRAW_CONTRACT,
        'editor_emitted_road_neighbors':neighbor_graph(patterns),
        'editor_emitted_river_neighbors':neighbor_graph(patterns,'river'),
        'extracted_units_sha256':{k:assembly.digest(v.encode()) for k,v in units.items()},
        'source_unit_execution_scope':'draw-call geometry and line-pattern validation; Qt and editor runtime excluded'}
    model['model_sha256']=assembly.digest(json.dumps(model,sort_keys=True,separators=(',',':')).encode())
    return model,units

def validate_proof(binding,native_inventory=None):
    path,_=common.pin(binding);proof=common.doc(binding)
    common.need(proof.get('schema')=='h3.map-source-oracle.report.v1' and proof.get('status')=='PASS'
        and proof.get('line_selector_cases')==512 and proof.get('draw_geometry_cases')==14208 and not proof.get('failures'),
        'EDITOR_SOURCE_PROOF_INCOMPLETE')
    # Qualified runtime directories can move at publication. Bind the exact
    # algorithm bytes to the currently executing qualified copy, never a guess.
    for name,current in (('oracle',Path(__file__)),('assembly',Path(assembly.__file__))):
        common.pin({'path':str(current.resolve()),'sha256':proof['readers'][name]['sha256']})
    for name,sha in (('source-unit-oracle.cpp',proof['generated_cpp_sha256']),('source-unit-oracle.exe',proof['executable_sha256'])):
        common.pin({'path':str(path.parent/name),'sha256':sha})
    frozen=proof['model'];current,_=freeze_sources({'sources':frozen['sources'],'upstream_revision':frozen['upstream_revision']})
    common.need(current==frozen,'EDITOR_SOURCE_RULE_MODEL_DRIFT')
    if proof.get('native_reference_graph'):
        ref=proof['native_reference_graph']
        if native_inventory:common.need(ref['native_inventory']['sha256']==native_inventory['sha256'],'EDITOR_NATIVE_INVENTORY_DRIFT')
        common.pin(ref['native_inventory'])
        for row in ref['native_frames']:common.pin(row)
    return proof

PREFIX=r'''
// Instrumented source-unit test only. Upstream units retain GPL-2.0-or-later.
// See the source-pinned model for repository, revision and full file hashes.
#include <array>
#include <cassert>
#include <cstdint>
#include <iostream>
#include <map>
#include <memory>
#include <string>
#include <vector>
#include <utility>
#define QT_VERSION 0
#define QT_VERSION_CHECK(a,b,c) 1
using ui8=std::uint8_t;
const int tileSize=32;
struct int3{int x,y,z;int3(int a,int b,int c):x(a),y(b),z(c){}};
struct QPointF{double a,b;QPointF(double x=0,double y=0):a(x),b(y){}double x()const{return a;}double y()const{return b;}};
struct QPoint{int a,b;QPoint(int x,int y):a(x),b(y){}};
struct QRect{int x,y,w,h;};
struct QImage{int id,flip=0;QImage mirrored(bool h,bool v)const{return {id,flip^(h?1:0)^(v?2:0)};}};
struct Call{int x,y,sx,sy,w,h,id,flip;};
struct QPainter{std::vector<Call> calls;
 void drawImage(QPoint p,QImage im,QRect r){calls.push_back({p.a,p.b,r.x,r.y,r.w,r.h,im.id,im.flip});}
 void drawImage(int x,int y,QImage im){drawImage(QPoint(x,y),im,{0,0,32,32});}};
struct Name{std::string key;std::string getJsonKey()const{return key;}};
struct Tile{int terView=0,roadType=0,roadDir=0,riverType=0,riverDir=0,extTileFlags=0;
 const Name*getTerrain()const{static Name n{"t"};return &n;}
 const Name*getRoad()const{static Name n{"r"};return &n;}
 const Name*getRiver()const{static Name n{"v"};return &n;}
 bool hasRiver()const{return riverType!=0;}};
struct Map{int y=1;Tile current,prior;const Tile&getTile(int3 p)const{return p.y==y?current:prior;}
 bool isInTheMap(int3 p)const{return p.x>=0&&p.x<3&&p.y>=0&&p.y<3;}};
struct MapHandler{Map*map;std::map<std::string,std::vector<std::shared_ptr<QImage>>>terrainImages,roadImages,riverImages;
 void drawTerrainTile(QPainter&,int,int,int,QPointF);
 void drawRoad(QPainter&,int,int,int,QPointF);
 void drawRiver(QPainter&,int,int,int,QPointF);};
struct PatternMap{bool isInTheMap(int3 p)const{return p.x>=0&&p.x<3&&p.y>=0&&p.y<3;}};
template<class T>struct CDrawLinesOperation{
 struct LinePattern{std::string data[9];std::pair<int,int>roadMapping,riverMapping;bool hasHFlip,hasVFlip;};
 struct ValidationResult{bool result;int flip;ValidationResult(bool r):result(r),flip(0){}};
 static const std::vector<LinePattern>patterns;
 static const int FLIP_PATTERN_HORIZONTAL=1,FLIP_PATTERN_VERTICAL=2,FLIP_PATTERN_BOTH=3;
 PatternMap*map;int mask=0,kind=0;
 bool canApplyPattern(const LinePattern&p)const{return (kind?p.riverMapping:p.roadMapping).first>=0;}
 bool tileHasSomething(int3 p)const{int i=p.y*3+p.x;int bit=i<4?i:i-1;return i==4||((mask>>bit)&1);}
 void flipPattern(LinePattern&,int)const;
 ValidationResult validateTile(const LinePattern&,const int3&);
};
'''

SUFFIX=r'''
int main(){
 Map map;MapHandler h;h.map=&map;
 for(int i=0;i<17;i++){h.roadImages["r"].push_back(std::make_shared<QImage>(QImage{i}));}
 for(int i=0;i<13;i++){h.riverImages["v"].push_back(std::make_shared<QImage>(QImage{200+i}));}
 h.terrainImages["t"].push_back(std::make_shared<QImage>(QImage{100}));
 PatternMap pm;CDrawLinesOperation<int>op;op.map=&pm;
 int mode,y,cur,prior,flags,pflags,ox,oy;
 while(std::cin>>mode>>y>>cur>>prior>>flags>>pflags>>ox>>oy){
  if(mode==3){op.mask=cur;op.kind=y;int index=-1,flip=0,lo=-1,hi=-1;
   for(int i=0;i<(int)op.patterns.size();i++){auto r=op.validateTile(op.patterns[i],int3(1,1,0));
    if(r.result){index=i;flip=r.flip;auto range=op.kind?op.patterns[i].riverMapping:op.patterns[i].roadMapping;lo=range.first;hi=range.second;break;}}
   std::cout<<index<<' '<<flip<<' '<<lo<<' '<<hi<<'\n';continue;}
  map.y=y;map.current={0,cur>=0,cur,cur>=0,cur,flags};map.prior={0,prior>=0,prior,prior>=0,prior,pflags};
  QPainter p;QPointF off(ox,oy);
  if(mode==0)h.drawRoad(p,1,y,0,off);if(mode==1)h.drawRiver(p,1,y,0,off);if(mode==2)h.drawTerrainTile(p,1,y,0,off);
  std::cout<<p.calls.size();for(auto c:p.calls)std::cout<<' '<<c.x<<' '<<c.y<<' '<<c.sx<<' '<<c.sy<<' '<<c.w<<' '<<c.h<<' '<<c.id<<' '<<c.flip;
  std::cout<<'\n';
 }
}
'''

def run(spec,outdir,cxx):
    readers={name:{'path':str(p.resolve()),'sha256':assembly.digest(p.read_bytes())}
        for name,p in (('oracle',Path(__file__)),('assembly',Path(assembly.__file__)))}
    model,units=freeze_sources(spec);outdir.mkdir(parents=True,exist_ok=False)
    code=PREFIX+'\n'+'\n\n'.join(units.values())+'\n'+SUFFIX
    src=outdir/'source-unit-oracle.cpp';src.write_text(code,encoding='utf-8');exe=outdir/'source-unit-oracle.exe'
    compiler=Path(cxx).resolve();common.need(compiler.is_file(),'SOURCE_ORACLE_COMPILER_MISSING')
    compiled=subprocess.run([str(compiler),'-std=c++17','-O2','-static-libgcc','-static-libstdc++',str(src),'-o',str(exe)],capture_output=True,text=True)
    (outdir/'compile.json').write_text(json.dumps({'returncode':compiled.returncode,'stdout':compiled.stdout,'stderr':compiled.stderr},indent=2),encoding='utf-8')
    common.need(compiled.returncode==0,'SOURCE_ORACLE_COMPILE_FAILED: '+compiled.stderr[-4000:])
    cases=[];expected=[]
    for kind in range(2):
        for mask in range(256):
            cases.append([3,kind,mask,0,0,0,0,0]);expected.append(select_pattern(mask,model['patterns'],'river' if kind else 'road'))
    for y,cur,prior,flags,pflags in itertools.product((0,1),range(-1,17),range(-1,17),range(4),range(4)):
        bits=flags<<4;pbits=pflags<<4;offset=(13,7)
        cases.append([0,y,cur,prior,bits,pbits,*offset])
        tiles=np.zeros((1,3,3,7),np.uint8)
        if cur>=0:tiles[0,y,1,4:7]=[3,cur,bits]
        if y>0 and prior>=0:tiles[0,y-1,1,4:7]=[3,prior,pbits]
        draws=[d for d in assembly.tile_draw_commands(tiles,1,y,0,1,offset) if d['category']=='road']
        row=[len(draws)]
        for d in draws:row+=d['destination']+d['source_rect']+[d['key'][3],d['flags']]
        expected.append(row)
    for mode,flags,frame in itertools.product((1,2),range(256),range(-1,13)):
        if mode==2 and frame!=0:continue
        cases.append([mode,1,frame,0,flags,0,13,7]);tiles=np.zeros((1,3,3,7),np.uint8);tiles[0,1,1,6]=flags
        if mode==1 and frame>=0:tiles[0,1,1,2:4]=[1,frame]
        draws=[d for d in assembly.tile_draw_commands(tiles,1,1,0,1,(13,7)) if d['category']==('river' if mode==1 else 'terrain')]
        row=[len(draws)]
        for d in draws:row+=d['destination']+d['source_rect']+[(200+d['key'][3]) if mode==1 else 100,d['flags']]
        expected.append(row)
    stdin=''.join(' '.join(map(str,c))+'\n' for c in cases)
    result=subprocess.run([str(exe)],input=stdin,text=True,capture_output=True,check=True)
    observed=[list(map(int,line.split())) for line in result.stdout.splitlines()]
    common.need(len(observed)==len(expected),'SOURCE_ORACLE_RESULT_COUNT_DIFFERS')
    failures=[{'case':c,'expected':e,'observed':o} for c,e,o in zip(cases,expected,observed) if e!=o]
    report={'schema':'h3.map-source-oracle.report.v1','status':'PASS' if not failures else 'FAIL',
        'cases':len(cases),'line_selector_cases':512,'draw_geometry_cases':len(cases)-512,
        'failures':failures[:50],'model':model,'compiler':{'path':str(compiler),'sha256':assembly.digest(compiler.read_bytes())},
        'generated_cpp_sha256':assembly.digest(src.read_bytes()),'executable_sha256':assembly.digest(exe.read_bytes()),
        'readers':readers,'case_stream_sha256':assembly.digest(stdin.encode()),
        'observed_trace_sha256':assembly.digest(result.stdout.encode()),
        'qt_rasterization_executed':False,'complete_editor_executed':False,'desktop_control_used':False,
        'art_accepted':False,'game_runtime_accepted':False,'images_written':0}
    if spec.get('dispatcher_renderer'):report['dispatcher_renderer']=spec['dispatcher_renderer']
    if spec.get('native_reference'):report['native_reference_graph']=crosscheck_native_graph(spec['native_reference'],model)
    for ref in readers.values():common.pin(ref)
    (outdir/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    return report

def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--spec',required=True);p.add_argument('--spec-sha256',required=True)
    p.add_argument('--outdir',required=True);p.add_argument('--cxx',required=True)
    p.add_argument('--operation-id')
    p.add_argument('--renderer');p.add_argument('--renderer-sha256')
    a=p.parse_args(argv);spec=common.doc({'path':a.spec,'sha256':a.spec_sha256})
    common.need(bool(a.renderer)==bool(a.renderer_sha256),'SOURCE_ORACLE_DISPATCHER_PIN_REQUIRED')
    if a.renderer:
        ref={'path':a.renderer,'sha256':a.renderer_sha256};common.pin(ref);spec={**spec,'dispatcher_renderer':ref}
    out=Path(a.outdir).resolve();root=resolve_tool_root(__file__)/'build'/'experiments'
    common.need(out.is_relative_to(root.resolve()),'SOURCE_ORACLE_OUTPUT_MUST_BE_TOOL_EXPERIMENT')
    common.need(not a.operation_id or out.name==a.operation_id,'SOURCE_ORACLE_OPERATION_ID_DIFFERS')
    r=run(spec,out,a.cxx);print(json.dumps({k:r[k] for k in ('status','cases','failures')},ensure_ascii=False))
    return 0 if r['status']=='PASS' else 1

if __name__=='__main__':sys.exit(main())
