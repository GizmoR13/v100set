"""Simulated 1–8 GPU layout and linked controls regression test."""
import sys,tempfile
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from unittest.mock import patch
from dataclasses import replace
from v100set import app as ui
from v100set.core import GPU,Settings
from gi.repository import GLib,Gdk
gpus=[]; calls=[]; errors=[]; clock_calls=[]
tmp=tempfile.TemporaryDirectory()
def apply(uuid,watts):
    calls.append((uuid,watts))
    for i,g in enumerate(gpus):
        if g.uuid==uuid: gpus[i]=replace(g,limit=watts)
    return watts
def apply_clock(uuid,value):
    clock_calls.append((uuid,value))
    return value
def later(ms,fn):
    def run():
        try: fn()
        except Exception as e: errors.append(repr(e)); app.quit()
        return False
    GLib.timeout_add(ms,run)
def layout(n=1):
    gpus[:]=[GPU(f'GPU-{i}','Tesla V100-PCIE-32GB',f'0000:{i:02x}:00.0',25,170,100,250,35,135,(135,900,1005,1380)) for i in range(n)]
    app.refresh(gpus,None,app.revision)
    assert len(app.cards)==n
    for i,c in enumerate(app.cards.values()):
        assert app.rows.child_get_property(c,'left-attach')==i//4
        assert app.rows.child_get_property(c,'top-attach')==i%4
    if n<8: later(100,lambda:layout(n+1))
    else:
        app.link_check.set_active(True)
        app.cards['GPU-3'].scale.set_value(183)
        assert all(c.scale.get_value()==185 for c in app.cards.values())
        later(1500,linked)
def linked():
    assert len(calls)==8 and all(w==185 for _,w in calls),calls
    app.cards['GPU-3'].clock_scale.set_value(2)
    later(1200,clocks_linked)
def clocks_linked():
    assert len(clock_calls)==8 and all(v==900 for _,v in clock_calls),clock_calls
    app.save()
    assert len(app.settings.read()['clocks'])==8
    app.reset_clocks()
    later(1200,auto)
def auto():
    assert all(v is None for _,v in clock_calls[-8:])
    app.load()
    later(1500,loaded)
def loaded():
    assert all(v==900 for _,v in clock_calls[-8:])
    app.window.resize(1250,1000)
    later(1200,resized)
def resized():
    assert app.ui_scale>0.9,app.ui_scale
    assert app.settings.read()['window_size']==list(app.window.get_size())
    if len(sys.argv)>1:
        w,h=app.window.get_size()
        Gdk.pixbuf_get_from_window(app.window.get_window(),0,0,w,h).savev(sys.argv[1],'png',[],[])
    print('PASS: layouts 1–8, linked 5 W changes on 8 GPUs, scaling, saved size')
    app.quit()
with patch.object(ui,'discover',side_effect=lambda:list(gpus)),patch.object(ui,'apply_limit',side_effect=apply),patch.object(ui,'apply_clock',side_effect=apply_clock):
    app=ui.App();app.set_application_id('io.github.v100set.layouttest');app.settings=Settings(tmp.name)
    later(500,layout)
    later(15000,lambda:(_ for _ in ()).throw(TimeoutError('timeout')))
    app.run([]);app.poll_pool.shutdown(wait=True);app.apply_pool.shutdown(wait=True)
    saved=app.settings.read().get('window_size')
    if not errors:
        app.settings.set_startup(True)
        app=ui.App(startup=True);app.set_application_id('io.github.v100set.restoretest');app.settings=Settings(tmp.name)
        def restored():
            app.show_panel()
            assert app.saved_size==saved
            assert len(app.clock_applied)==8 and all(v==900 for v in app.clock_applied.values())
            assert app.link_check.get_active()
            print('PASS: restored window dimensions and link preference')
            app.quit()
        later(2000,restored)
        app.run([]);app.poll_pool.shutdown(wait=True);app.apply_pool.shutdown(wait=True)
if errors: raise SystemExit(str(errors))
