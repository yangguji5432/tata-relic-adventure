import copy
import ctypes
import queue
import threading
import time
import tkinter as tk
from tkinter import ttk,filedialog,messagebox
from PIL import ImageTk
import engine
import hunt
from pathlib import Path
from audio_alert import AudioPlayer
import fishing_adb
from adb_backend import AdbClient,Session,AdbError,find_adb,DEFAULT_POINTS

POINT_LABELS={'开始探险':'home','准备前进':'forward','宝藏展示继续':'treasure','增益收下':'buff','结算收下':'result','撤离':'retreat','战斗':'fight','打开图鉴':'catalog','未知画面尝试继续':'unknown'}
POINT_LABELS.update({'钓鱼：下钩/拉钩/黄条/收下':'fish_action','围猎：开始':'hunt_start','围猎：锁定':'hunt_lock','围猎：狂暴':'hunt_rage','围猎：退出':'hunt_exit'})

class App(engine.Runner):
    def __init__(self,root):
        self.root=root;root.title('塔塔冒险队活动助手');root.geometry('1000x980')
        self.q=queue.Queue();self.stop=threading.Event();self.worker=None;self.closed=threading.Event()
        self.settings=engine.load(engine.CONFIG,{})
        self.audio_player=AudioPlayer()
        self.hunt_audio_path=self.settings.get('hunt_audio_path','')
        self.profiles=self.settings.get('profiles',{})
        self.catalog=engine.fill_catalog_slots(engine.load(engine.CATALOG,engine.INITIAL_CATALOG.copy()))
        self.adb=tk.StringVar(value=self.settings.get('adb_path') or find_adb())
        self.serial=tk.StringVar(value=self.settings.get('serial',''))
        self.address=tk.StringVar(value='127.0.0.1:16384')
        self.normal=tk.StringVar(value=str(self.settings.get('normal',20)))
        self.protected=tk.StringVar(value=str(self.settings.get('protected',60)))
        self.continuous=tk.BooleanVar(value=self.settings.get('continuous',False));self.observe=tk.BooleanVar(value=True)
        self.status=tk.StringVar(value='三个活动均使用ADB后台；先绑定当前游戏并校准完整游戏区域。')
        self.binding=tk.StringVar(value='未连接或绑定。')
        panel=ttk.Frame(root,padding=10);panel.pack(fill='x')
        ttk.Label(panel,text='ADB 路径：').grid(row=0,column=0,sticky='w')
        ttk.Entry(panel,textvariable=self.adb,width=73).grid(row=0,column=1,columnspan=4,sticky='ew')
        ttk.Button(panel,text='选择 adb.exe',command=self.browse_adb).grid(row=0,column=5,padx=4)
        ttk.Label(panel,text='设备：').grid(row=1,column=0,sticky='w',pady=7)
        self.device_box=ttk.Combobox(panel,textvariable=self.serial,width=28,state='readonly');self.device_box.grid(row=1,column=1,sticky='w')
        self.device_box.bind('<<ComboboxSelected>>',self.device_changed)
        ttk.Button(panel,text='刷新设备',command=self.refresh).grid(row=1,column=2,padx=4)
        ttk.Entry(panel,textvariable=self.address,width=23).grid(row=1,column=3)
        ttk.Button(panel,text='连接此端口',command=self.connect).grid(row=1,column=4,padx=4)
        ttk.Button(panel,text='绑定当前游戏',command=self.bind_game).grid(row=1,column=5)
        ttk.Label(root,textvariable=self.binding,padding=(10,0),wraplength=880).pack(anchor='w')
        row=ttk.Frame(root,padding=(10,7));row.pack(fill='x')
        ttk.Button(row,text='查看后台画面',command=lambda:self.calibrate('preview')).pack(side='left',padx=3)
        ttk.Button(row,text='校准游戏区域',command=lambda:self.calibrate('region')).pack(side='left',padx=3)
        ttk.Button(row,text='校准点击位置',command=lambda:self.calibrate('points')).pack(side='left',padx=3)
        ttk.Label(row,text='三个活动均无需Windows焦点；安卓内部切换应用会暂停。').pack(side='left',padx=10)
        self.tabs=ttk.Notebook(root);self.tabs.pack(fill='both',expand=True,padx=10,pady=5)
        relic=ttk.Frame(self.tabs);hunt_page=ttk.Frame(self.tabs)
        self.tabs.add(relic,text='遗迹探险');self.tabs.add(hunt_page,text='围猎萨满（半自动建议）')
        self.fish_page=ttk.Frame(self.tabs);self.tabs.add(self.fish_page,text='钓鱼助手')
        ttk.Label(self.fish_page,text='钓鱼助手 · ADB后台版。参考并复用 zz11-m/tt_fish 源码（GPL-3.0-only），保留原作者署名。',wraplength=900,padding=10).pack(anchor='w')
        self.fish_observe=tk.BooleanVar(value=True)
        self.fish_continuous=tk.BooleanVar(value=self.settings.get('fish_continuous',True))
        self.fish_timing=tk.BooleanVar(value=self.settings.get('fish_auto_timing',True))
        self.fish_casts=tk.StringVar(value=str(self.settings.get('fish_max_casts',20)))
        self.fish_minutes=tk.StringVar(value=str(self.settings.get('fish_max_minutes',15)))
        self.fish_latency=tk.StringVar(value=str(self.settings.get('fish_input_latency',150)))
        options_fish=ttk.Frame(self.fish_page);options_fish.pack(fill='x',padx=10,pady=6)
        ttk.Checkbutton(options_fish,text='仅观察，不点击',variable=self.fish_observe).pack(side='left')
        ttk.Checkbutton(options_fish,text='不停钓',variable=self.fish_continuous).pack(side='left',padx=12)
        ttk.Checkbutton(options_fish,text='自动黄条判定（试用）',variable=self.fish_timing).pack(side='left',padx=12)
        limits_fish=ttk.Frame(self.fish_page);limits_fish.pack(fill='x',padx=10,pady=6)
        for label,value in (('下钩上限',self.fish_casts),('时间上限（分钟）',self.fish_minutes),('输入延迟补偿（毫秒，0—250）',self.fish_latency)):
            ttk.Label(limits_fish,text=label).pack(side='left',padx=5)
            ttk.Entry(limits_fish,textvariable=value,width=6).pack(side='left')
        buttons_fish=ttk.Frame(self.fish_page);buttons_fish.pack(fill='x',padx=10,pady=10)
        ttk.Button(buttons_fish,text='启动钓鱼（3秒后）',command=self.start_fishing).pack(side='left',padx=3)
        ttk.Button(buttons_fish,text='校准钓鱼点击位置',command=lambda:self.calibrate('points')).pack(side='left',padx=3)
        ttk.Button(buttons_fish,text='停止 F9',command=self.request_stop).pack(side='left',padx=3)
        ttk.Label(self.fish_page,text=('使用上方ADB选择实例、绑定游戏并预览完整竖屏画面，保留游戏内部底部栏。\n'
                  '在点击校准中选择“钓鱼：下钩/拉钩/黄条/收下”，选中各阶段共用的游戏按钮位置。无需模拟器空格映射。\n\n'
                  '默认持续观察识别。确认下钩、上钩、黄条和结算阶段正常后，再取消仅观察。\n'
                  '支持最小化或切换Windows窗口；安卓内部请保持游戏前台。各活动任务互斥。\n\n'
                  '黄条复用tt_fish相位预测，输入前重新检查ADB目标和点击时机；过期操作跳过。\n'
                  'ADB截图、传输与触摸延迟可能降低黄条命中率，尚未实机验证完整一轮；可关闭自动黄条手动操作。\n'
                  '不开“不停钓”时，次数和时间上限生效。F9停止后不再发送新的ADB输入。'),padding=12,wraplength=900,justify='left').pack(anchor='w')
        self.hunt_observe=tk.BooleanVar(value=True);self.hunt_rage=tk.BooleanVar(value=False)
        self.hunt_timeout=tk.StringVar(value='15');self.hunt_events=tk.BooleanVar(value=False)
        self.hunt_auto_next=tk.BooleanVar(value=self.settings.get('hunt_auto_next',True))
        self.hunt_sound=tk.BooleanVar(value=self.settings.get('hunt_sound',False))
        self.hunt_pause_low=tk.BooleanVar(value=self.settings.get('hunt_pause_low_stock',True))
        self.hunt_pause_unknown=tk.BooleanVar(value=self.settings.get('hunt_pause_repeated_unknown',False))
        ttk.Label(hunt_page,text='仅在200倍场等待萨满；出现头像后锁定。等待期间不攻击。',padding=10).pack(anchor='w')
        hunt_options=ttk.Frame(hunt_page);hunt_options.pack(fill='x',padx=10,pady=5)
        ttk.Checkbutton(hunt_options,text='仅观察，不点击',variable=self.hunt_observe).pack(side='left')
        ttk.Checkbutton(hunt_options,text='锁定后开启狂暴（默认关）',variable=self.hunt_rage).pack(side='left',padx=15)
        ttk.Label(hunt_options,text='单次攻击超时暂停（秒）').pack(side='left')
        ttk.Entry(hunt_options,textvariable=self.hunt_timeout,width=5).pack(side='left')
        ttk.Checkbutton(hunt_page,text='仅观察：持续测试萨满出现与死亡线索（不点击，优先于自动模式）',variable=self.hunt_events).pack(anchor='w',padx=10,pady=4)
        hunt_cycle=ttk.Frame(hunt_page);hunt_cycle.pack(fill='x',padx=10,pady=4)
        ttk.Checkbutton(hunt_cycle,text='自动进行下一轮',variable=self.hunt_auto_next).pack(side='left')
        ttk.Checkbutton(hunt_cycle,text='萨满出现时声音提醒',variable=self.hunt_sound).pack(side='left',padx=15)
        ttk.Button(hunt_cycle,text='试听警报',command=self.play_hunt_alert).pack(side='left')
        audio_row=ttk.Frame(hunt_page);audio_row.pack(fill='x',padx=10,pady=4)
        self.audio_label=tk.StringVar(value=Path(self.hunt_audio_path).name if self.hunt_audio_path else '默认短哔声')
        ttk.Button(audio_row,text='选择提示音文件',command=self.select_hunt_audio).pack(side='left')
        ttk.Button(audio_row,text='恢复默认声音',command=self.reset_hunt_audio).pack(side='left',padx=5)
        ttk.Label(audio_row,textvariable=self.audio_label,wraplength=570).pack(side='left',padx=5)
        ttk.Checkbutton(hunt_page,text='锁定不足3个自动暂停（关闭时跳过数量检查）',variable=self.hunt_pause_low).pack(anchor='w',padx=10,pady=4)
        hunt_buttons=ttk.Frame(hunt_page);hunt_buttons.pack(fill='x',padx=10,pady=8)
        ttk.Button(hunt_buttons,text='启动围猎（3秒后）',command=self.start_hunt).pack(side='left',padx=3)
        ttk.Button(hunt_buttons,text='校准识别区域',command=lambda:self.calibrate('hunt_regions')).pack(side='left',padx=3)
        ttk.Button(hunt_buttons,text='校准点击位置',command=lambda:self.calibrate('points')).pack(side='left',padx=3)
        ttk.Button(hunt_buttons,text='停止 F9',command=self.request_stop).pack(side='left',padx=3)
        ttk.Checkbutton(hunt_page,text='2分钟内3次未知退出后暂停（默认关）',variable=self.hunt_pause_unknown).pack(anchor='w',padx=10,pady=4)
        text=('推荐半自动：萨满出现提醒较有用，死亡和退出识别误判较多，请用户自行判断。\n\n'
              '方案一（推荐）：勾选持续观察和声音提醒，手动入场、锁定及退出。\n'
              '方案二：开启声音提醒，取消两种观察模式，程序按原规则自动放锁定；用户盯场判断死亡并及时退出。\n'
              '方案二仍保留原有自动退出/重进判断，并非仅释放锁定的独立模式。\n\n'
              '注意：萨满可能已被秒杀，程序仍会释放锁定；识别失败时不保证暂停，可能转打小怪并持续耗金币。\n'
              '请及时手动退出场地。F9/停止只停止助手，不会停止游戏已开启的自动攻击。\n\n'
              '提示音首帧匹配头像即异步播放，自动锁定确认规则保持原样。\n'
              '声音仍受ADB截图与识别耗时影响，请先点“试听警报”检查音量。')
        ttk.Label(hunt_page,text=text,padding=12,justify='left',wraplength=880).pack(anchor='w')
        options=ttk.Frame(relic,padding=(10,3));options.pack(fill='x')
        ttk.Label(options,text='普通撤离阈值 ≤').pack(side='left');ttk.Entry(options,textvariable=self.normal,width=5).pack(side='left')
        ttk.Label(options,text='%    保护纪录阈值 ≤').pack(side='left');ttk.Entry(options,textvariable=self.protected,width=5).pack(side='left')
        ttk.Label(options,text='%').pack(side='left')
        ttk.Checkbutton(options,text='结算后继续下一局',variable=self.continuous).pack(side='left',padx=12)
        ttk.Checkbutton(options,text='仅观察，不点击',variable=self.observe).pack(side='left',padx=8)
        buttons=ttk.Frame(relic,padding=(10,5));buttons.pack(fill='x')
        for text,fn in [('打开游戏图鉴',self.open_catalog),('扫描图鉴',self.scan),('保存图鉴',self.save_catalog),('恢复图鉴',self.restore_catalog),('导入图鉴',self.import_catalog),('启动（3秒后）',self.start),('停止 F9',self.request_stop)]:
            ttk.Button(buttons,text=text,command=fn).pack(side='left',padx=2)
        ttk.Label(root,textvariable=self.status,padding=10,wraplength=890,foreground='#075d90').pack(fill='x')
        ttk.Label(relic,text='图鉴共20项；首次全部为0。扫描可覆盖更低分；获得物品时仅自动保存更高分。',padding=(10,0)).pack(anchor='w')
        self.editor=tk.Text(relic,height=10,font=('Microsoft YaHei UI',10),undo=True);self.editor.pack(fill='both',expand=True,padx=10,pady=5)
        self.set_editor(self.catalog)
        ttk.Label(root,text='运行记录',padding=(10,0)).pack(anchor='w')
        self.logs=tk.Text(root,height=8,state='disabled',font=('Microsoft YaHei UI',9));self.logs.pack(fill='both',expand=True,padx=10,pady=(5,10))
        root.after(100,self.pump);root.protocol('WM_DELETE_WINDOW',self.close)
        threading.Thread(target=self.hotkey,daemon=True).start()
        if self.serial.get():self.device_box['values']=[self.serial.get()]
        self.update_binding()

    def busy(self):return self.worker is not None and self.worker.is_alive()
    def start_fishing(self):
        if not self.idle():return
        try:
            casts=int(self.fish_casts.get());minutes=float(self.fish_minutes.get());latency=float(self.fish_latency.get())
            if not 1<=casts<=10000 or not 1<=minutes<=1440 or not 0<=latency<=250:
                raise ValueError('下钩上限1—10000、分钟上限1—1440、输入补偿0—250毫秒。')
            session=self.get_session();fishing_adb.components()
        except Exception as error:messagebox.showerror('无法启动钓鱼',str(error));return
        observe=self.fish_observe.get();continuous=self.fish_continuous.get();timing=self.fish_timing.get()
        self.settings.update({'fish_max_casts':casts,'fish_max_minutes':minutes,'fish_input_latency':latency,
                              'fish_continuous':continuous,'fish_auto_timing':timing});self.save_settings()
        self.begin('钓鱼助手（ADB后台）',lambda:fishing_adb.run(session,self.stop,
                   lambda key,value:self.q.put((key,value)),observe,continuous,casts,minutes,timing,latency),delay=3)
    def idle(self):
        if self.busy():messagebox.showinfo('任务运行中','请先停止当前任务，结束后再修改连接或校准。');return False
        return True
    def get_client(self):return AdbClient(self.adb.get().strip(),self.serial.get().strip())
    def get_session(self):
        client=self.get_client();profile=copy.deepcopy(self.profiles.get(client.serial,{}))
        return Session(client,profile,self.stop)
    def save_settings(self):
        self.settings.update({'adb_path':self.adb.get().strip(),'serial':self.serial.get(),'profiles':self.profiles})
        engine.atomic_save(engine.CONFIG,self.settings)
    def update_binding(self):
        p=self.profiles.get(self.serial.get(),{})
        self.binding.set('绑定游戏：'+p['package']+'；安卓尺寸：'+str(p.get('screen_size'))+'；最近逻辑画面：'+str(p.get('last_logical_display','待重新绑定'))+'（运行时动态检查）' if p.get('package') else '当前设备尚未绑定游戏。')
    def browse_adb(self):
        if not self.idle():return
        path=filedialog.askopenfilename(title='选择已安装的 adb.exe',filetypes=[('ADB','adb.exe'),('EXE','*.exe')])
        if path:self.adb.set(path)
    def device_changed(self,event=None):
        if self.busy():self.stop.set()
        self.update_binding()
    def begin(self,label,work,delay=0):
        if not self.idle():return
        self.stop.clear();self.status.set(f'{label}：正在处理。无需切换到模拟器。')
        def runner():
            try:
                if delay and self.stop.wait(delay):return
                work()
            except Exception as e:
                self.q.put(('status','已停止。' if self.stop.is_set() else '已暂停：'+str(e)))
                self.q.put(('log',str(e)))
            finally:self.stop.set();self.q.put(('done',label))
        self.worker=threading.Thread(target=runner,daemon=True);self.worker.start()
    def refresh(self):
        try:client=self.get_client()
        except Exception as e:messagebox.showerror('ADB 路径',str(e));return
        self.begin('刷新设备',lambda:self.q.put(('devices',client.devices())))
    def connect(self):
        try:client=self.get_client();address=self.address.get().strip()
        except Exception as e:messagebox.showerror('连接失败',str(e));return
        def work():
            self.q.put(('log',client.connect_local(address)))
            self.q.put(('devices',client.devices()))
        self.begin('连接本机端口',work)
    def bind_game(self):
        try:client=self.get_client()
        except Exception as e:messagebox.showerror('连接失败',str(e));return
        old=copy.deepcopy(self.profiles.get(client.serial,{}))
        def work():
            target=client.resolve_target(preferred=old.get('package'),stop=self.stop)
            package=target.package
            if package.startswith('com.android.') or package in ('app.lawnchair','android'):
                raise AdbError('当前是安卓桌面或系统界面，请先在模拟器内打开游戏。')
            image=client.screenshot(self.stop,physical_id=target.physical_id)
            if client.resolve_target(preferred=package,stop=self.stop)!=target:
                raise AdbError('绑定过程中游戏画面发生变化，请重试。')
            profile=old if old.get('package')==package and old.get('screen_size')==list(image.size) else {}
            profile.update({'package':package,'screen_size':list(image.size),
                            'last_logical_display':target.logical_id,'last_physical_display':target.physical_id})
            profile.setdefault('region',[0,0,1,1]);profile.setdefault('points',{})
            self.q.put(('log',f'已确认游戏画面：逻辑编号={target.logical_id}，物理显示器ID={target.physical_id}，截图={image.width}×{image.height}。'))
            self.q.put(('bound',(client.serial,profile)))
        self.begin('绑定当前游戏',work)
    def calibrate(self,mode):
        try:session=self.get_session()
        except Exception as e:messagebox.showerror('未连接',str(e));return
        serial=self.serial.get()
        def work():
            session.guard(force=True);image=session.raw_capture()
            if mode!='region':image=image.crop(session.box())
            self.q.put(('calibration',(mode,image,serial,copy.deepcopy(session.profile))))
        self.begin('读取后台画面',work)

    def show_calibration(self,mode,image,serial,profile):
        dialog=tk.Toplevel(self.root);dialog.title({'region':'选择安卓画面中的游戏区域','points':'点击位置校准','preview':'后台截图预览','hunt_regions':'围猎文字识别区域'}[mode])
        scale=min(1,800/image.width,max(350,self.root.winfo_screenheight()-230)/image.height)
        w,h=round(image.width*scale),round(image.height*scale)
        ttk.Label(dialog,text='截图来自安卓内部；不会占用 Windows 鼠标。区域拖动框选；点击位置选择动作后单击截图。',padding=6).pack()
        point_choice=tk.StringVar(value='准备前进')
        hint=tk.StringVar(value='请选择区域。' if mode=='region' else '单击只记录位置，不会在游戏里点击。')
        if mode=='points':
            ttk.Combobox(dialog,textvariable=point_choice,values=list(POINT_LABELS),state='readonly',width=25).pack()
        region_labels={'个人分数（只框顶部数字）':'score','锁定数量（只框x数字）':'stock','主页200倍场':'lobby_multiplier','场内200倍场':'arena_multiplier'}
        region_choice=tk.StringVar(value=next(iter(region_labels)))
        regions=copy.deepcopy(profile.get('hunt_regions',{}))
        if mode=='hunt_regions':
            ttk.Combobox(dialog,textvariable=region_choice,values=list(region_labels),state='readonly',width=32).pack()
            hint.set('选择一个区域名称，然后拖动框选；可连续设置多个区域，最后保存。')
        canvas=tk.Canvas(dialog,width=w,height=h,highlightthickness=0);canvas.pack()
        photo=ImageTk.PhotoImage(image.resize((w,h)));canvas.create_image(0,0,image=photo,anchor='nw');canvas.image=photo
        selected={'start':None,'rect':None,'shape':None};points=copy.deepcopy(profile.get('points',{}))
        def press(e):
            x=max(0,min(w,e.x));y=max(0,min(h,e.y))
            if mode in ('region','hunt_regions'):selected['start']=(x,y)
            elif mode=='points':
                key=POINT_LABELS[point_choice.get()];points[key]=[x/w,y/h]
                canvas.delete('target');canvas.create_oval(x-5,y-5,x+5,y+5,outline='red',width=2,tags='target')
                hint.set(f'已记录 {point_choice.get()}：{x/w:.3f}, {y/h:.3f}。可切换动作继续记录。')
        def drag(e):
            if mode not in ('region','hunt_regions') or selected['start'] is None:return
            x,y=selected['start'];xx=max(0,min(w,e.x));yy=max(0,min(h,e.y))
            selected['rect']=[min(x,xx)/w,min(y,yy)/h,max(x,xx)/w,max(y,yy)/h]
            if selected['shape']:canvas.delete(selected['shape'])
            selected['shape']=canvas.create_rectangle(min(x,xx),min(y,yy),max(x,xx),max(y,yy),outline='red',width=2)
            if mode=='hunt_regions' and xx!=x and yy!=y:
                regions[region_labels[region_choice.get()]]=selected['rect']
                hint.set('已记录 '+region_choice.get()+'；可切换区域继续设置。')
        def save(full=False):
            if mode=='region':
                rect=[0,0,1,1] if full else selected['rect']
                if not rect or rect[2]-rect[0]<.1 or rect[3]-rect[1]<.1:
                    messagebox.showerror('选区无效','请选择完整竖屏游戏区域。',parent=dialog);return
                ratio=(rect[2]-rect[0])*image.width/((rect[3]-rect[1])*image.height)
                if not .48<ratio<.66:
                    messagebox.showerror('选区比例异常','请选择完整竖屏游戏区域。',parent=dialog);return
                profile['region']=rect
                # A changed crop changes normalized point meaning.
                profile['points']={}
                profile['hunt_regions']={}
            elif mode=='points':profile['points']=points
            elif mode=='hunt_regions':profile['hunt_regions']=regions
            self.profiles[serial]=profile;self.save_settings();self.update_binding()
            self.status.set('校准已保存；改变游戏区域后请重新核对各动作的点击位置。');dialog.destroy()
        canvas.bind('<ButtonPress-1>',press);canvas.bind('<B1-Motion>',drag);canvas.bind('<ButtonRelease-1>',drag)
        ttk.Label(dialog,textvariable=hint,padding=5,wraplength=760).pack()
        if mode=='region':ttk.Button(dialog,text='使用完整安卓画面',command=lambda:save(True)).pack(side='left',padx=8,pady=5)
        if mode!='preview':ttk.Button(dialog,text='保存校准',command=save).pack(side='left',padx=8,pady=5)
        ttk.Button(dialog,text='关闭',command=dialog.destroy).pack(side='right',padx=8,pady=5)
        dialog.transient(self.root);dialog.grab_set();dialog.bind('<Escape>',lambda e:dialog.destroy())

    def set_editor(self,data):self.editor.delete('1.0','end');self.editor.insert('end',engine.format_catalog(data))
    def parse_editor(self):return engine.parse_catalog_text(self.editor.get('1.0','end'))
    def save_catalog(self):
        if not self.idle():return
        try:self.catalog=engine.ordered_catalog(self.parse_editor());engine.atomic_save(engine.CATALOG,self.catalog);self.set_editor(self.catalog);self.status.set('图鉴已保存。')
        except Exception as e:messagebox.showerror('保存失败',str(e))
    def restore_catalog(self):
        if not self.idle():return
        try:data=engine.fill_catalog_slots(engine.load(engine.CATALOG,engine.INITIAL_CATALOG.copy()));engine.parse_catalog_text(engine.format_catalog(data))
        except Exception:data=engine.INITIAL_CATALOG.copy()
        self.set_editor(data);self.status.set('已恢复图鉴，请核对后保存。')
    def import_catalog(self):
        if not self.idle():return
        path=filedialog.askopenfilename(title='选择前台版或其他设备保存的 catalog.json',filetypes=[('图鉴 JSON','*.json')])
        if not path:return
        try:
            data=engine.fill_catalog_slots(engine.load(__import__('pathlib').Path(path),{}))
            data=engine.parse_catalog_text(engine.format_catalog(data));self.set_editor(data)
            self.status.set('图鉴已导入到编辑区，请核对后保存。')
        except Exception as e:messagebox.showerror('导入失败',str(e))
    def run_with_session(self,label,fn,delay=0):
        try:session=self.get_session()
        except Exception as e:messagebox.showerror('无法运行',str(e));return
        def work():
            session.guard(force=True);session.raw_capture();engine.SESSION=session
            fn()
        self.begin(label,work,delay)
    def open_catalog(self):
        def work():
            engine.send_key(None,'c',self.stop)
            self.q.put(('status','已后台点击打开图鉴；请确认图鉴并滚到顶部，再扫描。'))
        self.run_with_session('打开图鉴',work)
    def scan(self):
        try:
            content=self.editor.get('1.0','end')
            if not any(line.strip() and not line.lstrip().startswith('#') for line in content.splitlines()):self.restore_catalog()
            baseline=self.parse_editor()
        except Exception as e:messagebox.showerror('图鉴错误',str(e));return
        def work():
            result=engine.scan_catalog_pages(None,baseline,self.stop,lambda text:self.q.put(('log',text)))
            if result is not None:self.q.put(('scan',result[0]));self.q.put(('status',f'两页扫描结束，有 {len(result[1])} 条待核对提示；核对后保存。'))
        self.run_with_session('扫描图鉴',work)
    def start(self):
        try:
            engine.visual._load();a=int(self.normal.get());b=int(self.protected.get());catalog=self.parse_editor()
            if not 0<=a<=100 or not 0<=b<=100:raise ValueError('阈值须为0–100的整数。')
            if catalog!=engine.load(engine.CATALOG,{}):raise ValueError('请先保存核对后的图鉴。')
            self.settings.update({'normal':a,'protected':b,'continuous':self.continuous.get()});self.save_settings()
        except Exception as e:messagebox.showerror('无法启动',str(e));return
        continuous=self.continuous.get();observe=self.observe.get()
        self.run_with_session('3秒后开始后台运行',lambda:self.run_game(None,catalog,a,b,continuous,observe),delay=3)
    def request_stop(self):
        self.stop.set();self.status.set('正在停止；围猎已锁定时，请手动退出场地停止游戏自动攻击。')
    def select_hunt_audio(self):
        path=filedialog.askopenfilename(title='选择萨满提示音',filetypes=[('音频文件','*.wav *.mp3 *.wma *.m4a *.aac *.mid *.midi'),('所有文件','*.*')])
        if not path:return
        try:
            path=self.audio_player.validate(path)
            self.hunt_audio_path=path;self.settings['hunt_audio_path']=path
            self.audio_label.set(Path(path).name);self.save_settings()
        except Exception as error:messagebox.showerror('音频文件不可用',str(error))
    def reset_hunt_audio(self):
        self.audio_player.stop();self.hunt_audio_path='';self.settings['hunt_audio_path']=''
        self.audio_label.set('默认短哔声');self.save_settings()
    def play_hunt_alert(self):
        path=self.hunt_audio_path
        def play():
            try:self.audio_player.play(path)
            except Exception as error:self.q.put(('log','声音提醒未播放：'+str(error)))
        worker=threading.Thread(target=play,daemon=True);worker.start()
        return worker

    def emit_hunt(self,key,value):
        # Sound runs directly on the recognition worker using asynchronous audio.
        # It does not wait behind UI messages; all widget changes stay on the UI queue.
        if key=='sound':self.play_hunt_alert()
        else:self.q.put((key,value))

    def start_hunt(self):
        if not self.idle():return
        try:
            timeout=float(self.hunt_timeout.get())
            if not 3<=timeout<=120:raise ValueError('攻击超时须为3到120秒。')
            session=self.get_session();vision=hunt.Vision(session.profile.get('hunt_regions'))
        except Exception as error:messagebox.showerror('无法启动围猎',str(error));return
        observe=self.hunt_observe.get();rage=self.hunt_rage.get();events=self.hunt_events.get()
        auto_next=self.hunt_auto_next.get();sound=self.hunt_sound.get();pause_low=self.hunt_pause_low.get();pause_unknown=self.hunt_pause_unknown.get()
        self.settings.update({'hunt_auto_next':auto_next,'hunt_sound':sound,'hunt_pause_low_stock':pause_low,'hunt_pause_repeated_unknown':pause_unknown});self.save_settings()
        self.begin('围猎萨满',lambda:hunt.run(session,self.stop,self.emit_hunt,
                   observe,rage,timeout,session.profile.get('hunt_regions'),observe_events=events,auto_next=auto_next,sound_alert=sound,pause_low_stock=pause_low,pause_repeated_unknown=pause_unknown),delay=3)
    def hotkey(self):
        try:query=ctypes.windll.user32.GetAsyncKeyState
        except AttributeError:return
        prev=False
        while not self.closed.is_set():
            down=bool(query(0x78)&0x8000)
            if down and not prev:self.stop.set();self.q.put(('status','已请求 F9 停止；围猎已锁定时，请手动退出场地停止游戏自动攻击。'))
            prev=down;self.closed.wait(.08)
    def pump(self):
        try:
            while True:
                key,value=self.q.get_nowait()
                if key=='status':self.status.set(value)
                elif key=='log':
                    self.logs.configure(state='normal');self.logs.insert('end',time.strftime('%H:%M:%S ')+value+'\n');self.logs.see('end');self.logs.configure(state='disabled')
                elif key=='devices':
                    self.device_box['values']=value
                    if self.serial.get() not in value:self.serial.set(next((s for s in value if s=='127.0.0.1:16384'),value[0] if value else ''))
                    self.update_binding();self.status.set(f'发现 {len(value)} 个 ADB 连接，请选定一个实例。多个地址可能指向同一个模拟器。')
                elif key=='bound':
                    serial,profile=value;self.profiles[serial]=profile;self.save_settings();self.update_binding();self.status.set('游戏绑定成功；先查看后台画面并核对区域、点击位置。')
                elif key=='calibration':self.show_calibration(*value)
                elif key=='sound':self.play_hunt_alert()
                elif key=='notify':messagebox.showwarning('围猎已暂停',value)
                elif key in ('scan','catalog_update'):
                    if key=='catalog_update':self.catalog=dict(value)
                    self.set_editor(value)
                elif key=='done' and self.status.get().startswith('正在停止'):self.status.set('已停止。')
        except queue.Empty:pass
        if not self.closed.is_set():self.root.after(100,self.pump)
    def close(self):
        self.stop.set();self.closed.set()
        self.audio_player.stop();self.root.destroy()

if __name__=='__main__':
    try:ctypes.windll.user32.SetProcessDPIAware()
    except (AttributeError,OSError):pass
    root=tk.Tk();App(root);root.mainloop()
