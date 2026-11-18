"""Shaman hunt: fast local vision and a separately testable decision policy."""
from dataclasses import dataclass,field
from pathlib import Path
import math
import re
import time
from PIL import ImageOps

import engine

POINTS={'hunt_start':[.55,.95],'hunt_lock':[.93,.84],
        'hunt_rage':[.93,.73],'hunt_exit':[.50,.965]}
REGIONS={'score':[.38,.045,.67,.095],'stock':[.94,.845,.999,.875],
         'lobby_multiplier':[.57,.575,.80,.62],'arena_multiplier':[.49,.005,.71,.055]}
ASSETS=Path(__file__).resolve().parent/'assets'/'hunt'


@dataclass
class Observation:
    scene:str='unknown'
    multiplier:int|None=None
    stock:int|None=None
    bosses:tuple=()
    marker:tuple|None=None
    score:object=None
    evidence:str=''


class Policy:
    def __init__(self,rage=False,max_attack=15,auto_next=True,pause_low_stock=True):
        self.phase='entry';self.rage=rage;self.max_attack=max_attack;self.auto_next=auto_next;self.pause_low_stock=pause_low_stock
        self.stock=None;self.origin=None;self.started=0.;self.absent=None
        self.baseline=None;self.changed=0;self.previous_score=None
        self.last_marker=None;self.last_marker_time=0.;self.suspicious=0
        self.seen_marker=False;self.rage_pending=False;self.exit_at=0.;self.exit_tries=0
        self.pause_after=False;self.reason='';self.boss_candidate=None;self.boss_hits=0;self.candidate_score=None

    def exit(self,now,reason,pause=False):
        self.phase='exiting';self.exit_at=now;self.exit_tries=1
        self.pause_after=pause;self.reason=reason
        return 'exit',reason

    def step(self,o,now):
        if self.phase=='exiting':
            if o.scene=='lobby':
                if self.pause_after:return 'pause',self.reason
                if not self.auto_next:return 'pause','本轮已结束，未开启自动进行下一轮。'
                self.__init__(self.rage,self.max_attack,self.auto_next,self.pause_low_stock)
                return 'wait','已退出，重新确认200倍场后进入。'
            if now-self.exit_at>5:
                if self.exit_tries>=2:return 'pause','退出后未能确认主页，请人工检查。'
                self.exit_tries+=1;self.exit_at=now
                return 'exit','再次退出，等待主页确认。'
            return 'wait','正在确认离场。'
        if o.scene=='lobby':
            if self.phase=='locked':return 'pause','攻击过程中意外回到主页，请人工检查。'
            if self.phase=='entering':
                return ('pause','开始围猎后5秒仍未入场，请人工检查。') if now-self.started>=5 else ('wait','已点击开始，等待入场。')
            if o.multiplier!=200:return 'pause','只支持200倍场，请手动选择200倍场后启动。'
            self.phase='entering';self.started=now
            return 'start','已确认200倍场，开始围猎。'
        if o.scene!='arena':return 'wait','等待确认围猎主页或场内画面，不盲目点击。'
        if self.phase in ('entry','entering'):
            if o.multiplier!=200:return self.exit(now,'未能确认场内为200倍场，退出并暂停。',True)
            if o.stock is None and self.pause_low_stock:return self.exit(now,'锁定数量未读清，退出并暂停，请核对数量区域。',True)
            self.stock=o.stock;self.phase='waiting'
        if self.phase=='waiting':
            if o.stock is not None:self.stock=o.stock
            if self.pause_low_stock and self.stock is not None and self.stock<3:return self.exit(now,'锁定道具少于3个，请去1倍场打道具后重新启动。',True)
            if len(o.bosses)!=1:
                self.boss_hits=0;return 'wait','等待单个萨满头像；不攻击。'
            quadrant=o.bosses[0]
            if self.boss_candidate==quadrant and self.candidate_score==o.score:self.boss_hits+=1
            else:self.boss_candidate=quadrant;self.boss_hits=1
            self.candidate_score=o.score
            if self.boss_hits<2:return 'wait',f'已检测到萨满头像（象限{quadrant+1}），等待下一帧确认头像与个人分数。'
            if o.score is None:return 'wait','个人分数区域未确认，不使用锁定。'
            self.origin=quadrant;self.baseline=o.score;self.started=now;self.phase='locked'
            self.stock=max(0,self.stock-1) if self.stock is not None else None;self.rage_pending=self.rage
            return 'lock','萨满头像已确认，使用一次锁定。'
        if self.phase=='locked':
            # A post-use stock of two does NOT interrupt this paid target.
            if o.stock is not None:self.stock=min(self.stock,o.stock) if self.stock is not None else o.stock
            low=self.pause_low_stock and self.stock is not None and self.stock<3
            if o.score is not None and o.score!=self.baseline:
                if o.score==self.previous_score:self.changed+=1
                else:self.changed=1
                self.previous_score=o.score
            else:self.changed=0;self.previous_score=None
            if self.changed>=2:
                return self.exit(now,'个人分数字形连续确认变化，退出。'+('锁定不足3个，请去1倍场打道具。' if low else ''),low)
            if self.origin not in o.bosses:
                if self.absent is None:self.absent=now
            else:self.absent=None
            in_window=self.absent is not None and now<=self.absent+2
            if o.marker is not None:
                x,y=o.marker;quadrant=(0 if y<.5 else 2)+(0 if x<.5 else 1)
                wrong=in_window and quadrant!=self.origin
                jump=(in_window and self.last_marker is not None and
                      now-self.last_marker_time<.8 and math.dist(o.marker,self.last_marker)>.28)
                self.suspicious=self.suspicious+1 if wrong or jump else 0
                if self.suspicious>=2 or jump:
                    return self.exit(now,'头像消失后的2秒内锁定转移或明显瞬移，退出。'+('锁定不足，请去1倍场补道具。' if low else ''),low)
                if not wrong:self.seen_marker=True
                self.last_marker=o.marker;self.last_marker_time=now
            if self.absent is not None and now>=self.absent+2 and (not self.seen_marker or now-self.last_marker_time>.6):
                return self.exit(now,'头像消失满2秒仍无法确认锁定标志，退出。'+('锁定不足，请去1倍场补道具。' if low else ''),low)
            if now-self.started>=self.max_attack:
                return self.exit(now,'攻击超过设定时限，退出并暂停，请核对识别。',True)
            if self.rage_pending:
                self.rage_pending=False
                return 'rage','按设置使用一次狂暴。'
            return 'wait','攻击本次萨满；道具不足时完成本轮退出后暂停。' if low else '追踪本次萨满。'
        return 'wait','等待入场。'


