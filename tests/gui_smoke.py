"""Run with a graphical session: python3 tests/gui_smoke.py [screenshot.png].
Uses simulated GPUs and a temporary settings directory; never changes GPU limits.
"""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from dataclasses import replace
import tempfile
from unittest.mock import patch
from v100set import app as ui
from v100set.core import Settings, parse_gpus
from gi.repository import GLib, Gdk

A='GPU-11111111-2222-3333-4444-555555555555'
B='GPU-aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee'
C='GPU-99999999-8888-7777-6666-555555555555'
gpus=parse_gpus(f'{A}, NVIDIA RTX A400, 0000:05:00.0, [N/A], 50, 10, 50, 34\n{B}, Tesla V100-PCIE-32GB, 0000:0C:00.0, 23.5, 200, 100, 250, 36\n{C}, Tesla V100-PCIE-32GB, 0000:0D:00.0, 24.1, 200, 100, 250, 35')
calls=[]
failure=[]
temporary=tempfile.TemporaryDirectory()
settings=Settings(temporary.name)
def apply(uuid,watts):
    calls.append((uuid,watts))
    for i,gpu in enumerate(gpus):
        if gpu.uuid==uuid:
            gpus[i]=replace(gpu,limit=watts)
    return watts

def later(ms,func):
    def guarded():
        try:
            func()
        except Exception as exc:
            failure.append(repr(exc))
            application.quit()
        return False
    GLib.timeout_add(ms,guarded)

def start():
    assert len(application.cards)==3, 'GPU rows missing'
    application.cards[B].scale.set_value(199)
    application.cards[B].scale.set_value(198)
    application.cards[B].scale.set_value(195)
    later(1000,saved)

def saved():
    assert calls==[(B,195)],f'Debounce failed: {calls}'
    application.save()
    assert settings.read()['limits'][B]==195
    application.start_check.set_active(True)
    assert settings.autostart.exists()
    assert settings.read()['start_at_login']
    application.start_check.set_active(False)
    assert not settings.autostart.exists()
    assert not settings.read()['start_at_login']
    application.cards[B].scale.set_value(190)
    later(1000,load)

def load():
    assert gpus[1].limit==190
    application.load()
    later(1200,finish)

def finish():
    assert gpus[1].limit==195, 'LOAD did not restore saved value'
    assert not application.busy and not application.pending
    assert application.save_button.get_sensitive()
    assert not failure
    if len(sys.argv)>1:
        window=application.window.get_window()
        size=application.window.get_size()
        image=Gdk.pixbuf_get_from_window(window,0,0,size.width,size.height)
        image.savev(sys.argv[1],'png',[],[])
    print('GUI PASS: 3 cards, debounced live apply, SAVE, LOAD, autostart on/off')
    application.quit()

with patch.object(ui,'discover',side_effect=lambda:list(gpus)), patch.object(ui,'apply_limit',side_effect=apply):
    application=ui.App()
    application.set_application_id('io.github.v100set.smoketest')
    application.settings=settings
    later(1600,start)
    later(12000,lambda: (_ for _ in ()).throw(TimeoutError('GUI test timed out')))
    application.run([])
    application.poll_pool.shutdown(wait=True,cancel_futures=True)
    application.apply_pool.shutdown(wait=True,cancel_futures=True)
    if not failure:
        settings.set_startup(True)
        gpus[1]=replace(gpus[1],limit=180)
        application=ui.App(startup=True)
        application.set_application_id('io.github.v100set.startuptest')
        application.settings=settings
        def check_startup():
            assert not application.window.get_visible(), 'Startup should remain in tray'
            assert gpus[1].limit==195, 'Startup did not restore saved limit'
            assert not application.restore_on_start
            print('GUI PASS: enabled login startup restores saved limits and stays hidden')
            application.quit()
        later(1800,check_startup)
        application.run([])
        application.poll_pool.shutdown(wait=True,cancel_futures=True)
        application.apply_pool.shutdown(wait=True,cancel_futures=True)
temporary.cleanup()
if failure:
    raise SystemExit('; '.join(failure))
