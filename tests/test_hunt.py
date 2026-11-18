from pathlib import Path
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from hunt import Policy,Observation,Vision,run,ObserveTracker,AppearanceAlarm
from unittest.mock import patch,Mock
from PIL import Image


def frame(**kwargs):
    values=dict(scene='arena',multiplier=200,stock=4,score=b'old')
    values.update(kwargs);return Observation(**values)


class HuntTests(unittest.TestCase):
    def locked(self,stock=4,rage=False):
        p=Policy(rage=rage)
        self.assertEqual(p.step(frame(stock=stock,bosses=(0,)),0)[0],'wait')
        self.assertEqual(p.step(frame(stock=stock,bosses=(0,)),.2)[0],'lock')
        return p

    def test_only_200_can_start(self):
        for value in (1,20,100,None):
            self.assertEqual(Policy().step(Observation(scene='lobby',multiplier=value),0)[0],'pause')
        p=Policy();self.assertEqual(p.step(Observation(scene='lobby',multiplier=200),0)[0],'start')
        self.assertEqual(p.step(Observation(scene='lobby',multiplier=200),.2)[0],'wait')
        self.assertEqual(p.step(Observation(scene='lobby',multiplier=200),5)[0],'pause')

    def test_wrong_arena_exits_and_pauses(self):
        p=Policy();self.assertEqual(p.step(frame(multiplier=1),0)[0],'exit')
        self.assertEqual(p.step(Observation(scene='lobby'),1)[0],'pause')

    def test_stock_below_three_before_lock(self):
        for stock in (0,1,2,None):
            p=Policy();self.assertEqual(p.step(frame(stock=stock),0)[0],'exit')
            self.assertEqual(p.step(Observation(scene='lobby'),1)[0],'pause')

    def test_three_consumed_to_two_does_not_interrupt_current_target(self):
        p=self.locked(3)
        self.assertEqual(p.stock,2)
        self.assertEqual(p.step(frame(stock=None,bosses=(0,)),.4)[0],'wait')
        self.assertEqual(p.step(frame(stock=None,score=b'new'),.6)[0],'wait')
        self.assertEqual(p.step(frame(stock=None,score=b'new'),.8)[0],'exit')
        self.assertEqual(p.step(Observation(scene='lobby'),1)[0],'pause')
        self.assertIn('1倍场',p.reason)

    def test_normal_exit_rechecks_200_before_next_round(self):
        p=self.locked(4)
        p.step(frame(stock=None,score=b'new'),.4)
        self.assertEqual(p.step(frame(stock=None,score=b'new'),.6)[0],'exit')
        self.assertEqual(p.step(Observation(scene='lobby',multiplier=200),1)[0],'wait')
        self.assertEqual(p.step(Observation(scene='lobby',multiplier=200),1.2)[0],'start')

    def test_offscreen_death_exits_after_two_seconds(self):
        p=self.locked()
        self.assertEqual(p.step(frame(stock=None),.3)[0],'wait')
        self.assertEqual(p.step(frame(stock=None),2.29)[0],'wait')
        self.assertEqual(p.step(frame(stock=None),2.3)[0],'exit')

    def test_late_gradual_marker_in_same_quadrant_is_allowed(self):
        p=self.locked()
        p.step(frame(stock=None),.4)
        self.assertEqual(p.step(frame(stock=None,marker=(.1,.3)),1.6)[0],'wait')
        self.assertEqual(p.step(frame(stock=None,marker=(.12,.32)),2.5)[0],'wait')

    def test_marker_other_quadrant_is_confirmed_then_exit(self):
        p=self.locked()
        self.assertEqual(p.step(frame(stock=None,marker=(.8,.7)),.5)[0],'wait')
        self.assertEqual(p.step(frame(stock=None,marker=(.81,.7)),.7)[0],'exit')

    def test_teleport_within_window_exits(self):
        p=self.locked();p.step(frame(stock=None,marker=(.05,.1)),.4)
        self.assertEqual(p.step(frame(stock=None,marker=(.4,.45)),.6)[0],'exit')

    def test_first_visible_marker_is_not_teleport(self):
        p=self.locked()
        self.assertEqual(p.step(frame(stock=None,marker=(.4,.4)),.5)[0],'wait')

    def test_quadrant_rule_only_during_first_two_seconds_after_avatar(self):
        p=self.locked();p.step(frame(stock=None,marker=(.1,.2)),.4)
        p.step(frame(stock=None,marker=(.2,.3)),1)
        p.step(frame(stock=None,marker=(.4,.4)),2)
        self.assertEqual(p.step(frame(stock=None,marker=(.6,.4)),2.6)[0],'wait')

    def test_rage_optional_and_once(self):
        p=self.locked(rage=True)
        self.assertEqual(p.step(frame(stock=None,bosses=(0,)),.4)[0],'rage')
        self.assertEqual(p.step(frame(stock=None,bosses=(0,)),.6)[0],'wait')
        p=self.locked(rage=False)
        self.assertEqual(p.step(frame(stock=None,bosses=(0,)),.4)[0],'wait')

    def test_ambiguous_avatars_and_unstable_score_do_not_lock(self):
        p=Policy()
        for i in range(4):self.assertEqual(p.step(frame(bosses=(0,1)),i*.2)[0],'wait')
        self.assertEqual(p.step(frame(bosses=(0,),score=b'one'),1)[0],'wait')
        self.assertEqual(p.step(frame(bosses=(0,),score=b'two'),1.2)[0],'wait')
        self.assertEqual(p.step(frame(bosses=(0,),score=None),1.4)[0],'wait')

    def test_attack_timeout_exits_and_pauses(self):
        p=self.locked()
        self.assertEqual(p.step(frame(stock=None,bosses=(0,)),15.3)[0],'exit')
        self.assertEqual(p.step(Observation(scene='lobby'),16)[0],'pause')

    def test_score_single_change_is_not_enough(self):
        p=self.locked();self.assertEqual(p.step(frame(stock=None,score=b'one'),.4)[0],'wait')
        self.assertEqual(p.step(frame(stock=None,score=b'two'),.6)[0],'wait')