class AppearanceAlarm:
    """One alert per two-frame-confirmed notice; brief flicker does not rearm it."""
    def __init__(self,required_hits=2):
        self.required_hits=required_hits
        self.candidate=None;self.hits=0;self.latched=None;self.absent=None

    def step(self,o,now):
        if o.scene=='lobby':self.__init__(self.required_hits);return False
        if o.scene!='arena':self.candidate=None;self.hits=0;return False
        if len(o.bosses)!=1:
            self.candidate=None;self.hits=0
            if self.absent is None:self.absent=now
            if now-self.absent>=2:self.latched=None
            return False
        origin=o.bosses[0]
        if self.absent is not None and now-self.absent>=2:self.latched=None
        self.absent=None
        self.hits=self.hits+1 if self.candidate==origin else 1
        self.candidate=origin
        if self.hits>=self.required_hits and self.latched!=origin:
            self.latched=origin;return True
        return False


class ObserveTracker:
    """Passive event tracking: no stock requirements, simulated skill use or inputs."""
    def __init__(self):
        self.active=False;self.origin=None;self.candidate=None;self.hits=0
        self.baseline=None;self.changed=None;self.change_hits=0
        self.absent=None;self.marker=None;self.marker_at=0.;self.wrong_hits=0
        self.blocked=None;self.warned=False;self.next_candidate=None;self.next_hits=0

    def finish(self,reason):
        self.active=False;self.blocked=self.origin;self.hits=0
        return '仅观察：'+reason+'；未发送任何操作。'

    def step(self,o,now):
        if o.scene=='unknown':
            self.absent=None;self.marker=None;self.wrong_hits=0;self.hits=0;self.change_hits=0
            return '仅观察：画面未确认，无法判断萨满生死；继续观察，不点击。'
        if o.scene=='lobby':
            previous=self.active;self.__init__()
            return '仅观察：已离开场地，无法确认是否击杀；请手动入场测试。' if previous else '仅观察：围猎主页，请手动进入场内测试。'
        if self.blocked is not None:
            if self.blocked in o.bosses:return '仅观察：本次目标已结束判断，等待头像消失及下次出现。'
            self.blocked=None
        if not self.active:
            if len(o.bosses)!=1:
                self.hits=0;return '仅观察：等待唯一萨满头像；不使用锁定。'
            origin=o.bosses[0]
            self.hits=self.hits+1 if self.candidate==origin else 1
            self.candidate=origin
            if self.hits<2:return f'仅观察：发现萨满头像候选，象限{origin+1}，等待下一帧确认。'
            self.active=True;self.origin=origin;self.baseline=o.score
            self.changed=None;self.change_hits=0;self.absent=None
            self.marker=None;self.wrong_hits=0;self.warned=False
            return f'仅观察：确认萨满出现，象限{origin+1}；开始跟踪死亡线索，不释放锁定。'
        if len(o.bosses)==1 and o.bosses[0]!=self.origin:
            candidate=o.bosses[0]
            self.next_hits=self.next_hits+1 if self.next_candidate==candidate else 1
            self.next_candidate=candidate
            if self.next_hits>=2:
                previous=self.origin
                self.__init__();self.candidate=candidate;self.hits=1
                message=self.step(o,now)
                return f'仅观察：上一只萨满（象限{previous+1}）生死未确认；'+message.removeprefix('仅观察：')
        else:self.next_hits=0;self.next_candidate=None
        if o.score is not None:
            if self.baseline is None:self.baseline=o.score
            if o.score!=self.baseline:
                self.change_hits=self.change_hits+1 if self.changed==o.score else 1
                self.changed=o.score
            else:self.change_hits=0;self.changed=None
            if self.change_hits>=2:
                return self.finish('疑似萨满死亡：个人分数字形连续两帧确认变化（仍可能是其他怪得分）')
        else:self.change_hits=0;self.changed=None
        if self.origin in o.bosses:
            # A later notice can represent a new spawn after the old notice vanished.
            if self.absent is not None and now-self.absent>=2:
                self.active=False;self.hits=0
                return self.step(o,now)
            self.absent=None
        elif self.absent is None:self.absent=now
        window=self.absent is not None and now-self.absent<=2
        if o.marker is not None:
            x,y=o.marker;quadrant=(0 if y<.5 else 2)+(0 if x<.5 else 1)
            wrong=window and quadrant!=self.origin
            jump=window and self.marker is not None and now-self.marker_at<.8 and math.dist(o.marker,self.marker)>.28
            self.wrong_hits=self.wrong_hits+1 if wrong else 0
            if jump or self.wrong_hits>=2:
                return self.finish('疑似目标死亡或锁定转移：头像消失后2秒内标志跨象限/明显瞬移')
            self.marker=o.marker;self.marker_at=now
        else:self.wrong_hits=0
        if self.absent is not None and now-self.absent>=2:
            if self.marker is None or now-self.marker_at>.6:
                if not self.warned:
                    self.warned=True
                    return '仅观察：头像已消失满2秒，未持续确认锁定标志；无法确认死亡（未开锁定时属正常），继续观察。'
        return f'仅观察：跟踪萨满（象限{self.origin+1}）；头像{"可见" if self.origin in o.bosses else "已消失"}，锁定标志{"可见" if o.marker is not None else "未发现"}，个人分数{"可读取字形" if o.score is not None else "未读清"}。'


