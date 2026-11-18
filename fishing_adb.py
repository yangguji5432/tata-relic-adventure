"""GPL-3.0-only. tt_fish detector/controller reused with guarded Android ADB touches.
Source: https://github.com/zz11-m/tt_fish, zz11-m and contributors.
This adapter replaces foreground screenshot/space input; no Windows game input.
"""
import time
from pathlib import Path
import sys

SOURCE=Path(__file__).resolve().parent/'third_party'/'tt_fish'

def components():
    if str(SOURCE) not in sys.path:sys.path.insert(0,str(SOURCE))
    from fisher.config import DEFAULTS
    from fisher.controller import Controller
    from fisher.vision import Detector,LABELS
    import numpy as np
    return DEFAULTS,Controller,Detector,LABELS,np

def timing_ready(controller,stop,now=time.perf_counter):
    # Re-plan AFTER the Android package/display guard; never fire a stale HIT.
    plan=controller.phase.plan(now())
    if plan.get('action')!='HIT':return False
    delay=max(0,plan.get('send_at',now())-now())
    if delay>.1:return False
    if delay and stop.wait(delay):return False
    if stop.is_set():return False
    current=now()
    deadline=plan.get('valid_until')
    if deadline is not None and current>deadline:return False
    check=controller.phase.plan(current)
    return check.get('action')=='HIT' and check.get('send_at',current)<=current+.003

def run(session,stop,emit,observe=True,continuous=True,max_casts=20,max_minutes=15,
        auto_timing=True,input_latency=150,interval=.1):
    defaults,Controller,Detector,labels,np=components()
    cfg=dict(defaults,continuous_fishing=continuous,max_casts=max_casts,
             max_session_minutes=max_minutes,input_latency_ms=input_latency,
             adaptive_latency=False,calibrate_speed=False,timing_mode='phase')
    detector=Detector(SOURCE/'assets/templates');controller=Controller(cfg)
    controller.reset(time.perf_counter());previous=None;last_report=0;durations=[]
    while not stop.is_set():
        begin=time.perf_counter()
        image=session.capture_game()
        frame_time=getattr(session,'frame_time',begin)
        observation,_=detector.detect(np.array(image.convert('RGB'))[:,:,::-1].copy())
        now=time.perf_counter();durations.append(now-begin)
        if now-last_report>=10:
            emit('log',f'钓鱼ADB截图+识别平均耗时：{sum(durations)/len(durations):.3f}秒/帧。')
            last_report=now;durations=[]
        decision=controller.step(observation,frame_time,now)
        reason=f'{labels.get(observation.state,observation.state)}：{decision.reason}'
        if observe:reason='仅观察，不点击；'+reason
        if reason!=previous:
            emit('status',reason);emit('log',reason);previous=reason
        if not observe:
            if decision.pause:raise RuntimeError('钓鱼助手：'+decision.reason)
            if decision.action:
                if decision.action=='HIT' and not auto_timing:
                    emit('status','黄条判定由用户手动操作；未点击。')
                else:
                    action=decision.action
                    if action=='HIT':
                        # Distant plans are observed again rather than blocking on old frames.
                        if decision.send_at is not None and decision.send_at-now>.25:
                            if stop.wait(min(interval,.1)):return
                            continue
                        check=lambda:timing_ready(controller,stop)
                    else:
                        # Re-check frame freshness after the package/display guard as well.
                        check=lambda: not stop.is_set() and time.perf_counter()-frame_time<=2
                    sent=session.tap('fish_action',before_input=check)
                    if sent:
                        controller.commit(action,time.perf_counter())
                        emit('log','ADB操作：'+action+'（安卓触摸）')
                    else:emit('log','已跳过过期/未到期钓鱼操作，等待新截图。')
        if stop.wait(max(0,interval-(time.perf_counter()-begin))):return
