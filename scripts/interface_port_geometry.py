"""Source-only interface planes for the native correspondence-mesh renderer.

No raster is written or changed here. A true opaque two-dimensional source
cross-section is moved to the native opening by the existing formal sampler.
Disconnected banks do not count as a filled opening. Native RGB is never read.
"""
import numpy as np

SIDES=('top','right','bottom','left')


def initial_roi(alpha):
    ys,xs=np.nonzero(alpha>=128)
    if not len(xs):raise ValueError('OPAQUE_SOURCE_SUBJECT_MISSING')
    x=max(0,int(xs.min())-2);y=max(0,int(ys.min())-2)
    right=min(alpha.shape[1],int(xs.max())+3);bottom=min(alpha.shape[0],int(ys.max())+3)
    return [x,y,right-x,bottom-y]


def reorient_rgba(rgba,quarter_turns):
    if type(quarter_turns) is not int or not 0<=quarter_turns<=3:raise ValueError('LOSSLESS_QUARTER_TURNS_REQUIRED')
    if rgba.ndim!=3 or rgba.shape[2]!=4 or rgba.dtype!=np.uint8:raise ValueError('ORIGINAL_RGBA_REQUIRED')
    return np.rot90(rgba,quarter_turns).copy()


def validate_formal_mesh(roi,body,vertices,targets,triangles):
    source=np.array(vertices)/[roi[2]/(body[2]-body[0]),roi[3]/(body[3]-body[1])]
    target=np.array(targets);worst=0.
    for tri in triangles:
        a,b,c=source[tri];e,f=b-a,c-a;sa=e[0]*f[1]-e[1]*f[0]
        a,b,c=target[tri];e,f=b-a,c-a;ta=e[0]*f[1]-e[1]*f[0]
        if min(sa,ta)<=1e-7 or not .2<=ta/sa<=5:raise ValueError('FORMAL_TRIANGLE_AREA_LIMIT')
        s=source[tri];t=target[tri]
        ratios=np.linalg.norm(t-np.roll(t,1,axis=0),axis=1)/np.linalg.norm(s-np.roll(s,1,axis=0),axis=1)
        if ratios.min()<.25-1e-9 or ratios.max()>2.5+1e-9:raise ValueError('FORMAL_MESH_EDGE_LIMIT')
        worst=max(worst,float(max(abs(np.log(ratios)))))
    return worst


def runs(mask):
    edges=np.diff(np.r_[False,np.asarray(mask,dtype=bool),False].astype(np.int8))
    return list(zip(np.flatnonzero(edges==1),np.flatnonzero(edges==-1)))


def planes(alpha,roi,native_alpha,body,ports,max_fraction=.24):
    if not 0<max_fraction<=.25:raise ValueError('PORT_DEPTH_BUDGET')
    x,y,w,h=roi;source=np.asarray(alpha)[y:y+h,x:x+w]
    if source.shape!=(h,w) or not np.issubdtype(source.dtype,np.integer):raise ValueError('SOURCE_ALPHA_GEOMETRY')
    result={}
    for side in SIDES:
        if side not in ports:continue
        view={'top':source,'bottom':source[::-1],'left':source.T,'right':source.T[::-1]}[side]
        native={'top':native_alpha[0,:],'bottom':native_alpha[-1,:],
                'left':native_alpha[:,0],'right':native_alpha[:,-1]}[side]
        indexes=np.flatnonzero(native>=250)
        if len(indexes)<3:raise ValueError('NATIVE_FUNCTIONAL_PORT_MISSING:'+side)
        # A robust band covers at least one final 3X reconstruction footprint.
        band=max(2,int(np.ceil(view.shape[0]/max(native_alpha.shape)*.35)))
        span=(body[2]-body[0]) if side in ('top','bottom') else (body[3]-body[1])
        expected=view.shape[1]*(indexes[-1]-indexes[0]+1)/span
        minimum=max(8,expected*.60)
        candidates=[]
        for depth in range(band,min(int(view.shape[0]*max_fraction),view.shape[0]-band-1)+1):
            profile=np.min(view[depth-band:depth+band+1],axis=0)>=250
            intervals=[(int(a),int(b)) for a,b in runs(profile) if b-a>=minimum]
            if not intervals:continue
            a,b=max(intervals,key=lambda v:v[1]-v[0])
            score=abs(np.log((b-a)/expected))+.08*depth/(view.shape[0]*max_fraction)
            candidates.append((score,depth,a,b))
            break
        if not candidates:raise ValueError('NO_OPAQUE_PORT_CROSS_SECTION:'+side)
        _,depth,a,b=min(candidates)
        result[side]={'source_depth':depth,'source_interval':[a,b],'opaque_band_radius':band,
                      'native_interval':[int(indexes[0]),int(indexes[-1]+1)],
                      'source_min_alpha':int(view[depth-band:depth+band+1,a:b].min())}
    return result