class EntryRecognitionTests(unittest.TestCase):
    def test_multiplier_keeps_exact_number(self):
        for value in (1,20,100,200):
            self.assertEqual(Vision.parse_multiplier([{'text':f'{value} 倍 场'}]),value)
        self.assertEqual(Vision.parse_multiplier([{'text':'200倍，场'}]),200)
        self.assertIsNone(Vision.parse_multiplier([{'text':'200'}]))
        self.assertIsNone(Vision.parse_multiplier([{'text':'200倍场 100倍场'}]))

    def test_stock_accepts_icon_fragment_but_not_random_digits(self):
        for text in ('〗 x7','、 ×7','x 7','7'):
            self.assertEqual(Vision.parse_stock([{'text':text}]),7)
        for text in ('锁定7','x7x2','7个道具',''):
            self.assertIsNone(Vision.parse_stock([{'text':text}]))
        self.assertIsNone(Vision.parse_stock([{'text':'x7'},{'text':'x2'}]))

    def test_two_agreeing_reads_required_and_conflicts_rejected(self):
        v=Vision();crop=Image.new('RGB',(80,30))
        def lines(text):return [{'text':text}] if text else []
        for texts,expected in ((('x7','x7','','',''),7),
                               (('x7','','','',''),None),
                               (('x7','x7','x2','',''),None)):
            with patch('hunt.engine.ocr',side_effect=[lines(t) for t in texts]):
                self.assertEqual(v.read_consensus([crop],v.parse_stock),expected)

    def test_preprocessing_preserves_aspect_ratio(self):
        source=Image.new('RGB',(160,48))
        for variant in Vision.ocr_variants(source,True):
            self.assertAlmostEqual((variant.width-60)/(variant.height-60),160/48,places=2)


class ShamanPortraitTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.vision=Vision()
        cls.notice=Image.open(Path(__file__).parent/'fixtures/shaman_notice.png').convert('RGB')

    def arena(self):
        image=Image.new('RGB',(500,900),(45,42,60))
        assets=Path(__file__).resolve().parents[1]/'assets/hunt'
        image.paste(Image.open(assets/'lock.png'),(424,728))
        image.paste(Image.open(assets/'exit.png'),(232,850))
        return image

    def test_supplied_portrait_is_recognized(self):
        v=self.vision;im=self.notice
        array=v.np.array(im)[:,:,::-1].copy()
        point,score=v.match(array,'shaman',(0,0,im.width,im.height),range(28,95,2),.84)
        self.assertIsNotNone(point)
        self.assertGreaterEqual(score,.84)

    def test_real_portrait_in_each_quadrant_and_single_lock(self):
        for quadrant,pos in enumerate(((0,170),(330,170),(0,640),(330,550))):
            image=self.arena();image.paste(self.notice,pos)
            o,_=self.vision.frame(image)
            self.assertEqual(o.scene,'arena')
            self.assertEqual(o.bosses,(quadrant,))
            o.multiplier=200;o.stock=7;o.score=b'stable'
            policy=Policy()
            self.assertEqual(policy.step(o,0)[0],'wait')
            self.assertEqual(policy.step(o,.2)[0],'lock')
            self.assertEqual(policy.step(o,.4)[0],'wait')
            self.assertEqual(policy.stock,6)

    def test_portrait_crossing_midlines_is_not_cut_or_duplicated(self):
        image=self.arena();image.paste(self.notice,(183,387))
        o,_=self.vision.frame(image)
        self.assertEqual(len(o.bosses),1)

    def test_scaled_portrait_and_no_portrait(self):
        self.assertEqual(self.vision.frame(self.arena())[0].bosses,())
        for scale in (.75,1.25):
            image=self.arena()
            notice=self.notice.resize((round(self.notice.width*scale),round(self.notice.height*scale)))
            image.paste(notice,(10,200))
            self.assertEqual(self.vision.frame(image)[0].bosses,(0,))


class UnknownExitTests(unittest.TestCase):
    def simulate(self,recover=False,observe=False,attack=False):
        now=[0.];taps=[];events=[];count=[0]
        stop=Mock();stop.is_set.return_value=False
        def wait(seconds):now[0]+=1;return False
        stop.wait.side_effect=wait
        session=Mock();session.tap.side_effect=taps.append
        vision=Mock();vision.read_entry.return_value=(200,7)
        def frame_result(image):
            count[0]+=1
            if attack and count[0]<=4:
                return Observation(scene='arena',bosses=(0,),score=b'stable'),None
            if recover and 'hunt_exit' in taps:return Observation(scene='lobby'),None
            return Observation(),None
        vision.frame.side_effect=frame_result
        with patch('hunt.Vision',return_value=vision),patch('hunt.time.monotonic',side_effect=lambda:now[0]):
            with self.assertRaises(RuntimeError) as result:
                run(session,stop,lambda *event:events.append(event),observe=observe,auto_next=False)
        return taps,events,str(result.exception),vision

    def test_unknown_exits_before_pausing_and_confirms_lobby_without_ocr(self):
        taps,events,error,vision=self.simulate(recover=True)
        self.assertEqual(taps,['hunt_exit'])
        self.assertIn('已确认回到围猎主页',error)
        vision.read_entry.assert_not_called()

    def test_unknown_after_lock_exits_instead_of_leaving_attack_running(self):
        taps,events,error,vision=self.simulate(recover=True,attack=True)
        self.assertEqual(taps,['hunt_lock','hunt_exit'])
        self.assertIn('已确认回到围猎主页',error)

    def test_unconfirmed_exit_retries_once_then_alerts(self):
        taps,events,error,vision=self.simulate()
        self.assertEqual(taps,['hunt_exit','hunt_exit'])
        self.assertIn('手动',error)
        self.assertTrue(any(event[0]=='notify' for event in events))

    def test_observe_never_sends_unknown_exit(self):
        taps,events,error,vision=self.simulate(observe=True)
        self.assertEqual(taps,[])
        self.assertIn('仅观察',error)


