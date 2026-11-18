from pathlib import Path
import sys,threading,unittest
from types import SimpleNamespace
from unittest.mock import Mock,patch
from PIL import Image
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import fishing_adb as fish
import adb_backend as adb

class TimingTests(unittest.TestCase):
    def test_future_or_expired_plan_is_not_fired(self):
        controller=Mock();stop=threading.Event()
        for plan in ({'action':'HIT','send_at':2,'valid_until':3},
                     {'action':'HIT','send_at':0,'valid_until':.5},{}):
            controller.phase.plan.return_value=plan
            self.assertFalse(fish.timing_ready(controller,stop,now=lambda:1.))

    def test_due_plan_and_cancel(self):
        controller=Mock();controller.phase.plan.return_value={'action':'HIT','send_at':1,'valid_until':1.2}
        stop=threading.Event()
        self.assertTrue(fish.timing_ready(controller,stop,now=lambda:1.))
        stop.set();self.assertFalse(fish.timing_ready(controller,stop,now=lambda:1.))

class AdbInputTests(unittest.TestCase):
    def session(self):
        client=Mock();client.resolve_target.return_value=adb.DisplayTarget('com.game',2,'100')
        return adb.Session(client,{'package':'com.game','screen_size':[1000,1800]},threading.Event()),client

    def test_deadline_checked_after_target_guard_and_skipped_without_input(self):
        session,client=self.session();checked=[]
        def expired():
            self.assertTrue(client.resolve_target.called);checked.append(True);return False
        self.assertFalse(session.tap('fish_action',before_input=expired))
        self.assertEqual(checked,[True]);client.run.assert_not_called()

    def test_due_fishing_touch_goes_to_selected_android_display(self):
        session,client=self.session()
        self.assertTrue(session.tap('fish_action',before_input=lambda:True))
        self.assertEqual(client.run.call_args.args[0],['shell','input','-d','2','tap','500','1557'])

class RunnerTests(unittest.TestCase):
    def run_mock(self,observe=False,sent=True,pause=False):
        stop=Mock();stop.is_set.return_value=False;stop.wait.return_value=True
        session=Mock();session.frame_time=1.;session.capture_game.return_value=Image.new('RGB',(336,598));session.tap.return_value=sent
        decision=SimpleNamespace(action='CAST',reason='下钩',pause=pause,send_at=None)
        controller=Mock();controller.step.return_value=decision
        detector=Mock();detector.detect.return_value=(SimpleNamespace(state='IDLE'),None)
        with patch.object(fish,'components',return_value=({},Mock(return_value=controller),Mock(return_value=detector),{'IDLE':'准备下钩'},np)),patch.object(fish.time,'perf_counter',return_value=1.):
            if pause and not observe:
                with self.assertRaises(RuntimeError):fish.run(session,stop,lambda *event:None,observe=observe)
            else:fish.run(session,stop,lambda *event:None,observe=observe)
        return session,controller

    def test_observe_does_not_send_android_or_windows_input(self):
        session,controller=self.run_mock(observe=True)
        session.tap.assert_not_called();controller.commit.assert_not_called()

    def test_successful_touch_only_is_committed(self):
        for sent in (True,False):
            session,controller=self.run_mock(sent=sent)
            session.tap.assert_called_once()
            self.assertEqual(controller.commit.call_count,1 if sent else 0)

    def test_pause_decision_prevents_touch(self):
        session,controller=self.run_mock(pause=True)
        session.tap.assert_not_called()
