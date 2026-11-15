import json, os, re, subprocess, tempfile, time, math
from pathlib import Path
from PIL import ImageOps
import visual

ROOT=Path(__file__).resolve().parent
DATA=ROOT/'data'
DATA.mkdir(exist_ok=True)
CATALOG=DATA/'catalog.json'
CONFIG=DATA/'settings.json'
OCR_SCRIPT=ROOT/'ocr.ps1'
LAST_DETECTION={}
CURRENT_KIND='unknown'
SESSION=None


INITIAL_CATALOG = {
    '黄金圣甲虫':0, '黄金圣权杖':0, '黑曜猫神像':0, '荷鲁斯之眼':0,
    '璀璨之心':0, '灵蛇宝镯':0, '古代盛装':0, '古代印记石板':0,
    '喵乃伊':0, '守护陶罐':0, '宫廷臭水瓶':0, '神鸟摆件':0,
    '梅看见雕像':0, '神秘草药':0, '陶碗':0, '猫猫头沙漏':0,
    '莎草挂画':0, '古币巧克力':0, '亡灵腌鱼':0, '图坦卡门金面':0,
}

KNOWN = [name for name in INITIAL_CATALOG if not name.startswith('??? #')]

def load(path, default):
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return default

def atomic_save(path, data):
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    os.replace(tmp, path)

def ocr(im):
    name = None
    try:
        with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as f:
            name = f.name
        im.save(name)
        p = subprocess.run(['powershell.exe','-NoLogo','-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File',str(OCR_SCRIPT),'-ImagePath',name], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30, creationflags=subprocess.CREATE_NO_WINDOW)
        if p.returncode:
            raise RuntimeError('Windows OCR 出错：' + p.stderr.decode('utf-8', errors='replace')[-500:])
        data = json.loads(p.stdout.decode('utf-8-sig'))
        return data.get('lines', []) or []
    finally:
        if name:
            try: os.unlink(name)
            except OSError: pass

def compact(s):
    return re.sub(r'\s+', '', s).replace('％','%').replace('＋','+')

def read_chance(lines):
    # A solo player may have one percentage; a visible bonus must be complete.
    for line in lines:
        s = compact(line['text']).replace('O','0').replace('o','0')
        if '胜率' not in s: continue
        m = re.search(r'胜率(\d{1,3})%(?:\+(\d{1,3})%)?', s)
        if m:
            if re.search(r'[+%\d]',s[m.end():]): return None
            a=int(m.group(1)); b=int(m.group(2) or 0)
            if 0 <= a <= 100 and 0 <= b <= 100 and a+b <= 100:
                return a+b
    return None

def should_retreat(chance, threshold):
    return chance <= threshold

def is_placeholder(name):
    return bool(re.fullmatch(r'\?\?\? #\d+', name))

def ordered_catalog(data):
    # Python's stable sort preserves the earlier displayed order for ties.
    return dict(sorted(data.items(), key=lambda item: (is_placeholder(item[0]), -item[1])))

def promoted_catalog(catalog,name,value):
    if not name or is_placeholder(name) or not isinstance(value,int) or value<=catalog.get(name,0): return None
    updated=dict(catalog)
    if name not in updated:
        vacant=next((key for key in updated if is_placeholder(key)),None)
        if vacant is None: return None
        del updated[vacant]
    updated[name]=value
    return ordered_catalog(updated)

def fill_catalog_slots(data):
    result = dict(data)
    added_name='图坦卡门金面'
    if added_name not in result and (set(KNOWN)-{added_name}).issubset(result):
        vacant=next((key for key,value in result.items() if is_placeholder(key) and value==0),None)
        if vacant is not None:
            del result[vacant]
            result[added_name]=0
        elif len(result)<20: result[added_name]=0
    if set(KNOWN).issubset(result):
        for key in list(result):
            if len(result)<=20: break
            if is_placeholder(key) and result[key]==0: del result[key]
    index = 1
    while len(result) < 20:
        name = f'??? #{index}'
        if name not in result: result[name] = 0
        index += 1
    return result

def parse_catalog_text(text):
    result = {}
    placeholders = 0
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith('#'): continue
        name,sep,value = line.partition('=')
        name=name.strip(); value=value.strip()
        if name in ('???', '？？？'):
            placeholders += 1
            name = f'??? #{placeholders}'
            if value != '0': raise ValueError('？？？的分数必须为 0：'+line)
        if not sep or not name or not value.isdigit() or name in result:
            raise ValueError('图鉴格式有误或名称重复：'+line)
        result[name] = int(value)
    if len(result) != 20: raise ValueError(f'图鉴需要 20 项，目前有 {len(result)} 项。未解锁的项目请填“？？？=0”。')
    return result

