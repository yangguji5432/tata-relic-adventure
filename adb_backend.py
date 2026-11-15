"""ADB transport: all game screenshots and touches stay inside one device."""
import io
import math
from pathlib import Path
import re
import subprocess
import time
from dataclasses import dataclass
from PIL import Image

DEFAULT_POINTS={
 'home':[.52,.85], 'forward':[.50,.82], 'treasure':[.50,.70],
 'buff':[.50,.92], 'result':[.50,.92], 'retreat':[.26,.77],
 'fight':[.74,.77], 'catalog':[.10,.86], 'unknown':[.50,.88],
}

class AdbError(RuntimeError): pass

def find_adb():
    import shutil
    candidates=[shutil.which('adb')]
    candidates += [str(Path(root)/tail) for root in ('C:/Program Files','C:/Program Files (x86)') for tail in ('Netease/MuMu/nx_main/adb.exe','Netease/MuMu/nx_device/12.0/shell/adb.exe')]
    return next((p for p in candidates if p and Path(p).is_file()),'')

def parse_devices(text):
    devices=[]
    for line in text.splitlines():
        fields=line.split()
        if len(fields)>=2 and fields[1]=='device': devices.append(fields[0])
    return devices

def parse_foreground(text):
    patterns=(r'topResumedActivity[^\n]*?\bu\d+\s+([A-Za-z0-9_.]+)/',r'mResumedActivity[^\n]*?\bu\d+\s+([A-Za-z0-9_.]+)/',r'mCurrentFocus[^\n]*?\bu\d+\s+([A-Za-z0-9_.]+)/')
    for pattern in patterns:
        match=re.search(pattern,text)
        if match:return match.group(1)
    return None


@dataclass(frozen=True)
class DisplayTarget:
    package: str
    logical_id: int
    physical_id: str


def parse_display_activities(text):
    """Only display-local resumed records, never the global dump trailer.

    Task fields must be nested below the root task. The display's explicit
    `Resumed:` summary is also accepted. A flat/global resumed record cannot
    establish which display owns an activity.
    """
    displays={};current=None;header_indent=0;task_indent=None;closed=False
    for line in text.splitlines():
        indent=len(line)-len(line.lstrip())
        header=re.match(r'^\s*Display\s+#(\d+)\b',line)
        if header:
            current=int(header.group(1));displays.setdefault(current,set())
            header_indent=indent;task_indent=None;closed=False
            continue
        if current is None or closed or not line.strip():continue
        body=line.strip()
        if indent<=header_indent:
            closed=True;continue
        # RootWindowContainer.dumpActivities switches to global state here.
        if re.match(r'(?:mFocusedRootTask|mLastFocusedRootTask|mTopResumedActivity|'
                    r'mTopFocusedDisplayId|mResumedActivities|mRootWindowContainer|'
                    r'mLastPausedActivity|mSleepTokens|mHomeProcess|mPending|'
                    r'mTaskSupervisor|mSupervisor|ACTIVITY MANAGER)\b',body):
            closed=True;continue
        if re.match(r'(?:\*\s*)?(?:RootTask\b|Task[ #{]|Stack[ #])',body):
            task_indent=indent if task_indent is None else min(task_indent,indent)
        summary=bool(re.match(r'Resumed\s*:',body))
        local_field=bool(re.match(r'(?:mResumedActivity|topResumedActivity)\s*[:=]',body))
        if local_field and indent<=(task_indent if task_indent is not None else header_indent+2):
            # This is a global record, even if it repeats the last display's game.
            closed=True;continue
        if summary or local_field:
            match=re.search(r'\bu\d+\s+([A-Za-z0-9_.]+)/',body)
            if match:displays[current].add(match.group(1))
    return displays


def parse_display_viewports(text):
    """Map logical display IDs to physical IDs from active local viewports."""
    mapping={}
    for block in re.findall(r'DisplayViewport\{([^{}]*)\}',text,re.S):
        if not re.search(r'\bisActive\s*=\s*true\b',block,re.I):continue
        if re.search(r'\bvalid\s*=\s*false\b',block,re.I):continue
        logical=re.search(r'\bdisplayId\s*=\s*(\d+)\b',block)
        physical=re.search(r"\buniqueId\s*=\s*['\"]?local:(\d+)\b",block)
        if logical and physical:
            mapping.setdefault(int(logical.group(1)),set()).add(physical.group(1))
    return mapping