def mesh(alpha,roi,native_alpha,body,ports,max_fraction=.24):
    selected=planes(alpha,roi,native_alpha,body,ports,max_fraction)
    w,h=roi[2:];x,y,x1,y1=body
    corners=[[0.,0.],[float(w),0.],[float(w),float(h)],[0.,float(h)]]
    target_corners=[[x,y],[x1,y],[x1,y1],[x,y1]]
    edge_points={}
    for side in SIDES:
        length=w if side in ('top','bottom') else h
        base=x if side in ('top','bottom') else y
        span=(x1-x) if side in ('top','bottom') else (y1-y)
        details=selected.get(side)
        if details:
            a,b=details['source_interval'];u,v=details['native_interval'];u-=base;v-=base
            # Keep a real two-dimensional margin for small native corner wedges.
            # These coordinates stay inside the proven opaque source interval.
            a=max(a,u*length/span*.65)
            b=min(b,length-(span-v)*length/span*.65)
            if b-a<8:raise ValueError('OPAQUE_PORT_MARGIN_CAPACITY:'+side)
            depth=details['source_depth']
            nodes=[]
            for coordinate,mapped in ((a,u),(b,v)):
                if side=='top':s,t=[coordinate,depth],[base+mapped,y];low,high=0,1
                elif side=='right':s,t=[w-depth,coordinate],[x1,base+mapped];low,high=1,2
                elif side=='bottom':s,t=[coordinate,h-depth],[base+mapped,y1];low,high=3,2
                else:s,t=[depth,coordinate],[x,base+mapped];low,high=0,3
                if mapped==0:corners[low]=list(map(float,s))
                elif mapped==span:corners[high]=list(map(float,s))
                else:nodes.append((list(map(float,s)),list(map(float,t))))
            edge_points[side]=nodes if side in ('top','right') else nodes[::-1]
        else:edge_points[side]=[]
    vertices=[];targets=[]
    for i,side in enumerate(SIDES):
        vertices.append(corners[i]);targets.append(list(map(float,target_corners[i])))
        for s,t in edge_points[side]:vertices.append(s);targets.append(t)
    count=len(vertices)
    triangles=[[i,(i+1)%count,count] for i in range(count)]
    source_scale=np.array([w/(x1-x),h/(y1-y)])
    best=None
    fractions=(.5,.4,.6,.3,.7,.2,.8)
    for fx in fractions:
        for fy in fractions:
            source=np.array([*vertices,[w*fx,h*fy]])/source_scale
            target=np.array([*targets,[x+(x1-x)*fx,y+(y1-y)*fy]])
            scores=[]
            for tri in triangles:
                a,b,c=source[tri];e,f=b-a,c-a;sa=e[0]*f[1]-e[1]*f[0]
                a,b,c=target[tri];e,f=b-a,c-a;ta=e[0]*f[1]-e[1]*f[0]
                if min(sa,ta)<=1e-7 or not .2<=ta/sa<=5:break
                s=source[tri];t=target[tri]
                ratios=np.linalg.norm(t-np.roll(t,1,axis=0),axis=1)/np.linalg.norm(s-np.roll(s,1,axis=0),axis=1)
                if ratios.min()<.25-1e-9 or ratios.max()>2.5+1e-9:break
                scores.extend(abs(np.log(ratios)))
            else:
                score=max(scores)+.02*((fx-.5)**2+(fy-.5)**2)
                if best is None or score<best[0]:best=(score,fx,fy)
    if best is None:raise ValueError('NO_NONFOLDING_MESH_WITHIN_FORMAL_STRETCH_LIMITS')
    _,fx,fy=best
    vertices.append([w*fx,h*fy]);targets.append([x+(x1-x)*fx,y+(y1-y)*fy])
    return vertices,targets,triangles,selected


