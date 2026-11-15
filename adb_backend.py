"""ADB transport: all game screenshots and touches stay inside one device."""
import io
import math
from pathlib import Path
import re
import subprocess
import time
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

    def foreground(self,stop=None):
        text=self.run(['shell','dumpsys','activity','activities'],stop=stop).decode('utf-8',errors='replace')
        package=parse_foreground(text)
        if not package:
            text=self.run(['shell','dumpsys','window','windows'],stop=stop).decode('utf-8',errors='replace')
            package=parse_foreground(text)
        if not package:raise AdbError('无法确认安卓内部前台应用，已停止操作。')
        return package

    def screenshot(self,stop=None):
        data=self.run(['exec-out','screencap','-p'],timeout=15,stop=stop)
        if not data.startswith(b'\x89PNG\r\n\x1a\n') or len(data)>32*1024*1024:
            raise AdbError('ADB 截图格式异常，未进行操作。')
        try:
            with Image.open(io.BytesIO(data)) as im:
                im.load();return im.convert('RGB')
        except (OSError,ValueError) as error:raise AdbError('无法解码 ADB 截图。') from error

class Session:
    def __init__(self,client,profile,stop):
        self.client=client;self.profile=profile;self.stop=stop
        self.last_check=0;self.raw_size=None
        self.package=profile.get('package','')
        if not self.package:raise AdbError('请先绑定当前游戏。')

    def guard(self,force=False):
        if self.stop.is_set():raise AdbError('任务已停止。')
        if force or time.monotonic()-self.last_check>=2:
            current=self.client.foreground(self.stop)
            if current!=self.package:raise AdbError('安卓内部已切换应用，已暂停；请回到绑定的游戏后重新启动。')
            self.last_check=time.monotonic()

    def raw_capture(self):
        self.guard()
        image=self.client.screenshot(self.stop)
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
        self.client.run(['shell','input','tap',str(x),str(y)],stop=self.stop)

    def swipe_catalog(self,down=True):
        self.guard(force=True)
        l,t,r,b=self.box();x=round(l+(r-l)*.8)
        ys=[.78,.22] if down else [.22,.78]
        y0,y1=[round(t+(b-t)*v) for v in ys]
        self.client.run(['shell','input','swipe',str(x),str(y0),str(x),str(y1),'400'],stop=self.stop)