def select_display(activities,preferred=None):
    if preferred:
        matches=[(display,preferred) for display,packages in activities.items() if preferred in packages]
    else:
        matches=[(display,package) for display,packages in activities.items() for package in packages
                 if not (package=='android' or package.startswith('com.android.')
                         or 'launcher' in package.lower() or package=='app.lawnchair')]
    if len(matches)>1:raise AdbError('目标画面不唯一：多个前台候选或同一游戏出现在多个画面，已暂停。请关闭多余画面后重试。')
    if not matches:raise AdbError('无法确定游戏所在画面，请让游戏在安卓画面中保持前台，再重新绑定。')
    display,package=matches[0]
    if activities[display]!={package}:
        raise AdbError('游戏画面的前台应用不唯一，已暂停。')
    return display,package


def decode_screenshot(data):
    signature=b'\x89PNG\r\n\x1a\n'
    offset=data.find(signature,0,4096+len(signature))
    if len(data)>32*1024*1024 or offset<0:
        preview=data[:180].decode('utf-8',errors='replace')
        raise AdbError(f'ADB 截图格式异常：收到{len(data)}字节，未找到有效PNG文件头；返回内容：{preview!r}')
    if offset:
        try:prefix=data[:offset].decode('utf-8')
        except UnicodeDecodeError:raise AdbError('PNG之前含非文本数据，已拒绝截图。') from None
        if any(not char.isprintable() and char not in '\r\n\t' for char in prefix):
            raise AdbError('PNG之前含异常控制字符，已拒绝截图。')
    payload=data[offset:]
    try:
        with Image.open(io.BytesIO(payload)) as im:
            if im.format!='PNG':raise ValueError('not PNG')
            im.verify()
        with Image.open(io.BytesIO(payload)) as im:
            im.load();return im.convert('RGB')
    except (OSError,ValueError,SyntaxError) as error:
        raise AdbError(f'无法解码ADB的PNG截图（{len(payload)}字节）：{error}') from error