class PassiveTrackingTests(unittest.TestCase):
    def appeared(self):
        tracker=ObserveTracker()
        tracker.step(frame(stock=2,bosses=(0,)),0)
        message=tracker.step(frame(stock=2,bosses=(0,)),.2)
        self.assertIn('确认萨满出现',message)
        return tracker

    def test_appearance_does_not_require_three_items_or_a_score(self):
        t=ObserveTracker()
        t.step(frame(stock=None,score=None,bosses=(2,)),0)
        self.assertIn('确认萨满出现',t.step(frame(stock=None,score=None,bosses=(2,)),.2))

    def test_absent_avatar_without_lock_is_not_declared_dead(self):
        t=self.appeared();t.step(frame(),.4)
        self.assertIn('无法确认死亡',t.step(frame(),2.4))
        self.assertTrue(t.active)

    def test_score_change_is_labelled_suspected_and_requires_two_reads(self):
        t=self.appeared()
        self.assertNotIn('疑似',t.step(frame(score=b'new'),.4))
        self.assertIn('疑似萨满死亡',t.step(frame(score=b'new'),.6))
        self.assertFalse(t.active)

    def test_lock_transfer_is_labelled_suspected(self):
        t=self.appeared()
        t.step(frame(marker=(.8,.8)),.4)
        self.assertIn('疑似目标死亡或锁定转移',t.step(frame(marker=(.81,.8)),.6))

    def test_unknown_and_lobby_are_not_death_proof(self):
        t=self.appeared()
        self.assertIn('无法判断',t.step(Observation(),.4))
        self.assertIn('无法确认是否击杀',t.step(Observation(scene='lobby'),.6))

    def test_later_spawn_in_same_corner_can_be_tracked(self):
        t=self.appeared();t.step(frame(),.4);t.step(frame(),2.4)
        t.step(frame(bosses=(0,)),3)
        self.assertIn('确认萨满出现',t.step(frame(bosses=(0,)),3.2))

    def test_continuous_observe_forces_no_input_even_if_auto_is_selected(self):
        session=Mock();stop=Mock();stop.is_set.return_value=False
        stop.wait.side_effect=[False]*9+[True]
        vision=Mock();vision.frame.side_effect=[(Observation(),None)]*10
        events=[]
        with patch('hunt.Vision',return_value=vision):
            run(session,stop,lambda *event:events.append(event),observe=False,observe_events=True)
        self.assertEqual(session.capture_game.call_count,10)
        session.tap.assert_not_called();vision.read_entry.assert_not_called()
        self.assertTrue(any('画面未确认' in event[1] for event in events))


class DirectionAndSwitchTests(unittest.TestCase):
    def test_real_directional_portraits(self):
        v=Vision();assets=Path(__file__).resolve().parents[1]/'assets/hunt'
        fixtures=Path(__file__).parent/'fixtures'
        for q,pos in ((0,(0,170)),(1,(360,170)),(3,(365,570))):
            image=Image.new('RGB',(500,900),(45,42,60))
            image.paste(Image.open(assets/'lock.png'),(424,728))
            image.paste(Image.open(assets/'exit.png'),(232,850))
            image.paste(Image.open(fixtures/f'shaman_q{q+1}.png'),pos)
            observation,_=v.frame(image)
            self.assertEqual(observation.scene,'arena')
            self.assertEqual(observation.bosses,(q,))

    def test_unconfirmed_previous_target_does_not_block_other_corners(self):
        tracker=ObserveTracker()
        tracker.step(frame(bosses=(2,)),0)
        tracker.step(frame(bosses=(2,)),.2)
        tracker.step(frame(),.4)
        for q,now in ((0,3),(1,6),(3,9),(2,12)):
            tracker.step(frame(bosses=(q,)),now)
            message=tracker.step(frame(bosses=(q,)),now+.2)
            self.assertIn('确认萨满出现',message)
            self.assertIn('生死未确认',message)
            self.assertEqual(tracker.origin,q)

    def test_single_other_corner_frame_does_not_switch_target(self):
        tracker=ObserveTracker();tracker.step(frame(bosses=(2,)),0)
        tracker.step(frame(bosses=(2,)),.2)
        tracker.step(frame(bosses=(0,)),.4)
        tracker.step(frame(bosses=()),.6)
        self.assertEqual(tracker.origin,2)


