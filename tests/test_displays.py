import io
from pathlib import Path
import sys
import threading
import unittest
from unittest.mock import MagicMock

from PIL import Image

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import adb_backend as adb

# Representative Android dump layouts; these are synthetic, not private device dumps.
ACTIVITIES='''ACTIVITY MANAGER ACTIVITIES (dumpsys activity activities)
Display #0 (activities from top to bottom):
  * Task{aaa #1 type=home}
    mResumedActivity: ActivityRecord{aaa u0 com.android.launcher3/.Launcher t1}
Display #2 (activities from top to bottom):
  * Task{bbb #9 type=standard}
    topResumedActivity=ActivityRecord{bbb u0 com.game.example/.Main t9}
  Resumed activities in task display areas (from top to bottom):
    Resumed: ActivityRecord{bbb u0 com.game.example/.Main t9}
  mFocusedRootTask=Task{bbb #9}
  mResumedActivity: ActivityRecord{bbb u0 com.game.example/.Main t9}
'''
VIEWPORTS='''mViewports=[DisplayViewport{type=INTERNAL, valid=true, isActive=true,
displayId=0, uniqueId='local:100', physicalPort=0},
DisplayViewport{type=EXTERNAL, valid=true, isActive=true,
displayId=2, uniqueId='local:1844674407370955161', physicalPort=2},
DisplayViewport{type=EXTERNAL, valid=true, isActive=false,
displayId=2, uniqueId='local:999', physicalPort=3}]
'''
TARGET=adb.DisplayTarget('com.game.example',2,'1844674407370955161')


class DisplayTests(unittest.TestCase):
    def test_display_local_and_global_boundary(self):
        # A game in display 0 must not be copied to the LAST display by globals.
        dump=ACTIVITIES.replace('com.android.launcher3','com.game.first')
        dump=dump.replace('mResumedActivity: ActivityRecord{bbb u0 com.game.example/.Main t9}',
                          'mResumedActivity: ActivityRecord{aaa u0 com.game.first/.Main t1}')
        parsed=adb.parse_display_activities(dump)
        self.assertEqual(parsed[2],{'com.game.example'})
        self.assertEqual(adb.select_display(parsed,'com.game.first'),(0,'com.game.first'))

    def test_flat_global_not_attributed(self):
        dump='''  Display #0 (activities from top to bottom):
    * Task{a #1}
      mResumedActivity: ActivityRecord{a u0 com.game.example/.Main t1}
  Display #2 (activities from top to bottom):
    * Task{b #2}
      mResumedActivity: ActivityRecord{b u0 com.android.launcher3/.Main t2}
    mResumedActivity: ActivityRecord{a u0 com.game.example/.Main t1}
'''
        self.assertEqual(adb.select_display(adb.parse_display_activities(dump),'com.game.example'),(0,'com.game.example'))

    def test_missing_display_sections_rejected(self):
        with self.assertRaises(adb.AdbError):
            adb.select_display(adb.parse_display_activities('mResumedActivity: ActivityRecord{a u0 com.game.example/.Main}'))

    def test_duplicate_game_rejected(self):
        with self.assertRaises(adb.AdbError):
            adb.select_display({0:{'com.game.example'},2:{'com.game.example'}},'com.game.example')

    def test_preferred_and_unknown_candidates(self):
        parsed={0:{'com.other.app'},2:{'com.game.example'}}
        self.assertEqual(adb.select_display(parsed,'com.game.example'),(2,'com.game.example'))
        with self.assertRaises(adb.AdbError):adb.select_display(parsed)
        with self.assertRaises(adb.AdbError):adb.select_display(parsed,'com.missing.game')

    def test_active_physical_mapping(self):
        self.assertEqual(adb.parse_display_viewports(VIEWPORTS)[2],{TARGET.physical_id})
        self.assertNotEqual(TARGET.physical_id,str(TARGET.logical_id))

    def test_resolve_target_and_ambiguous_mapping(self):
        client=adb.AdbClient(__file__,'device')
        client.run=MagicMock(side_effect=[ACTIVITIES.encode(),VIEWPORTS.encode()])
        self.assertEqual(client.resolve_target(),TARGET)
        extra="DisplayViewport{valid=true, isActive=true, displayId=2, uniqueId='local:123'}"
        for display in (VIEWPORTS+extra,'',VIEWPORTS.replace('isActive=true','isActive=false')):
            client.run=MagicMock(side_effect=[ACTIVITIES.encode(),display.encode()])
            with self.assertRaises(adb.AdbError):client.resolve_target('com.game.example')

    def test_png_warning_and_validation(self):
        buf=io.BytesIO();Image.new('RGB',(8,12),'green').save(buf,format='PNG')
        png=buf.getvalue()
        for prefix in (b'',b'WARNING: multiple displays present\r\n'):
            self.assertEqual(adb.decode_screenshot(prefix+png).size,(8,12))
        for invalid in (b'',b'error: display not found',png[:40],b'\0'+png,b'x'*5000+png):
            with self.assertRaises(adb.AdbError):adb.decode_screenshot(invalid)

    def test_capture_uses_physical_id(self):
        client=adb.AdbClient(__file__,'device');buf=io.BytesIO()
        Image.new('RGB',(8,12)).save(buf,format='PNG');client.run=MagicMock(return_value=buf.getvalue())
        client.screenshot(physical_id=TARGET.physical_id)
        self.assertEqual(client.run.call_args.args[0],['exec-out','screencap','-p','-d',TARGET.physical_id])
        client.run.reset_mock()
        with self.assertRaises(adb.AdbError):client.screenshot()
        client.run.assert_not_called()

    def session(self):
        client=MagicMock();client.resolve_target.return_value=TARGET
        return adb.Session(client,{'package':TARGET.package,'screen_size':[1080,1920]},threading.Event()),client

    def test_inputs_use_logical_display(self):
        session,client=self.session();session.tap('fight')
        self.assertEqual(client.run.call_args.args[0][:5],['shell','input','-d','2','tap'])
        session.swipe_catalog()
        self.assertEqual(client.run.call_args.args[0][:5],['shell','input','-d','2','swipe'])

    def test_changed_mapping_or_app_blocks_input(self):
        session,client=self.session();session.guard(force=True)
        client.resolve_target.return_value=adb.DisplayTarget(TARGET.package,3,'200')
        with self.assertRaises(adb.AdbError):session.tap('fight')
        client.run.assert_not_called()
        client.resolve_target.side_effect=adb.AdbError('game not resumed')
        with self.assertRaises(adb.AdbError):session.swipe_catalog()
        client.run.assert_not_called()

    def test_capture_rechecks_route_and_cancellation(self):
        session,client=self.session();client.screenshot.return_value=Image.new('RGB',(1080,1920))
        session.raw_capture()
        client.screenshot.assert_called_once_with(session.stop,physical_id=TARGET.physical_id)
        self.assertEqual(client.resolve_target.call_count,2)
        session.stop.set();client.run.reset_mock()
        with self.assertRaises(adb.AdbError):session.tap('fight')
        client.run.assert_not_called()


if __name__=='__main__':unittest.main()