class AdbClient:
    def __init__(self,path,serial=''):
        if not path or not Path(path).is_file():raise AdbError('请先选择本机 adb.exe。')
        self.path=str(Path(path).resolve());self.serial=serial

    def run(self,args,timeout=12,stop=None,device=True):
        if device and not self.serial:raise AdbError('请先选择 ADB 设备。')
        if stop is not None and stop.is_set():raise AdbError('任务已停止。')
        command=[self.path]+(['-s',self.serial] if device else [])+list(args)
        process=subprocess.Popen(command,stdout=subprocess.PIPE,stderr=subprocess.PIPE,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        deadline=time.monotonic()+timeout
        try:
            while True:
                if stop is not None and stop.is_set():raise AdbError('任务已停止。')
                remaining=deadline-time.monotonic()
                if remaining<=0:raise AdbError('ADB 操作超时，请检查模拟器连接。')
                try:
                    out,err=process.communicate(timeout=min(.2,remaining));break
                except subprocess.TimeoutExpired:continue
            if process.returncode:
                raise AdbError('ADB 操作失败：'+err.decode('utf-8',errors='replace')[-350:])
            return out
        finally:
            if process.poll() is None:
                process.terminate()
                try:process.communicate(timeout=2)
                except subprocess.TimeoutExpired:process.kill();process.communicate()

    def devices(self):return parse_devices(self.run(['devices','-l'],device=False).decode('utf-8',errors='replace'))

    def connect_local(self,address):
        if not re.fullmatch(r'(?:127\.0\.0\.1|localhost):\d{1,5}',address):
            raise AdbError('请填写本机地址，例如 127.0.0.1:16384。')
        port=int(address.rsplit(':',1)[1])
        if not 1<=port<=65535:raise AdbError('端口应在 1–65535 之间。')
        return self.run(['connect',address],device=False).decode('utf-8',errors='replace')

    def resolve_target(self,preferred=None,stop=None):
        text=self.run(['shell','dumpsys','activity','activities'],stop=stop).decode('utf-8',errors='replace')
        logical,package=select_display(parse_display_activities(text),preferred)
        display_text=self.run(['shell','dumpsys','display'],stop=stop).decode('utf-8',errors='replace')
        physical=parse_display_viewports(display_text).get(logical,set())
        if len(physical)!=1:raise AdbError(f'逻辑画面{logical}没有唯一的活动物理显示器映射，已暂停；不会回退到桌面截图。')
        return DisplayTarget(package,logical,next(iter(physical)))

    def screenshot(self,stop=None,physical_id=None):
        if physical_id is None or not re.fullmatch(r'\d+',str(physical_id)):
            raise AdbError('未确定截图物理显示器，已停止。请重新绑定游戏。')
        data=self.run(['exec-out','screencap','-p','-d',str(physical_id)],timeout=15,stop=stop)
        return decode_screenshot(data)

class Session:
    def __init__(self,client,profile,stop):
        self.client=client;self.profile=profile;self.stop=stop
        self.last_check=0;self.raw_size=None;self.target=None
        self.package=profile.get('package','')
        if not self.package:raise AdbError('请先绑定当前游戏。')

    def guard(self,force=False):
        if self.stop.is_set():raise AdbError('任务已停止。')
        if force or time.monotonic()-self.last_check>=2:
            current=self.client.resolve_target(preferred=self.package,stop=self.stop)
            if current.package!=self.package:
                raise AdbError('目标画面已切换到其他安卓应用，已暂停。')
            if self.target is not None and self.target!=current:
                raise AdbError('运行中游戏显示画面或物理映射发生变化，已暂停；请重新绑定并核对校准。')
            self.target=current
            self.last_check=time.monotonic()

    def raw_capture(self):
        self.guard(force=True)
        image=self.client.screenshot(self.stop,physical_id=self.target.physical_id)
        self.guard(force=True)
        saved=self.profile.get('screen_size')
        if saved and list(image.size)!=saved:raise AdbError('安卓截图尺寸变化，请重新连接并校准游戏区域。')
        self.raw_size=image.size
        return image

    def box(self):
        size=self.raw_size or self.profile.get('screen_size')
        if not size:raise AdbError('尚未获取截图尺寸。')
        w,h=size;rect=self.profile.get('region',[0,0,1,1])
        if len(rect)!=4 or any(not isinstance(v,(int,float)) or not math.isfinite(v) for v in rect):raise AdbError('游戏选区无效。')
        x0,y0,x1,y1=rect
        if not (0<=x0<x1<=1 and 0<=y0<y1<=1):raise AdbError('游戏选区超出安卓屏幕。')
        box=(round(x0*w),round(y0*h),round(x1*w),round(y1*h))
        if box[2]-box[0]<100 or box[3]-box[1]<180:raise AdbError('游戏区域太小，请重新校准。')
        return box

    def capture_game(self):return self.raw_capture().crop(self.box()).resize((994,1768))

    def point(self,name):
        point=self.profile.get('points',{}).get(name,DEFAULT_POINTS[name])
        if len(point)!=2 or any(not isinstance(v,(int,float)) or not math.isfinite(v) or not 0<=v<=1 for v in point):raise AdbError('点击位置无效：'+name)
        l,t,r,b=self.box()
        return (min(r-1,max(l,round(l+(r-l)*point[0]))),min(b-1,max(t,round(t+(b-t)*point[1]))))

    def tap(self,name):
        self.guard(force=True)
        x,y=self.point(name)
        self.client.run(['shell','input','-d',str(self.target.logical_id),'tap',str(x),str(y)],stop=self.stop)

    def swipe_catalog(self,down=True):
        self.guard(force=True)
        l,t,r,b=self.box();x=round(l+(r-l)*.8)
        ys=[.78,.22] if down else [.22,.78]
        y0,y1=[round(t+(b-t)*v) for v in ys]
        self.client.run(['shell','input','-d',str(self.target.logical_id),'swipe',str(x),str(y0),str(x),str(y1),'400'],stop=self.stop)
