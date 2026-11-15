from contextlib import ExitStack
from pathlib import Path
import queue
import sys
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import engine


class ProtectionTests(unittest.TestCase):
    def run_flow(self,frames,initial=100,save_error=None):
        runner=engine.Runner();runner.stop=threading.Event();runner.q=queue.Queue()
        catalog={'古代盛装':initial};clock=[0.];index=[-1];actions=[]
        def capture(_):index[0]+=1;return frames[index[0]]
        def observe(frame):return frame[0],[{'text':'test','treasure':frame[1]}]
        def identify(lines,_):return lines[0]['treasure']
        def send(_hwnd,key,_stop):
            actions.append((frames[index[0]][0],key))
            if index[0]==len(frames)-1:runner.stop.set()
            return not runner.stop.is_set()
        def wait(_hwnd,_stop,seconds):clock[0]+=seconds;return True
        with ExitStack() as patches:
            for name,replacement in [('capture',capture),('observe_game',observe),
                                     ('identify',identify),('send_key',send),('wait_game',wait)]:
                patches.enter_context(patch.object(engine,name,side_effect=replacement))
            patches.enter_context(patch.object(engine,'require_focus'))
            patches.enter_context(patch.object(engine,'read_chance',return_value=45))
            patches.enter_context(patch.object(engine,'read_floor',return_value=9))
            patches.enter_context(patch.object(engine,'read_multiplier',return_value=1))
            patches.enter_context(patch.object(engine.time,'monotonic',side_effect=lambda:clock[0]))
            save=patches.enter_context(patch.object(engine,'atomic_save',side_effect=save_error))
            if save_error:
                with self.assertRaisesRegex(OSError,'save failed'):
                    runner.run_game(None,catalog,20,60,True,False)
            else:runner.run_game(None,catalog,20,60,True,False)
        messages=list(runner.q.queue)
        return actions,catalog,save,messages

    def test_mid_round_enemy_uses_normal_threshold(self):
        actions,_,save,_=self.run_flow([('enemy',(None,None))]*2)
        self.assertEqual(actions,[('enemy','d')]);save.assert_not_called()

    def test_unknown_recovery_does_not_protect(self):
        actions,_,save,_=self.run_flow([('unknown',(None,None))]*2+[('enemy',(None,None))]*2)
        self.assertEqual(actions[-1],('enemy','d'));save.assert_not_called()

    def test_unreadable_treasure_does_not_protect(self):
        actions,_,save,_=self.run_flow([('treasure',(None,None))]*2+[('enemy',(None,None))]*2)
        self.assertEqual(actions[-1],('enemy','d'));save.assert_not_called()

    def test_lower_and_equal_records_do_not_protect(self):
        for value in (1521,2413):
            with self.subTest(value=value):
                actions,catalog,save,_=self.run_flow([('treasure',('古代盛装',value))]*2+[('enemy',(None,None))]*2,initial=2413)
                self.assertEqual(actions[-1],('enemy','d'));save.assert_not_called()
                self.assertEqual(catalog['古代盛装'],2413)

    def test_inconsistent_higher_record_does_not_protect(self):
        actions,_,save,_=self.run_flow([('treasure',('古代盛装',200)),('treasure',('古代盛装',300))]+[('enemy',(None,None))]*2)
        self.assertEqual(actions[-1],('enemy','d'));save.assert_not_called()

    def test_confirmed_saved_record_enables_protection(self):
        actions,catalog,save,messages=self.run_flow([('treasure',('古代盛装',200))]*2+[('enemy',(None,None))]*2)
        self.assertEqual(actions[-1],('enemy','a'));save.assert_called_once()
        self.assertEqual(catalog['古代盛装'],200)
        self.assertEqual(len([m for m in messages if m[0]=='catalog_update']),1)
        logs=[m[1] for m in messages if m[0]=='log']
        self.assertTrue(any('图鉴已自动保存' in log for log in logs))
        self.assertTrue(any('保护模式=True' in log for log in logs))

    def test_invalid_catalog_promotion_does_not_protect(self):
        actions,_,save,_=self.run_flow([('treasure',('不在图鉴的名称',999))]*2+[('enemy',(None,None))]*2)
        self.assertEqual(actions[-1],('enemy','d'));save.assert_not_called()

    def test_protection_stays_after_lower_unreadable_and_unknown(self):
        frames=([('treasure',('古代盛装',200))]*2+[('forward',(None,None))]*2
                +[('treasure',('古代盛装',50))]*2+[('forward',(None,None))]*2
                +[('treasure',(None,None))]*2+[('unknown',(None,None))]*2
                +[('enemy',(None,None))]*2)
        actions,_,save,_=self.run_flow(frames)
        self.assertEqual(actions[-1],('enemy','a'));save.assert_called_once()

    def test_new_round_resets_protection(self):
        frames=([('treasure',('古代盛装',200))]*2+[('enemy',(None,None))]*2
                +[('result',(None,None))]*2+[('home',(None,None))]*2
                +[('enemy',(None,None))]*2)
        actions,_,save,_=self.run_flow(frames)
        self.assertEqual([action for action in actions if action[0]=='enemy'],[('enemy','a'),('enemy','d')])
        save.assert_called_once()

    def test_save_failure_does_not_report_protection_or_change_catalog(self):
        _,catalog,_,messages=self.run_flow([('treasure',('古代盛装',200))]*2,save_error=OSError('save failed'))
        self.assertEqual(catalog['古代盛装'],100)
        self.assertFalse(any(key=='catalog_update' for key,_ in messages))
        self.assertFalse(any('本局开启保护模式' in str(value) for _,value in messages))


if __name__=='__main__':unittest.main()
