"""Actual Firefox Ronald fitting, cancellation, quota feedback and restart recovery."""
import json
import shutil
import time
from pathlib import Path
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.firefox.options import Options
from selenium.webdriver.firefox.service import Service
from selenium.webdriver.support.ui import WebDriverWait
out=Path('artifacts/sim-creator/guided-large-browser').resolve();out.mkdir(exist_ok=True)
checks=[];timings={};d=None

def launch(profile=None):
    o=Options();o.add_argument('-headless');o.add_argument('-no-remote');o.binary_location='/Applications/Firefox.app/Contents/MacOS/firefox';o.enable_bidi=True
    if profile:o.profile=str(profile)
    return webdriver.Firefox(options=o,service=Service(log_output=str(out/'driver.log')))
def page(code):return json.loads(d.script.execute('async()=>JSON.stringify((await(async()=>{'+code+'})()) ?? null)')['value'])
def el(name):return d.find_element(By.ID,'sim-'+name)
def click(css):d.find_element(By.CSS_SELECTOR,css).click()
def tab():
    WebDriverWait(d,60).until(lambda _:d.find_element(By.CSS_SELECTOR,'[data-tab="sim"]').is_displayed())
    page("const {SimPreview}=await import('/static/sim-preview.mjs');const render=SimPreview.prototype.render;SimPreview.prototype.render=function(){window.__sim=this;return render.call(this)};")
    click('[data-tab="sim"]');WebDriverWait(d,60).until(lambda _:d.find_elements(By.CSS_SELECTOR,'#sim-saved summary'))
def wait(text,seconds=180):WebDriverWait(d,seconds).until(lambda _:text in el('status').text)
def check(text):checks.append(text);print(text,flush=True)
try:
    d=launch();d.set_window_size(1450,1200);d.get('http://192.168.50.213:8002/');tab()
    started=time.monotonic();el('model').send_keys(str(Path.home()/'Downloads/ronald_mcdonald.glb'));wait('Review the suggested');timings['import_seconds']=time.monotonic()-started
    el('viewer').screenshot(str(out/'ronald-before.png'))
    assert page('return window.__sim.renderer.getContext().getError()')==0
    check('Original Ronald loads with visible geometry and prepared guides')
    el('confirm-align').click();el('confirm-markers').click();WebDriverWait(d,60).until(lambda _:el('confirm-head').is_displayed() and el('confirm-head').is_enabled());el('confirm-head').click()
    el('preview').click();WebDriverWait(d,30).until(lambda _:el('cancel').is_displayed());started=time.monotonic();el('cancel').click();wait('cancelled',30);timings['cancel_seconds']=time.monotonic()-started
    assert page('return window.__sim.models.has("before")');check('Cancelling a Ronald fit retains the displayed source and saved inputs')
    started=time.monotonic();el('preview').click();wait('Fit ready',180);timings['fit_seconds']=time.monotonic()-started
    timings['worker']=page('return window.__sim.result.runtimeMetrics');assert timings['worker']['heap_bytes']<1024**3
    assert page('return window.__sim.renderer.getContext().getError()')==0
    el('viewer').screenshot(str(out/'ronald-fitted.png'));check('Retry renders the complete Ronald fit below the WASM heap limit')
    # Browser frames remain available while adjusting guides, without calling a fit.
    click('[data-sim-stage="markers"]');start=page('return performance.now()')
    timings['guide_frames_ms']=page("const times=[];let previous=performance.now();for(let i=0;i<20;i++){await new Promise(requestAnimationFrame);const now=performance.now();times.push(now-previous);previous=now;}return times;")
    # Simulate quota failure on job writes only. Restore before continuing.
    page("window.originalPut=IDBObjectStore.prototype.put;IDBObjectStore.prototype.put=function(...args){if(this.name==='jobs')throw new DOMException('Test quota exhausted','QuotaExceededError');return window.originalPut.apply(this,args);};document.getElementById('sim-name').value='Ronald fitting';document.getElementById('sim-name').dispatchEvent(new Event('input',{bubbles:true}));")
    WebDriverWait(d,30).until(lambda _:'Could not save' in el('save-status').text)
    assert page('return window.__sim.models.has("after")')
    page("IDBObjectStore.prototype.put=window.originalPut;document.getElementById('sim-name').dispatchEvent(new Event('input',{bubbles:true}));")
    WebDriverWait(d,30).until(lambda _:el('save-status').text=='Saved in this browser.')
    check('Quota failure is explicit and keeps the input and fitted preview available')
    original=page("const r=await import('/static/package-runtime/client.mjs');return (await r.store.listJobs()).find(j=>j.kind==='sim');")
    profile=out/'restart-profile';shutil.copytree(d.capabilities['moz:profile'],profile,dirs_exist_ok=True,ignore=shutil.ignore_patterns('lock','.parentlock','parent.lock'))
    d.quit();d=launch(profile);d.get('http://192.168.50.213:8002/');tab();click('#sim-saved summary');click('#sim-saved .saved-batch-row button');wait('Review the suggested')
    reopened=page("const r=await import('/static/package-runtime/client.mjs');return (await r.store.listJobs()).find(j=>j.kind==='sim');")
    assert original['id']==reopened['id'];assert original['files']==reopened['files'];assert original['parameters']['markers']==reopened['parameters']['markers']
    check('Browser restart restores the original Ronald file, joint guides and stable job identity')
    (out/'report.json').write_text(json.dumps({'checks':checks,'timing':timings,'gameplay':'not_tested','fit_quality':'Suggested landmarks used for capacity testing, not model-quality acceptance'},indent=2))
finally:
    if d:
        (out/'last-status.txt').write_text(d.page_source);d.quit()