class CycleAndAlarmTests(unittest.TestCase):
    def test_auto_next_off_stops_after_confirmed_exit(self):
        p=Policy(auto_next=False)
        p.exit(0,'正常目标结束')
        action,reason=p.step(Observation(scene='lobby',multiplier=200),1)
        self.assertEqual(action,'pause');self.assertIn('未开启自动',reason)

    def test_auto_next_on_rechecks_multiplier_before_start(self):
        p=Policy(auto_next=True);p.exit(0,'正常目标结束')
        self.assertEqual(p.step(Observation(scene='lobby',multiplier=200),1)[0],'wait')
        self.assertTrue(p.auto_next)
        self.assertEqual(p.step(Observation(scene='lobby',multiplier=100),1.2)[0],'pause')

    def test_auto_next_does_not_override_exception_pause(self):
        p=Policy(auto_next=True);p.exit(0,'锁定不足3个，请去1倍场打道具。',True)
        action,reason=p.step(Observation(scene='lobby'),1)
        self.assertEqual(action,'pause');self.assertIn('1倍场',reason)

    def test_alarm_requires_two_frames_and_only_rings_once(self):
        alarm=AppearanceAlarm()
        self.assertFalse(alarm.step(frame(bosses=(0,)),0))
        self.assertTrue(alarm.step(frame(bosses=(0,)),.2))
        for n in range(3):self.assertFalse(alarm.step(frame(bosses=(0,)),.4+n*.2))

    def test_alarm_rearms_for_new_corner_or_new_notice(self):
        alarm=AppearanceAlarm()
        alarm.step(frame(bosses=(0,)),0);alarm.step(frame(bosses=(0,)),.2)
        self.assertFalse(alarm.step(frame(bosses=(1,)),.4))
        self.assertTrue(alarm.step(frame(bosses=(1,)),.6))
        alarm.step(frame(),1)
        alarm.step(frame(),3)
        self.assertFalse(alarm.step(frame(bosses=(1,)),3.2))
        self.assertTrue(alarm.step(frame(bosses=(1,)),3.4))

    def test_brief_flicker_does_not_repeat_alarm(self):
        alarm=AppearanceAlarm();alarm.step(frame(bosses=(0,)),0);alarm.step(frame(bosses=(0,)),.2)
        alarm.step(frame(),.4)
        alarm.step(frame(bosses=(0,)),.6)
        self.assertFalse(alarm.step(frame(bosses=(0,)),.8))

    def test_sound_option_works_in_passive_mode_without_inputs(self):
        for enabled in (True,False):
            session=Mock();stop=Mock();stop.is_set.return_value=False
            stop.wait.side_effect=[False]*4+[True]
            vision=Mock();vision.frame.return_value=(frame(bosses=(1,)),None)
            events=[]
            with patch('hunt.Vision',return_value=vision):
                run(session,stop,lambda *event:events.append(event),observe_events=True,sound_alert=enabled)
            self.assertEqual(sum(event[0]=='sound' for event in events),1 if enabled else 0)
            session.tap.assert_not_called()


class UnknownCycleRecoveryTests(unittest.TestCase):
    def test_unknown_exit_restarts_only_after_confirmed_lobby_and_multiplier(self):
        for multiplier in (200,100):
            now=[0.];taps=[];events=[];finished=[False]
            stop=Mock();stop.is_set.side_effect=lambda:finished[0]
            stop.wait.side_effect=lambda seconds:now.__setitem__(0,now[0]+1) or False
            session=Mock()
            def tap(action):
                taps.append(action)
                if action=='hunt_start':finished[0]=True
            session.tap.side_effect=tap
            vision=Mock();vision.read_entry.return_value=(multiplier,None)
            vision.frame.side_effect=lambda image:(Observation(scene='lobby' if 'hunt_exit' in taps else 'unknown'),None)
            with patch('hunt.Vision',return_value=vision),patch('hunt.time.monotonic',side_effect=lambda:now[0]):
                if multiplier==200:
                    run(session,stop,lambda *event:events.append(event),observe=False,auto_next=True)
                else:
                    with self.assertRaisesRegex(RuntimeError,'200倍场'):
                        run(session,stop,lambda *event:events.append(event),observe=False,auto_next=True)
            self.assertEqual(taps,['hunt_exit','hunt_start'] if multiplier==200 else ['hunt_exit'])
            vision.read_entry.assert_called_once()

    def test_low_stock_still_pauses_after_unknown_exit(self):
        p=Policy(auto_next=True);p.stock=2
        p.exit(0,'锁定不足3个，请去1倍场打道具。',p.stock<3)
        self.assertEqual(p.step(Observation(scene='lobby'),1)[0],'pause')


