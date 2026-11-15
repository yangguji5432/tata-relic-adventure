from pathlib import Path
import queue
import sys
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import engine


class UnknownRetryTests(unittest.TestCase):
    def run_unknown(self,observe=False,stop_after=None,capture_cost=0):
        runner=engine.Runner();runner.stop=threading.Event();runner.q=queue.Queue()
        clock=[0.0];clicks=[]
        def wait(_hwnd,_stop,seconds):clock[0]+=seconds;return True
        def capture(_hwnd):clock[0]+=capture_cost;return None
        def send(_hwnd,_key,_stop):
            clicks.append(clock[0])
            if stop_after and len(clicks)>=stop_after:runner.stop.set()
            return not runner.stop.is_set()
        with patch.object(engine.time,'monotonic',side_effect=lambda:clock[0]), \
             patch.object(engine,'capture',side_effect=capture), \
             patch.object(engine,'observe_game',return_value=('unknown',[])), \
             patch.object(engine,'require_focus'), \
             patch.object(engine,'wait_game',side_effect=wait), \
             patch.object(engine,'send_key',side_effect=send):
            if observe or stop_after:
                runner.run_game(None,{},20,60,False,observe)
            else:
                with self.assertRaisesRegex(RuntimeError,'1 分钟'):
                    runner.run_game(None,{},20,60,False,observe)
        return clicks,clock[0]

    def test_fast_then_slow_and_total_deadline(self):
        clicks,finished=self.run_unknown()
        self.assertEqual(clicks,list(range(1,21))+list(range(25,60,5)))
        self.assertEqual(finished,60)
        self.assertFalse(any(value>=60 for value in clicks))

    def test_observation_never_clicks(self):
        clicks,_=self.run_unknown(observe=True)
        self.assertEqual(clicks,[])

    def test_stop_prevents_further_clicks(self):
        clicks,finished=self.run_unknown(stop_after=1)
        self.assertEqual(clicks,[1])
        self.assertEqual(finished,1)

    def test_slow_ocr_does_not_catch_up_or_click_past_deadline(self):
        clicks,finished=self.run_unknown(capture_cost=4)
        self.assertTrue(clicks)
        self.assertTrue(all(b-a>=1 for a,b in zip(clicks,clicks[1:])))
        self.assertTrue(all(value<64 for value in clicks)) # unknown starts at t=4
        self.assertGreaterEqual(finished,64)
        for a,b in zip(clicks,clicks[1:]):
            if a-4>=20:self.assertGreaterEqual(b-a,5)


if __name__=='__main__':unittest.main()