def format_catalog(data):
    lines = []
    for i, (name,value) in enumerate(ordered_catalog(data).items()):
        if i % 4 == 0:
            if i: lines.append('')
            lines.append(f'# 第 {i // 4 + 1} 排（从左到右）')
        lines.append(f'{"？？？" if is_placeholder(name) else name}={value}')
    return '\n'.join(lines) + '\n'

def identify(lines, catalog):
    # Treasure label and value occupy the central band of the obtained-item screen.
    center = [v for v in lines if 850 <= v['y'] <= 1250]
    joined = ''.join(compact(v['text']) for v in lines)
    names = set(KNOWN) | {name for name in catalog if not is_placeholder(name)}
    hits = [name for name in names if name in joined]
    if len(hits) != 1: return None, None
    nums = []
    for v in center:
        if any(name in compact(v['text']) for name in hits): continue
        s = compact(v['text'])
        match=re.fullmatch(r'[^\d]{0,3}(\d{2,7})[^\d]{0,2}',s)
        if match: nums.append(int(match.group(1)))
    return hits[0], nums[0] if len(nums)==1 else None

def read_floor(lines):
    for line in lines:
        if line.get('y',0)>250: continue
        match=re.search(r'探险第(\d{1,3})层',compact(line['text']))
        if match: return int(match.group(1))
    return None

def read_multiplier(lines):
    values=[]
    for line in lines:
        match=re.fullmatch(r'(\d{1,3})倍[场饧]?',compact(line['text']))
        if match: values.append(int(match.group(1)))
    return values[0] if len(values)==1 else None

def forward_button_visible(image):
    if image is None: return False
    # Bright green/cyan central round button, excluding the blue fight panel.
    def fraction(box,predicate):
        w,h=image.size
        roi=image.crop(tuple(round(v*(w if i%2==0 else h)) for i,v in enumerate(box))).resize((48,48)).convert('HSV')
        pixels=list(roi.getdata())
        return sum(predicate(*p) for p in pixels)/len(pixels)
    green=fraction((.38,.77,.62,.89),lambda h,s,v: 45<=h<=140 and s>95 and v>150)
    blue=fraction((.68,.72,.91,.89),lambda h,s,v: 125<=h<=175 and s>100 and v>100)
    return green>.36 and blue<.20

def home_signals(lines):
    text=''.join(compact(v['text']) for v in lines)
    signals=[]
    if '图鉴' in text: signals.append('图鉴')
    if '即将获得' in text: signals.append('即将获得')
    if '排名' in text: signals.append('排名')
    # Accept a truncated title, including OCR lines merged with the torch count.
    if re.search(r'遗迹.{0,2}探',text): signals.append('活动标题')
    return signals

def classify(lines, image=None):
    global LAST_DETECTION
    text = ''.join(compact(v['text']) for v in lines)
    # A completed expedition can share the green claim control with a buff.
    if ('探险结束' in text or '总收益' in text) and '收下' in text:
        LAST_DETECTION={'method':'结算文字','score':1.0}
        return 'result'
    visual_kind,score,scores=visual.detect(image)
    LAST_DETECTION={'method':'图像匹配','score':score,'scores':scores}
    if visual_kind!='unknown': return visual_kind
    if scores.get('_ambiguous'):
        LAST_DETECTION['method']='图像状态冲突，等待确认'
        return 'unknown'
    LAST_DETECTION['method']='文字辅助'
    if '撤离' in text and '战斗' in text and '胜率' in text: return 'enemy'
    # The crossed-swords artwork often prevents OCR from reading 战斗.
    # Confirm this page with several independent encounter-specific messages.
    retreat=any(label in text for label in ('撤离','撒离','保住当前全部宝藏'))
    risk='失败' in text and '部分宝' in text
    blocked='挡住' in text and '路' in text
    if '胜率' in text and retreat and (risk or blocked): return 'enemy'
    if '探险结束' in text and '收下' in text: return 'result'
    if ('超越全服' in text and '玩家' in text) or '传说宝藏' in text: return 'treasure'
    if any(buff in text for buff in ('好事成三','点金之触','圣光护体')) or '开心收下' in text: return 'buff'
    if '下一个宝藏' in text and '倍物品' in text: return 'buff'
    if '任意位置继续' in text:
        if any(buff in text for buff in ('好事成三','点金之触','圣光护体')): return 'buff'
        return 'treasure'
    if '去探险' in text: return 'home'
    # Any two independent home labels can confirm the screen. No single label
    # is mandatory; title truncation or a missed button caption is tolerated.
    if len(home_signals(lines))>=2 and read_floor(lines) is None:
        return 'home'
    if '前进' in text and '探险第' in text: return 'forward'
    if read_floor(lines) is not None and not any(word in text for word in ('撤离','战斗','臭臭','点击','结束')) and forward_button_visible(image): return 'forward'
    return 'unknown'

