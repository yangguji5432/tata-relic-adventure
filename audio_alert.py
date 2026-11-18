"""Local Windows audio only; plays selected files without shell commands."""
import ctypes
from pathlib import Path
import threading

FORMATS={'.wav','.mp3','.wma','.m4a','.aac','.mid','.midi'}

class AudioPlayer:
    def __init__(self):
        self.lock=threading.RLock();self.alias='tata_shaman_alert';self.opened=None

    @staticmethod
    def validate(path):
        target=Path(path).resolve()
        if not target.is_file():raise ValueError('音频文件不存在：'+str(target))
        if target.suffix.lower() not in FORMATS:raise ValueError('请选择WAV、MP3或受支持的音频文件。')
        if any(c in str(target) for c in ('"','\n','\r')):raise ValueError('音频路径含有不支持的字符。')
        return str(target)

    def command(self,text):
        buffer=ctypes.create_unicode_buffer(300)
        code=ctypes.windll.winmm.mciSendStringW(text,buffer,len(buffer),None)
        if code:
            ctypes.windll.winmm.mciGetErrorStringW(code,buffer,len(buffer))
            raise RuntimeError('Windows无法播放此音频，请换用PCM WAV：'+buffer.value)

    def close(self):
        with self.lock:
            if self.opened:
                try:self.command('close '+self.alias)
                finally:self.opened=None

    def stop(self):
        import winsound
        with self.lock:
            winsound.PlaySound(None,0)
            self.close()

    def play(self,path=''):
        import winsound
        with self.lock:
            if not path:
                self.close();winsound.Beep(1800,80);return
            path=self.validate(path)
            if Path(path).suffix.lower()=='.wav':
                self.close()
                winsound.PlaySound(path,winsound.SND_FILENAME|winsound.SND_ASYNC|winsound.SND_NODEFAULT)
                return
            winsound.PlaySound(None,0)
            if self.opened!=path:
                self.close()
                self.command(f'open "{path}" alias {self.alias}')
                self.opened=path
            self.command('stop '+self.alias)
            self.command('seek '+self.alias+' to start')
            self.command('play '+self.alias)
