"""Feature proposals preserve sources, reject unsupported comparisons, and never approve art."""
from pathlib import Path
import hashlib, json, random, sys, uuid
import numpy as np
from PIL import Image, ImageDraw
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import pose_feature_registration as feature

def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def pin(p):return {'path':str(p.resolve()),'sha256':sha(p),'pixels':list(Image.open(p).size)}
def write(p,image):
    with p.open('xb') as f:image.save(f,format='PNG')
def fail(fn, code):
    try:fn()
    except ValueError as e:assert code in str(e);return
    raise AssertionError('accepted invalid data: '+code)
def main():
    root=ROOT/'build/experiments/pose-feature-contract'/uuid.uuid4().hex;root.mkdir(parents=True,exist_ok=False)
    rng=random.Random(1729);im=Image.new('RGBA',(800,900),(0,0,0,0));d=ImageDraw.Draw(im)
    d.rectangle((80,70,720,830), fill=(53,82,122,255))
    for i in range(210):
        x=rng.randint(105,685);y=rng.randint(95,795);r=rng.randint(3,18);c=tuple(rng.randrange(40,255) for _ in range(3))+(255,)
        d.ellipse((x-r,y-r,x+r,y+r), fill=c);d.line((x-2*r,y,x+2*r,y+r),fill=(255,240,80,255),width=2)
    p=root/'reference.png';q=root/'target.png';bad=root/'featureless.png'
    write(p,im)
    transformed=im.transform(im.size,Image.Transform.AFFINE,(1/.8,0,-40/.8,0,1/.8,-70/.8),resample=Image.Resampling.BICUBIC)
    write(q,transformed)
    plain=Image.new('RGBA',(800,900),(0,0,0,0));ImageDraw.Draw(plain).rectangle((80,70,720,830), fill=(80,100,120,255));write(bad,plain)
    a,b,c=pin(p),pin(q),pin(bad);before={str(x):sha(x) for x in (p,q,bad)}
    provider_available = feature.provider()['status']=='AVAILABLE'
    if provider_available:
        result=feature.estimate(a,b,[60,50,740,850],[90,100,640,760])
        assert result['status']=='REVIEW_REQUIRED' and result['candidate_only']
        assert result['native_registration']=='NOT_MEASURED' and result['body_scale']=='NOT_MEASURED'
        assert not result['same_identity_proven'] and not result['pixels_changed']
        assert abs(result['uniform_scale_target_to_reference']-1.25)<.03,result
        assert np.linalg.norm(np.array(result['translation_reference_source_pixels'])-[-50,-87.5])<8,result
        assert result['inliers']>=12,result
        unrelated=feature.estimate(a,c,[60,50,740,850],[60,50,740,850])
        assert unrelated['status']=='NOT_COMPARABLE',unrelated
        fail(lambda:feature.estimate({**a,'sha256':'0'*64},b,[60,50,740,850],[90,100,640,760]),'FEATURE_SOURCE_SHA_DRIFT')
        fail(lambda:feature.estimate(a,b,[60,50,900,850],[90,100,640,760]),'FEATURE_REGION_OUTSIDE_IMAGE')
    else:
        result=feature.estimate(a,b,[60,50,740,850],[90,100,640,760])
        assert result['status']=='UNAVAILABLE' and result['reason']=='OPENCV_NOT_INSTALLED'
    old=sys.modules.get('cv2');sys.modules['cv2']=None
    try:assert feature.provider()=={'status':'UNAVAILABLE','reason':'OPENCV_NOT_INSTALLED'}
    finally:
        if old is None:sys.modules.pop('cv2',None)
        else:sys.modules['cv2']=old
    assert before=={str(x):sha(x) for x in (p,q,bad)}
    print(json.dumps({'passed':True,'fixture':str(root),'provider':feature.provider(),
        'opencv_dependent_checks':'PASSED' if provider_available else 'SKIPPED_PROVIDER_UNAVAILABLE',
        'checks':['missing_optional_provider','source_unchanged','no_native_or_art_pass'],
        'opencv_checks':['known_uniform_transform','explicit_regions','featureless_rejected','source_sha_pin']}))
if __name__=='__main__':main()