def rectangular_registration(alpha,roi,native_alpha,body,ports,max_fraction=.24):
    """Declare real lossless rectangular cut planes before the formal sampler.

    The entire retained ROI is triangulated. No alpha-positive pixel inside it
    can be discarded by the mesh hull; source bytes and alpha remain unchanged.
    """
    original=list(roi)
    for iteration in range(4):
        selected=planes(alpha,roi,native_alpha,body,ports,max_fraction)
        cuts={side:row['source_depth'] for side,row in selected.items()}
        x,y,w,h=roi;l=cuts.get('left',0);t=cuts.get('top',0);r=cuts.get('right',0);b=cuts.get('bottom',0)
        roi=[x+l,y+t,w-l-r,h-t-b]
        # Native openings coincident with a crop corner must also be opaque at
        # that source corner. Discard only a declared rectangular source fringe.
        x,y,w,h=roi;view=alpha[y:y+h,x:x+w];adjust=[0,0,0,0]
        for side in ports:
            values={'top':view[0,:],'bottom':view[-1,:],'left':view[:,0],'right':view[:,-1]}[side]
            options=runs(values>=250)
            if not options:raise ValueError('RETAINED_PORT_IS_NOT_OPAQUE:'+side)
            lo,hi=max(options,key=lambda v:v[1]-v[0]);length=len(values)
            a,z=selected[side]['native_interval'];base=body[0] if side in ('top','bottom') else body[1]
            span=body[2]-body[0] if side in ('top','bottom') else body[3]-body[1]
            first,last=(0,2) if side in ('top','bottom') else (1,3)
            if a==base:adjust[first]=max(adjust[first],int(lo))
            if z==base+span:adjust[last]=max(adjust[last],int(length-hi))
        if not any(adjust):break
        l,t,r,b=adjust;roi=[x+l,y+t,w-l-r,h-t-b]
    x,y,w,h=roi;view=alpha[y:y+h,x:x+w]
    if min(w,h)<16:raise ValueError('NO_RETAINED_HIGH_RESOLUTION_ROI')
    if min(w/original[2],h/original[3])<.45:raise ValueError('SOURCE_RECTANGULAR_CUT_TOO_LARGE')
    vertices=[];targets=[];x0,y0,x1,y1=body;details={}
    for side in SIDES:
        length=w if side in ('top','bottom') else h
        base=x0 if side in ('top','bottom') else y0
        span=x1-x0 if side in ('top','bottom') else y1-y0
        pairs=[(0.,float(base)),(float(length),float(base+span))]
        if side in ports:
            a,z=selected[side]['native_interval'];a-=base;z-=base
            values={'top':view[0,:],'bottom':view[-1,:],'left':view[:,0],'right':view[:,-1]}[side]
            lo,hi=map(int,max(runs(values>=250),key=lambda v:v[1]-v[0]))
            if a==0 and lo>0 or z==span and hi<length:raise ValueError('SOURCE_CORNER_NOT_OPAQUE:'+side)
            inset=length/span*.10
            if a>0:pairs.append((max(float(lo)+inset,a*length/span*.4),float(base+a)))
            if z<span:pairs.append((min(float(hi)-inset,length-(span-z)*length/span*.4),float(base+z)))
            pairs.sort()
            details[side]={'source_interval':[lo,hi],'native_interval':[a+base,z+base],
                           'retained_opaque_center':int(values[(lo+hi)//2])}
        order=pairs[:-1] if side in ('top','right') else pairs[:0:-1]
        for a,b in order:
            if side=='top':s,t=[a,0.],[b,y0]
            elif side=='right':s,t=[float(w),a],[x1,b]
            elif side=='bottom':s,t=[a,float(h)],[b,y1]
            else:s,t=[0.,a],[x0,b]
            vertices.append(s);targets.append(list(map(float,t)))
    count=len(vertices);vertices.append([w/2,h/2]);targets.append([(x0+x1)/2,(y0+y1)/2])
    triangles=[[i,(i+1)%count,count] for i in range(count)]
    return roi,vertices,targets,triangles,{'ports':details,'original_roi':original,'retained_roi':roi,'source_alpha_modified':False}
