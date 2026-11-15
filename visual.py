"""Local UI-anchor matching. No full account screenshots or network requests."""
from pathlib import Path
import json
from PIL import Image, ImageChops, ImageFilter

ASSETS=Path(__file__).resolve().parent/'assets'
_templates=None
_SAT=[255 if n<105 else 0 for n in range(256)]
_VALUE=[255 if n>165 else 0 for n in range(256)]

def _white(image,min_value=165,bright=False):
    hsv=image.resize((96,32),Image.Resampling.BILINEAR).convert('HSV')
    _,s,v=hsv.split()
    value=_VALUE if min_value==165 else [255 if n>min_value else 0 for n in range(256)]
    return v.point(value) if bright else ImageChops.multiply(s.point(_SAT),v.point(value))

def _count(mask): return mask.histogram()[255]

def _similarity(mask,template,expanded_template,template_count):
    count=_count(mask)
    if count<12 or count>96*32*.78: return 0.0
    precision=_count(ImageChops.multiply(mask,expanded_template))/count
    expanded=mask.filter(ImageFilter.MaxFilter(3))
    recall=_count(ImageChops.multiply(template,expanded))/template_count
    return 2*precision*recall/(precision+recall) if precision+recall else 0.0

def _load():
    global _templates
    if _templates is None:
        manifest=json.loads((ASSETS/'anchors.json').read_text(encoding='utf-8'))
        _templates=[]
        for entry in manifest:
            with Image.open(ASSETS/entry['file']) as image: mask=_white(image.convert('RGB'),entry.get('min_value',165),entry.get('bright',False))
            _templates.append((entry,mask,mask.filter(ImageFilter.MaxFilter(3)),_count(mask)))
    return _templates

def _match(image,item):
    entry,mask,expanded,count=item
    if not count: return 0.0
    x0,y0,x1,y1=entry['box']
    cx=(x0+x1)/2;cy=(y0+y1)/2;w=x1-x0;h=y1-y0
    best=0.0;best_box=None
    for scale in (1.0,.92,1.08,.84,1.16,1.24):
        for dy in (0,-8,8,-16,16,-24,24):
            for dx in (0,-4,4,-8,8,-12,12):
                box=(round(cx+dx-w*scale/2),round(cy+dy-h*scale/2),round(cx+dx+w*scale/2),round(cy+dy+h*scale/2))
                if box[0]<0 or box[1]<0 or box[2]>336 or box[3]>598: continue
                score=_similarity(_white(image.crop(box),entry.get('min_value',165),entry.get('bright',False)),mask,expanded,count)
                if score>best: best=score;best_box=box
                if best>.992: return best
    if best_box is not None:
        for dy in (-2,-1,0,1,2):
            for dx in (-2,-1,0,1,2):
                box=(best_box[0]+dx,best_box[1]+dy,best_box[2]+dx,best_box[3]+dy)
                if box[0]<0 or box[1]<0 or box[2]>336 or box[3]>598: continue
                score=_similarity(_white(image.crop(box),entry.get('min_value',165),entry.get('bright',False)),mask,expanded,count)
                best=max(best,score)
    return best

def _fraction(image,box,predicate):
    roi=image.crop(box).resize((32,32)).convert('HSV')
    raw=roi.tobytes()
    return sum(predicate(raw[i],raw[i+1],raw[i+2]) for i in range(0,len(raw),3))/(32*32)

def detect(image):
    if image is None: return 'unknown',0.0,{}
    image=image.convert('RGB').resize((336,598),Image.Resampling.BILINEAR)
    scores={entry['name']:_match(image,item) for item in _load() for entry in [item[0]]}
    green=_fraction(image,(128,460,209,532),lambda h,s,v:45<=h<=140 and s>95 and v>110)
    left_green=_fraction(image,(22,420,143,540),lambda h,s,v:45<=h<=120 and s>95 and v>55)
    right_blue=_fraction(image,(190,420,310,540),lambda h,s,v:125<=h<=180 and s>95 and v>55)
    orange=_fraction(image,(155,480,260,540),lambda h,s,v:8<=h<=40 and s>110 and v>120)
    # Several anchors must agree for every active screen. Reward panels use a
    # fixed continue/claim control; they never cause a fight/retreat decision.
    candidates={}
    result=min(scores.get('result_claim',0),max(scores.get('result_title',0),scores.get('result_total',0)))
    if result>.85: return 'result',result,scores
    enemy=min(scores.get('retreat',0),scores.get('fight',0))
    if left_green>.20 and right_blue>.20 and enemy>.76: candidates['enemy']=enemy
    if left_green>.40 and right_blue>.40 and max(scores.get('retreat',0),scores.get('fight',0))>.90 and enemy>.65:
        candidates['enemy']=max(candidates.get('enemy',0),enemy)
    if green>.36 and scores.get('forward',0)>.75:
        candidates['forward']=scores['forward']
    home=min(scores.get('home_title',0),scores.get('home_button',0))
    if home>.84 and orange>.30: candidates['home']=home
    if scores.get('home_title',0)>.90 and orange>.30:
        candidates['home']=max(candidates.get('home',0),scores['home_title'])
    if scores.get('continue',0)>.87: candidates['treasure']=scores['continue']
    legend=min(scores.get('legend_left',0),scores.get('legend_right',0))
    if legend>.83: candidates['treasure']=max(legend,candidates.get('treasure',0))
    if scores.get('claim',0)>.89: candidates['buff']=scores['claim']
    if not candidates: return 'unknown',max(scores.values(),default=0),scores
    ranked=sorted(candidates.items(),key=lambda item:item[1],reverse=True)
    if len(ranked)>1 and ranked[0][1]-ranked[1][1]<.035:
        scores['_ambiguous']=True
        return 'unknown',ranked[0][1],scores
    return ranked[0][0],ranked[0][1],scores