def parse_chance_number(text):
    s=compact(text).replace('O','0').replace('o','0')
    match=re.search(r'(\d{1,3})%(?:\+(\d{1,3})%)?',s)
    if not match or re.search(r'[+%\d]',s[match.end():]): return None
    first=int(match.group(1)); bonus=int(match.group(2) or 0)
    return first+bonus if first<=100 and bonus<=100 and first+bonus<=100 else None

def _crop_ratio(image,box):
    w,h=image.size
    return image.crop(tuple(round(v*(w if i%2==0 else h)) for i,v in enumerate(box)))

def _regional_numbers(image,box,parser,expected=None):
    region=_crop_ratio(image,box)
    region=region.resize((region.width*2,region.height*2))
    results=[]
    for candidate in (ImageOps.grayscale(region).convert('RGB'),region):
        try: lines=ocr(candidate)
        except RuntimeError: continue
        values=[value for line in lines if (value:=parser(line['text'])) is not None]
        if len(values)==1:
            results.append(values[0])
            if expected is not None and values[0]==expected: break
    return results

def observe_game(image):
    global CURRENT_KIND
    try: lines=ocr(image)
    except RuntimeError:
        if visual.detect(image)[0]=='unknown': raise
        lines=[]
    kind=classify(lines,image)
    if kind=='enemy':
        full=read_chance(lines)
        readings=_regional_numbers(image,(.52,.845,.96,.935),parse_chance_number,expected=full)
        if full is not None: readings.append(full)
        # Do not pick a convenient value when the independent reads conflict.
        chance=readings[0] if len(readings)>=2 and len(set(readings))==1 else None
        lines=[line for line in lines if '胜率' not in compact(line['text'])]
        if chance is not None: lines.append({'text':f'我方胜率{chance}%','x':520,'y':1500,'w':300,'h':50})
        LAST_DETECTION['chance_reads']=readings
    if kind=='home' and read_multiplier(lines) is None:
        def parse_badge(text):
            match=re.fullmatch(r'(\d{1,3})倍[场饧]?',compact(text))
            return int(match.group(1)) if match else None
        readings=_regional_numbers(image,(.63,.72,.92,.85),parse_badge)
        if len(readings)>=2 and len(set(readings))==1:
            lines.append({'text':f'{readings[0]}倍场','x':700,'y':1350,'w':150,'h':50})
    CURRENT_KIND=kind
    return kind,lines

def scan_candidates(lines):
    found = {}
    for item in lines:
        s = compact(item['text'])
        names = [n for n in KNOWN if n in s]
        if len(names) != 1: continue
        cx = item['x'] + item['w']/2
        values = []
        for v in lines:
            z = compact(v['text'])
            match = re.fullmatch(r'[^\d]{0,3}(\d{2,7})[^\d]{0,2}',z)
            if match and abs(v['x']+v['w']/2-cx) < 160 and 0 < v['y']-(item['y']+item['h']) < 120:
                values.append((v['y'],int(match.group(1))))
        if values:
            found[names[0]] = min(values)[1]
    return found

def catalog_columns(image):
    # OCR treats all four cards on a row as one sentence. Read columns independently.
    width, height = image.size
    part = width // 4
    return [image.crop((column*part,0,(column+1)*part,height)) for column in range(4)]

def read_catalog_column(column):
    # Run OCR on enlarged colour and grayscale versions. Two agreeing readings
    # reduce, but cannot eliminate, missed digits. Values still need review.
    enlarged=column.resize((column.width*2,column.height*2))
    readings=[]
    for variant in (enlarged, ImageOps.grayscale(enlarged).convert('RGB')):
        lines=ocr(variant)
        scaled=[dict(v, x=v['x']/2, y=v['y']/2, w=v['w']/2, h=v['h']/2) for v in lines]
        readings.append(scan_candidates(scaled))
    return readings

