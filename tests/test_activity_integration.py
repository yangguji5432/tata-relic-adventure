from pathlib import Path
import sys,tempfile,threading,unittest
from unittest.mock import Mock,patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from audio_alert import AudioPlayer
import app

class CustomAudioTests(unittest.TestCase):
    def test_selected_wav_is_played_asynchronously(self):
        source=Path(__file__).resolve().parents[1]/'assets/hunt/shaman_alert.wav'
        player=AudioPlayer()
        with patch('winsound.PlaySound') as play:
            player.play(str(source))
        self.assertEqual(Path(play.call_args.args[0]),source)
        import winsound
        self.assertTrue(play.call_args.args[1]&winsound.SND_ASYNC)

    def test_mp3_rewinds_cached_alias_without_opening_twice(self):
        with tempfile.TemporaryDirectory() as directory:
            source=Path(directory)/'我的 提示音.mp3';source.write_bytes(b'test')
            player=AudioPlayer()
            with patch.object(player,'command') as command,patch('winsound.PlaySound'):
                player.play(str(source));player.play(str(source))
            commands=[call.args[0] for call in command.call_args_list]
            self.assertEqual(sum(c.startswith('open ') for c in commands),1)
            self.assertEqual(sum(c.startswith('seek ') for c in commands),2)

    def test_missing_or_unsupported_files_are_rejected(self):
        with self.assertRaises(ValueError):AudioPlayer.validate('missing.wav')
        with tempfile.NamedTemporaryFile(suffix='.txt') as source:
            with self.assertRaises(ValueError):AudioPlayer.validate(source.name)

class ActivityExclusionTests(unittest.TestCase):
    def ui(self):
        ui=app.App.__new__(app.App);ui.worker=None;return ui

    def test_adb_fishing_worker_blocks_other_activity(self):
        ui=self.ui();ui.worker=Mock();ui.worker.is_alive.return_value=True
        self.assertTrue(ui.busy())
        with patch('app.messagebox.showinfo'):
            self.assertFalse(ui.idle())

    def test_other_activity_blocks_adb_fishing(self):
        ui=self.ui();ui.worker=Mock();ui.worker.is_alive.return_value=True;ui.get_session=Mock()
        with patch('app.messagebox.showinfo'):
            ui.start_fishing()
        ui.get_session.assert_not_called()

    def test_stop_signals_shared_adb_worker(self):
        ui=self.ui();ui.stop=threading.Event();ui.status=Mock()
        ui.request_stop();self.assertTrue(ui.stop.is_set())