class LowStockOptionTests(unittest.TestCase):
    def test_disabled_allows_low_quantity_and_never_goes_negative(self):
        for stock in (0,1,2):
            p=Policy(pause_low_stock=False)
            self.assertEqual(p.step(frame(stock=stock,bosses=(0,)),0)[0],'wait')
            self.assertEqual(p.step(frame(stock=stock,bosses=(0,)),.2)[0],'lock')
            self.assertEqual(p.stock,max(0,stock-1))

    def test_disabled_finishes_target_and_keeps_setting_next_round(self):
        p=Policy(pause_low_stock=False,auto_next=True)
        p.step(frame(stock=3,bosses=(0,)),0);p.step(frame(stock=3,bosses=(0,)),.2)
        p.step(frame(stock=None,score=b'new'),.4)
        self.assertEqual(p.step(frame(stock=None,score=b'new'),.6)[0],'exit')
        self.assertFalse(p.pause_after)
        self.assertEqual(p.step(Observation(scene='lobby'),1)[0],'wait')
        self.assertFalse(p.pause_low_stock)

    def test_disabled_allows_unknown_quantity_without_inventing_a_count(self):
        p=Policy(pause_low_stock=False)
        self.assertEqual(p.step(frame(stock=None,bosses=(0,)),0)[0],'wait')
        self.assertEqual(p.step(frame(stock=None,bosses=(0,)),.2)[0],'lock')
        self.assertIsNone(p.stock)
        p.step(frame(stock=None,score=b'new'),.4)
        self.assertEqual(p.step(frame(stock=None,score=b'new'),.6)[0],'exit')
        self.assertFalse(p.pause_after)

    def test_disabled_does_not_override_single_round_setting(self):
        p=Policy(pause_low_stock=False,auto_next=False)
        p.exit(0,'正常结束')
        self.assertEqual(p.step(Observation(scene='lobby'),1)[0],'pause')


class ArenaRecoveryRegressionTests(unittest.TestCase):
    def test_scene_anchor_does_not_depend_on_stock_digits(self):
        v=Vision();assets=Path(__file__).resolve().parents[1]/'assets/hunt'
        image=Image.new('RGB',(500,900),(45,42,60))
        image.paste(Image.open(assets/'lock.png'),(424,728))
        image.paste(Image.open(assets/'exit.png'),(232,850))
        # Cover the quantity label completely; fixed skill artwork is unchanged.
        image.paste((15,15,15),(467,764,500,790))
        observation,_=v.frame(image)
        self.assertEqual(observation.scene,'arena')
        self.assertIn('锁定图标=',observation.evidence)

    def test_three_unknown_exits_stop_reentry_loop_even_with_auto_next(self):
        now=[0.];taps=[]
        stop=Mock();stop.is_set.return_value=False
        stop.wait.side_effect=lambda seconds:now.__setitem__(0,now[0]+1) or False
        session=Mock();session.tap.side_effect=taps.append
        vision=Mock();vision.read_entry.return_value=(200,7)
        vision.frame.side_effect=lambda image:(Observation(scene='lobby' if taps and taps[-1]=='hunt_exit' else 'unknown'),None)
        with patch('hunt.Vision',return_value=vision),patch('hunt.time.monotonic',side_effect=lambda:now[0]):
            with self.assertRaisesRegex(RuntimeError,'连续发生3次'):
                run(session,stop,lambda *event:None,observe=False,auto_next=True,pause_low_stock=False,pause_repeated_unknown=True)
        self.assertEqual(taps,['hunt_exit','hunt_start','hunt_exit','hunt_start','hunt_exit'])