def merge_catalog_readings(catalog, pairs):
    """Use agreeing scan values, including corrections below saved records."""
    result=dict(catalog)
    warnings=[]
    changed=[]
    for first, second in pairs:
        for name in sorted(set(first) | set(second)):
            a,b=first.get(name),second.get(name)
            if a is None or b is None or a != b:
                warnings.append(f'{name} 识别不一致（{a if a is not None else "未识别"} / {b if b is not None else "未识别"}），保留原值')
                continue
            old=result.get(name)
            if old is None:
                vacant=next((key for key in result if is_placeholder(key)),None)
                if vacant is None:
                    warnings.append(f'{name} 未在图鉴中且没有空位，请手动核对')
                    continue
                del result[vacant]
            result[name]=a
            changed.append(name)
    return result, warnings, changed

def scan_catalog_pages(hwnd, baseline, stop, emit):
    """Read the top and bottom views, with exactly one scroll between them."""
    found=dict(baseline)
    all_warnings=[]
    for page in ('顶部', '底部'):
        if stop.is_set(): return None
        image=capture(hwnd)
        pairs=[]
        for column in catalog_columns(image):
            if stop.is_set(): return None
            pairs.append(read_catalog_column(column))
        require_focus(hwnd)
        if stop.is_set(): return None
        found, warnings, accepted=merge_catalog_readings(found,pairs)
        all_warnings.extend(warnings)
        emit(f'扫描{page}画面，双次识别一致 {len(accepted)} 项：'+ '、'.join(accepted))
        for warning in warnings: emit('请核对：'+warning)
        if page == '顶部':
            if stop.is_set(): return None
            scroll(hwnd,-2400)
            emit('已滚动，等待 2 秒让画面回正后扫描底部。')
            if stop.wait(2): return None
    return found, all_warnings

def require_focus(unused=None):
    if SESSION is None: raise RuntimeError('请先连接并绑定游戏。')
    SESSION.guard()

def capture(unused=None):
    require_focus()
    return SESSION.capture_game()

def send_key(unused,key,stop):
    if stop.is_set(): return False
    if key=='space': target=CURRENT_KIND if CURRENT_KIND in ('home','forward','treasure','buff','result') else 'unknown'
    else: target={'a':'retreat','d':'fight','c':'catalog'}[key]
    SESSION.tap(target)
    return not stop.is_set()

def scroll(unused,amount):
    SESSION.swipe_catalog(down=amount<0)

def wait_game(unused,stop,seconds):
    deadline=time.monotonic()+seconds
    while not stop.is_set():
        require_focus()
        remaining=deadline-time.monotonic()
        if remaining<=0:return True
        if stop.wait(min(.15,remaining)):return False
    return False