class Vision:
    def __init__(self,regions=None):
        try:
            import cv2
            import numpy as np
        except ImportError as error:
            raise RuntimeError('围猎页需要OpenCV和NumPy，请先运行 python -m pip install -r requirements.txt。') from error
        self.cv=cv2;self.np=np;self.regions={**REGIONS,**(regions or {})}
        self.templates={};self.portrait_scales=[];self.marker_scales={}
        for name in ('shaman','target','start','lock','exit','multiplier_200'):
            data=np.fromfile(str(ASSETS/(name+'.png')),dtype=np.uint8)
            self.templates[name]=cv2.imdecode(data,cv2.IMREAD_COLOR)
            if self.templates[name] is None:raise RuntimeError('围猎识别素材缺失：'+name)
        self.templates['lock_core']=self.templates['lock'][6:36,7:53]

    def detect_bosses(self,array):
        # Search all four directions together at half resolution, including mirrored
        # portraits. Only promising locations receive a full-resolution color check.
        cv=self.cv;np=self.np
        if not self.portrait_scales:
            face=self.templates['shaman'][4:-4,4:-4]
            for source in (face,cv.flip(face,1)):
                for width in range(12,48):
                    height=round(source.shape[0]*width/source.shape[1])
                    small=cv.resize(source,(width,height),interpolation=cv.INTER_AREA)
                    full=[cv.resize(source,(w,round(source.shape[0]*w/source.shape[1])),interpolation=cv.INTER_AREA)
                          for w in (width*2-1,width*2,width*2+1)]
                    self.portrait_scales.append((cv.cvtColor(small,cv.COLOR_BGR2GRAY),full))
        small=cv.resize(array,(250,450),interpolation=cv.INTER_AREA)
        gray=cv.cvtColor(small[35:420],cv.COLOR_BGR2GRAY)
        found=[]
        for template,full_templates in self.portrait_scales:
            result=cv.matchTemplate(gray,template,cv.TM_CCOEFF_NORMED)
            for _ in range(3):
                _,score,_,pos=cv.minMaxLoc(result)
                if score<.76:break
                px,py=pos;h,w=template.shape
                x,y=px*2,(py+35)*2
                roi=array[max(0,y-5):min(900,y+h*2+5),max(0,x-5):min(500,x+w*2+5)]
                for full in full_templates:
                    fh,fw=full.shape[:2]
                    if fh>roi.shape[0] or fw>roi.shape[1]:continue
                    scores=cv.matchTemplate(roi,full,cv.TM_CCOEFF_NORMED)
                    _,color_score,_,point=cv.minMaxLoc(scores)
                    if color_score>=.84:
                        cx=(max(0,x-5)+point[0]+fw/2)/500
                        cy=(max(0,y-5)+point[1]+fh/2)/900
                        found.append((cx,cy))
                        break
                result[max(0,py-h//2):py+h//2+1,max(0,px-w//2):px+w//2+1]=-1
        return tuple(sorted({(0 if y<.5 else 2)+(0 if x<.5 else 1) for x,y in found}))

    def marker_template(self,width):
        if width not in self.marker_scales:
            cv=self.cv;np=self.np;source=self.templates['target']
            height=round(source.shape[0]*width/source.shape[1])
            template=cv.resize(source,(width,height),interpolation=cv.INTER_AREA)
            yy,xx=np.ogrid[:height,:width]
            distance=((xx-width/2)/(width/2))**2+((yy-height/2)/(height/2))**2
            mask=((distance>.12)&(distance<.95)).astype('uint8')*255
            self.marker_scales[width]=(template,mask)
        return self.marker_scales[width]

    def detect_marker(self,array):
        cv=self.cv;np=self.np
        small=cv.resize(array,(250,450),interpolation=cv.INTER_AREA)[60:420]
        best=.88;answer=None
        for width in (21,25,30,35,40):
            template,mask=self.marker_template(width);h=template.shape[0]
            scores=cv.matchTemplate(small,template,cv.TM_CCOEFF_NORMED,mask=mask)
            scores=np.nan_to_num(scores,nan=-1,posinf=-1,neginf=-1)
            for _ in range(2):
                _,score,_,point=cv.minMaxLoc(scores)
                if score<.72:break
                px,py=point;x=px*2;y=(py+60)*2
                left=max(0,x-8);top=max(0,y-8)
                roi=array[top:min(900,y+h*2+8),left:min(500,x+width*2+8)]
                for full_width in range(width*2-4,width*2+5,2):
                    full,full_mask=self.marker_template(full_width);fh,fw=full.shape[:2]
                    if fh>roi.shape[0] or fw>roi.shape[1]:continue
                    result=cv.matchTemplate(roi,full,cv.TM_CCOEFF_NORMED,mask=full_mask)
                    result=np.nan_to_num(result,nan=-1,posinf=-1,neginf=-1)
                    _,value,_,pos=cv.minMaxLoc(result)
                    if value>=best:
                        best=value;answer=((left+pos[0]+fw/2)/500,(top+pos[1]+fh/2)/900)
                scores[max(0,py-h//2):py+h//2+1,max(0,px-width//2):px+width//2+1]=-1
        return answer

    def roi(self,image,name):
        return image.crop(tuple(round(v*(image.width if i%2==0 else image.height))
                                for i,v in enumerate(self.regions[name])))

    def match(self,array,name,box,scales,threshold):
        cv=self.cv;np=self.np;l,t,r,b=box;roi=array[t:b,l:r];template=self.templates[name]
        best=0.;point=None
        gray_roi=cv.cvtColor(roi,cv.COLOR_BGR2GRAY) if name=='shaman' else None
        for width in scales:
            height=round(template.shape[0]*width/template.shape[1])
            if height>roi.shape[0] or width>roi.shape[1]:continue
            resized=cv.resize(template,(width,height),interpolation=cv.INTER_AREA)
            # Color matching keeps bright explosion highlights from matching a portrait.
            if name=='target':
                yy,xx=np.ogrid[:height,:width]
                distance=((xx-width/2)/(width/2))**2+((yy-height/2)/(height/2))**2
                mask=((distance>.12)&(distance<.95)).astype('uint8')*255
                result=cv.matchTemplate(roi,resized,cv.TM_CCOEFF_NORMED,mask=mask)
            elif name=='shaman':
                # Locate in grayscale, then verify the exact patch in color.
                # This keeps the larger scale search fast without trusting colorless matches.
                result=cv.matchTemplate(gray_roi,cv.cvtColor(resized,cv.COLOR_BGR2GRAY),cv.TM_CCOEFF_NORMED)
            else:result=cv.matchTemplate(roi,resized,cv.TM_CCOEFF_NORMED)
            result=np.nan_to_num(result,nan=-1,posinf=-1,neginf=-1)
            _,score,_,pos=cv.minMaxLoc(result)
            if name=='shaman':
                px,py=pos
                patch=roi[py:py+height,px:px+width]
                score=float(cv.matchTemplate(patch,resized,cv.TM_CCOEFF_NORMED)[0,0])
            if score>best:best=score;point=((l+pos[0]+width/2)/500,(t+pos[1]+height/2)/900)
        return point if best>=threshold else None,best

    def frame(self,image):
        image=image.convert('RGB').resize((500,900));array=self.np.array(image)[:,:,::-1].copy()
        start,start_score=self.match(array,'start',(65,780,500,900),(140,156,172),.86)
        # The old lock anchor contained the x2 quantity and depended on its digits.
        # Match only the fixed upper part of the purple skill icon.
        if 'lock_core' not in self.templates:
            self.templates['lock_core']=self.templates['lock'][6:36,7:53]
        lock,lock_score=self.match(array,'lock_core',(370,650,500,860),range(36,65,4),.82)
        exit_,exit_score=self.match(array,'exit',(160,800,340,900),range(30,55,4),.83)
        score=self.score_signature(image)
        scene='lobby' if start else ('arena' if lock and (exit_ or score is not None) else 'unknown')
        evidence=f'开始按钮={start_score:.2f}，锁定图标={lock_score:.2f}，退出按钮={exit_score:.2f}，个人分数字形={score is not None}'
        bosses=[];marker=None
        if scene=='arena':
            bosses=self.detect_bosses(array)
            marker=self.detect_marker(array)
        score=self.score_signature(image) if scene=='arena' else None
        return Observation(scene=scene,bosses=tuple(bosses),marker=marker,score=score,evidence=evidence),image

    def score_signature(self,image):
        # Top personal score uses yellow digits. Ignore background, glow and tiny jitter.
        cv=self.cv;np=self.np
        roi=np.array(self.roi(image,'score').resize((174,48)))
        mask=((roi[:,:,0]>165)&(roi[:,:,1]>135)&(roi[:,:,2]<135)).astype('uint8')*255
        count=cv.countNonZero(mask)
        if not 80<count<mask.size*.6:return None
        # Coarse occupancy requires a stable text-shape change on two frames.
        small=cv.resize(mask,(29,8),interpolation=cv.INTER_AREA)
        return bytes((small>85).astype('uint8').ravel())

    @staticmethod
    def parse_multiplier(lines):
        values=[]
        for line in lines:
            text=re.sub(r'[，,。·]', '', engine.compact(line['text']))
            values.extend(int(v) for v in re.findall(r'(?<!\d)(\d{1,4})倍场',text))
        return values[0] if values and len(set(values))==1 else None

    @staticmethod
    def parse_stock(lines):
        values=[]
        for line in lines:
            text=engine.compact(line['text']).lower().replace('×','x').replace('✕','x')
            # OCR may include an icon fragment before the actual x7 label.
            match=re.fullmatch(r'[^\w]*[x*](\d{1,3})',text)
            if match is None:match=re.fullmatch(r'(\d{1,3})',text)
            if match:values.append(int(match.group(1)))
        return values[0] if values and len(set(values))==1 else None

    @staticmethod
    def ocr_variants(crop,multiplier=False):
        # Preserve glyph proportions; a border separates text from the crop edge.
        crop=crop.convert('RGB')
        binary=crop.convert('L').point(lambda value:0 if value>185 else 255)
        variants=((crop,140),(crop,150),(crop,200),(crop,300)) if multiplier else ((crop,140),(crop,200),(crop,240),(binary,100),(binary,150))
        for source,height in variants:
            width=max(1,round(source.width*height/source.height))
            scaled=source.resize((width,height))
            yield ImageOps.expand(scaled,border=30,fill='white')

    def read_consensus(self,crops,parser):
        for crop in crops:
            reads=[parser(engine.ocr(variant)) for variant in self.ocr_variants(crop,parser==self.parse_multiplier)]
            valid=[value for value in reads if value is not None]
            # Two preparations must agree; conflicting values never enable entry.
            if len(valid)>=2 and len(set(valid))==1:return valid[0]
        return None

    def entry_crops(self,image,scene):
        name='lobby_multiplier' if scene=='lobby' else 'arena_multiplier'
        # Fall back to the whole UI band if a saved narrow calibration misses text.
        band=(.36,.53,.88,.65) if scene=='lobby' else (.25,0,.82,.075)
        multiplier=[self.roi(image,name),image.crop(tuple(round(v*(image.width if i%2==0 else image.height))
                                                     for i,v in enumerate(band)))]
        # Use the supplied badge to locate text, never as proof of the multiplier.
        normalized=image.convert('RGB').resize((500,900))
        array=self.np.array(normalized)[:,:,::-1].copy()
        box=(120,475,460,590) if scene=='lobby' else (100,0,440,65)
        center,_=self.match(array,'multiplier_200',box,range(65,151,2),.75)
        if center:
            x,y=center[0]*500,center[1]*900
            multiplier.append(normalized.crop((max(0,round(x-80)),max(0,round(y-25)),
                                                min(500,round(x+80)),min(900,round(y+25)))))
        stock=[self.roi(image,'stock')]
        if scene=='arena':
            normalized=image.convert('RGB').resize((500,900))
            array=self.np.array(normalized)[:,:,::-1].copy()
            center,_=self.match(array,'lock_core',(370,650,500,860),range(36,65,4),.82)
            if center:
                # Quantity is on the lower right of the purple lock skill icon.
                x,y=center[0]*500,center[1]*900
                box=(max(0,x+8),max(0,y+8),min(500,x+48),min(900,y+44))
                stock.insert(0,normalized.crop(tuple(round(v) for v in box)))
        return multiplier,stock

    def read_entry(self,image,scene,read_stock=True):
        multiplier_crops,stock_crops=self.entry_crops(image,scene)
        multiplier=self.read_consensus(multiplier_crops,self.parse_multiplier)
        stock=self.read_consensus(stock_crops,self.parse_stock) if scene=='arena' and read_stock else None
        return multiplier,stock



def run(session,stop,emit,observe=True,rage=False,max_attack=15,regions=None,observe_events=False,auto_next=True,sound_alert=False,pause_low_stock=True,pause_repeated_unknown=False):
    observe=observe or observe_events
    vision=Vision(regions);policy=Policy(rage,max_attack,auto_next,pause_low_stock);tracker=ObserveTracker();alarm=AppearanceAlarm(required_hits=1)
    previous_scene=None;scene_hits=0;unknown_since=None;last_status=None
    last_speed_report=None;durations=[]
    cached_multiplier=None;cached_stock=None;slow_scene=None;unknown_exits=[]
    while not stop.is_set():
        started=time.monotonic()
        # Session already checks Android package and the physical/logical display route.
        observation,image=vision.frame(session.capture_game())
        durations.append(time.monotonic()-started)
        if last_speed_report is None or started-last_speed_report>=10:
            emit('log',f'围猎截图+识别平均耗时：{sum(durations)/len(durations):.3f}秒/帧（含ADB，不含入场OCR）。')
            last_speed_report=started;durations=[]
        if sound_alert and alarm.step(observation,started):
            emit('sound',f'检测到萨满头像，象限{observation.bosses[0]+1}')
            emit('log',f'萨满出现，象限{observation.bosses[0]+1}，已请求声音提醒。')
        scene_hits=scene_hits+1 if observation.scene==previous_scene else 1
        previous_scene=observation.scene
        if observe_events:
            # Passive monitoring bypasses entry OCR and every input-producing path.
            reason=tracker.step(observation,started)
            if reason!=last_status:
                emit('status',reason);emit('log',reason);last_status=reason
            if stop.wait(max(0,.05-(time.monotonic()-started))):return
            continue
        if observation.scene=='unknown':
            if unknown_since is None:
                unknown_since=started
                emit('log','围猎未知画面诊断：'+observation.evidence)
            if observe:
                if started-unknown_since>=5:
                    raise RuntimeError('仅观察：围猎画面连续未确认，已暂停；未发送操作。')
            else:
                action,reason='wait',''
                if policy.phase=='exiting':
                    action,reason=policy.step(observation,started)
                elif started-unknown_since>=5:
                    low=pause_low_stock and policy.stock is not None and policy.stock<3
                    unknown_exits=[t for t in unknown_exits if started-t<=120]
                    unknown_exits.append(started)
                    repeated=pause_repeated_unknown and len(unknown_exits)>=3
                    next_step='确认离场后重新核对200倍场并继续。' if auto_next and not low and not repeated else '离场后暂停。'
                    if repeated:next_step+='2分钟内连续发生3次未知退出，停止重复重进，请核对后台画面及游戏区域。'
                    if low:next_step+='锁定不足3个，请去1倍场打道具。'
                    action,reason=policy.exit(started,'围猎画面连续未确认，尝试退出以停止锁定消耗；'+next_step,low or repeated)
                if action=='exit':
                    session.tap('hunt_exit');emit('log',reason);emit('status',reason)
                elif action=='pause':
                    reason+=' 请立即手动核对并退出，避免锁定继续消耗金币。'
                    emit('notify',reason)
                    raise RuntimeError(reason)
            if stop.wait(max(0,.05-(time.monotonic()-started))):return
            continue
        unknown_since=None
        if scene_hits<2:continue
        if slow_scene!=observation.scene and policy.phase!='exiting':
            cached_multiplier,cached_stock=vision.read_entry(image,observation.scene,read_stock=pause_low_stock or observe)
            slow_scene=observation.scene
            # Discard the pre-OCR frame. The next snapshot supplies current targets.
            quantity=cached_stock if pause_low_stock or observe else '未读取（已关闭数量检查）'
            emit('log',f'围猎入场校对：{observation.scene}，倍率={cached_multiplier}，锁定数量={quantity}。')
            continue
        observation.multiplier=cached_multiplier;observation.stock=cached_stock
        if observe:
            emit('status',f'仅观察：{observation.scene}，倍率={cached_multiplier}，锁定={cached_stock}，萨满角={observation.bosses}，锁定标志={observation.marker}。未点击。')
            return
        # While attacking, use remaining stock tracked from our one lock operation.
        if policy.phase=='locked':observation.stock=None
        action,reason=policy.step(observation,time.monotonic())
        if reason!=last_status:emit('status',reason);last_status=reason
        if action=='pause':
            if policy.phase=='exiting' and observation.scene=='lobby':
                reason+=' 已确认回到围猎主页，已暂停。'
            if '1倍场' in reason:emit('notify',reason)
            raise RuntimeError(reason)
        if action in ('start','lock','rage','exit'):
            session.tap('hunt_'+action);emit('log',reason)
            if action=='lock' and cached_stock is not None:cached_stock=max(0,cached_stock-1)
            if action=='exit':slow_scene='arena'
        if stop.wait(max(0,.05-(time.monotonic()-started))):return