class OptionalQuantityReadTests(unittest.TestCase):
    def test_entry_skips_quantity_ocr_when_disabled(self):
        vision=Vision();image=Image.new('RGB',(500,900))
        with patch.object(vision,'read_consensus',return_value=200) as read:
            self.assertEqual(vision.read_entry(image,'arena',read_stock=False),(200,None))
        self.assertEqual(read.call_count,1)

    def test_disabled_quantity_check_runs_lock_and_exit_with_none_count(self):
        session=Mock();stop=Mock();stop.is_set.return_value=False
        stop.wait.side_effect=[False,False,False,True]
        vision=Mock();vision.read_entry.return_value=(200,None)
        vision.frame.side_effect=[(frame(stock=None,bosses=(0,),score=score),None)
                                  for score in (b'old',b'old',b'old',b'old',b'new',b'new')]
        with patch('hunt.Vision',return_value=vision):
            run(session,stop,lambda *event:None,observe=False,pause_low_stock=False)
        self.assertEqual([call.args[0] for call in session.tap.call_args_list],['hunt_lock','hunt_exit'])
        self.assertFalse(vision.read_entry.call_args.kwargs['read_stock'])


class RepeatedUnknownSwitchTests(unittest.TestCase):
    def test_default_off_keeps_reentering_after_third_confirmed_exit(self):
        now=[0.];taps=[];finished=[False]
        stop=Mock();stop.is_set.side_effect=lambda:finished[0]
        stop.wait.side_effect=lambda seconds:now.__setitem__(0,now[0]+1) or False
        session=Mock()
        def tap(action):
            taps.append(action)
            if taps.count('hunt_exit')>=4:finished[0]=True
        session.tap.side_effect=tap
        vision=Mock();vision.read_entry.return_value=(200,None)
        vision.frame.side_effect=lambda image:(Observation(scene='lobby' if taps and taps[-1]=='hunt_exit' else 'unknown'),None)
        with patch('hunt.Vision',return_value=vision),patch('hunt.time.monotonic',side_effect=lambda:now[0]):
            run(session,stop,lambda *event:None,observe=False,auto_next=True,pause_low_stock=False)
        self.assertEqual(taps,['hunt_exit','hunt_start']*3+['hunt_exit'])


class QuickSoundTests(unittest.TestCase):
    def test_first_frame_audio_alarm_keeps_debounce_after_lobby(self):
        alarm=AppearanceAlarm(required_hits=1)
        self.assertTrue(alarm.step(frame(bosses=(1,)),0))
        self.assertFalse(alarm.step(frame(bosses=(1,)),.2))
        alarm.step(Observation(scene='lobby'),.4)
        self.assertTrue(alarm.step(frame(bosses=(1,)),.6))

    def test_beep_is_short_and_does_not_use_audio_file(self):
        import app
        ui=app.App.__new__(app.App);ui.q=Mock();ui.hunt_audio_path=''
        from audio_alert import AudioPlayer
        ui.audio_player=AudioPlayer()
        with patch('winsound.Beep') as sound,patch('winsound.PlaySound') as file_sound:
            worker=ui.play_hunt_alert();worker.join(timeout=1)
            self.assertFalse(worker.is_alive())
            sound.assert_called_once_with(1800,80)
            file_sound.assert_not_called()
        ui.q.put.assert_not_called()

    def test_audio_bypasses_ui_queue(self):
        import app
        ui=app.App.__new__(app.App);ui.q=Mock()
        with patch.object(ui,'play_hunt_alert') as sound:
            ui.emit_hunt('sound','萨满出现')
        sound.assert_called_once()
        ui.q.put.assert_not_called()
        ui.emit_hunt('status','测试')
        ui.q.put.assert_called_once_with(('status','测试'))


if __name__=='__main__':unittest.main()