class Runner:
    def run_game(self,hwnd,catalog,a,b,continuous,observe):
        protected=False; pending={}; previous=None; repeat=0; last_action=0; unknown_since=None; recorded=False; entered=False
        last_detail=None
        number_unread_since=None
        saw_expedition=False
        unknown_next_space=None
        next_scan=0
        last_treasure_read=None
        self.q.put(('status','仅观察检查中，不发送游戏按键。' if observe else '自动探险运行中；Windows 切换窗口不暂停；F9 可停止。'))
        while not self.stop.is_set():
            remaining=next_scan-time.monotonic()
            if remaining>0 and not wait_game(hwnd,self.stop,remaining): return
            next_scan=time.monotonic()+1
            image=capture(hwnd)
            kind,lines=observe_game(image)
            require_focus(hwnd)
            if self.stop.is_set(): return
            self.q.put(('log',f'识别方式：{LAST_DETECTION.get("method","未知")}，匹配分数 {LAST_DETECTION.get("score",0):.2f}'))
            if kind=='enemy': self.q.put(('log','胜率校对读数：'+str(LAST_DETECTION.get('chance_reads',[]))))
            if kind=='home': self.q.put(('log','主页文字辅助标识：'+('、'.join(home_signals(lines)) or '未读取到，参考图像判断')))
            self.q.put(('log','识别：'+kind+' | '+' / '.join(compact(x['text']) for x in lines)[:200]))
            detail = (kind,read_chance(lines)) if kind=='enemy' else (kind,)
            previous_treasure_read=last_treasure_read
            last_treasure_read=identify(lines,catalog) if kind=='treasure' else None
            unread=not observe and ((kind=='enemy' and read_chance(lines) is None) or (kind=='home' and read_multiplier(lines) is None))
            if unread:
                if number_unread_since is None: number_unread_since=time.monotonic()
                if time.monotonic()-number_unread_since>15: raise RuntimeError('画面已确认，但胜率或倍率连续读取不一致，已暂停。')
                self.q.put(('log','画面已确认，数字仍需核对，重新读取中。'))
                continue
            number_unread_since=None
            if detail==last_detail: repeat+=1
            else: last_detail=detail; repeat=1
            previous=kind
            if kind=='unknown':
                if unknown_since is None:
                    unknown_since=time.monotonic()
                    unknown_next_space=unknown_since+1
            if repeat<2: continue
            if kind=='unknown':
                if observe:
                    self.q.put(('status','仅观察：当前画面未知，没有后台点击继续。'));return
                now=time.monotonic()
                if now-unknown_since>=60: raise RuntimeError('连续 1 分钟仍无法识别画面，已暂停，不再尝试继续。')
                if now>=unknown_next_space:
                    protected=True;saw_expedition=True
                    if not send_key(hwnd,'space',self.stop): return
                    finished=time.monotonic()
                    interval=1 if finished-unknown_since<20 else 5
                    unknown_next_space=finished+interval
                    self.q.put(('log',f'未知画面已持续 {int(now-unknown_since)} 秒：后台点击继续，{interval} 秒后仍未知再尝试。'))
                interval=1 if now-unknown_since<20 else 5
                self.q.put(('status',f'未知画面恢复中：{int(now-unknown_since)}/60 秒，当前每隔 {interval} 秒尝试继续；F9 可停止。'))
                continue
            if unknown_since is not None:
                self.q.put(('log','画面已重新确认，停止未知画面继续重试。'))
                unknown_since=None;unknown_next_space=None
            if observe:
                label={'home':'活动主页','forward':'准备前进','enemy':'撤离/战斗选择','treasure':'宝藏展示','buff':'增益展示','result':'结算'}.get(kind,kind)
                self.q.put(('status',f'检查完成：{label}，没有操作。要自动探险请取消“仅观察”。'))
                return
            if kind in ('forward','treasure','enemy','buff','result'): saw_expedition=True
            if not entered and kind in ('forward','treasure','enemy','buff','result'):
                entered=True
                protected=not (kind=='forward' and read_floor(lines)==1)
                self.q.put(('log','已接续本局，按保护纪录阈值处理。' if protected else '已确认第1层，可以从这里开始前进。'))
            if kind=='home' and read_multiplier(lines)!=1:
                raise RuntimeError('仅支持已识别的 1 倍场；请设置 1 倍并确认画面可识别。')
            if kind=='home':
                if (recorded or saw_expedition) and not continuous:
                    self.q.put(('status','已回到活动主页，单局模式暂停。')); return
                protected=False; pending={}; recorded=False; entered=True; saw_expedition=False
                if not send_key(hwnd,'space',self.stop): return
                self.q.put(('log','后台点击进入探险。'))
            elif kind=='forward':
                if not send_key(hwnd,'space',self.stop): return
                self.q.put(('log','后台点击前进，继续每秒识别。'))
            elif kind in ('treasure','buff','result'):
                fresh_lines=lines
                if kind=='treasure':
                    name,value=identify(fresh_lines,catalog)
                    if name and value is not None:
                        old=catalog.get(name)
                        protected=protected or old is None or value>old
                        pending[name]=max(value,pending.get(name,0))
                        self.q.put(('log',f'宝藏：{name} {value}；图鉴 {old if old is not None else "未知"}；保护模式={protected}。'))
                        if previous_treasure_read==(name,value):
                            updated=promoted_catalog(catalog,name,value)
                            if updated is not None:
                                atomic_save(CATALOG,updated)
                                catalog.clear();catalog.update(updated)
                                self.q.put(('catalog_update',updated))
                                self.q.put(('log',f'图鉴已自动保存：{name}，{old if old is not None else 0} → {value}。'))
                        else:
                            self.q.put(('log','两次宝藏读数不一致，本次不覆盖图鉴，请之后核对。'))
                    else:
                        protected=True
                        self.q.put(('log','已确认宝藏展示，但名称或价值未读清；本局按保护阈值处理，图鉴请之后核对。'))
                elif kind=='result': recorded=True
                else: self.q.put(('log','已确认获得增益。'))
                if not send_key(hwnd,'space',self.stop): return
                self.q.put(('log','后台点击收下/继续。'))
            elif kind=='enemy':
                chance=read_chance(lines)
                if chance is None: raise RuntimeError('无法可靠读取胜率，请手动处理。')
                threshold=b if protected else a
                retreat=should_retreat(chance,threshold)
                self.q.put(('log',f'胜率 {chance}%，阈值 {threshold}%：'+('撤离' if retreat else '战斗')))
                key='a' if retreat else 'd'
                if not send_key(hwnd,key,self.stop): return
                self.q.put(('log','已后台点击'+('撤离' if retreat else '战斗')+'，继续每秒识别。'))
            else:
                continue
            last_action=time.monotonic(); previous=None; last_detail=None; repeat=0
