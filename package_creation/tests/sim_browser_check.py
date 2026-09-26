"""Firefox guided fitting, input recovery and browser-only network evidence."""
import json
import os
import shutil
import time
from pathlib import Path
from urllib.parse import urlsplit
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.common.actions.action_builder import ActionBuilder
from selenium.webdriver.common.actions.pointer_input import PointerInput
from selenium.webdriver.firefox.options import Options
from selenium.webdriver.firefox.service import Service
from selenium.webdriver.support.ui import WebDriverWait, Select

out=Path('artifacts/sim-creator/guided-browser').resolve();out.mkdir(exist_ok=True)
options=Options();options.add_argument('-headless');options.add_argument('-no-remote');options.binary_location='/Applications/Firefox.app/Contents/MacOS/firefox';options.enable_bidi=True
d=webdriver.Firefox(options=options,service=Service(log_output=str(out/'driver.log')));w=WebDriverWait(d,120)
requests=[];checks=[];url=os.environ.get('SIM_BROWSER_URL','http://192.168.50.213:8002/')
def event(e):
    r=e.get('request',{}) if isinstance(e,dict) else e.request
    if not isinstance(r,dict):r=vars(r)
    if urlsplit(r.get('url','')).scheme in ['http','https']:requests.append({'method':r.get('method'),'url':r.get('url')})
def el(name):return d.find_element(By.ID,'sim-'+name)
def page(code):return json.loads(d.script.execute('async()=>JSON.stringify((await(async()=>{'+code+'})()) ?? null)')['value'])
def store(code):return page("const r=await import('/static/package-runtime/client.mjs');"+code)
def jobs():return store("return (await r.store.listJobs()).filter(j=>j.kind==='sim');")
def check(name):checks.append(name);print(name,flush=True)
def click(css):d.find_element(By.CSS_SELECTOR,css).click()
def tab():
    w.until(lambda _:d.find_element(By.CSS_SELECTOR,'[data-tab="sim"]').is_displayed())
    page("const {SimPreview}=await import('/static/sim-preview.mjs');const render=SimPreview.prototype.render;SimPreview.prototype.render=function(){window.__sim=this;return render.call(this)};")
    click('[data-tab="sim"]');w.until(lambda _:d.find_elements(By.CSS_SELECTOR,'#sim-saved summary'))
def aligned():w.until(lambda _:'Review the suggested' in el('status').text and el('confirm-align').is_enabled())
def fitted():w.until(lambda _:'Fit ready' in el('status').text and el('preview').is_enabled())
def stage(name):click(f'[data-sim-stage="{name}"]')
try:
    d.network.add_event_handler('before_request_sent',event);d.set_window_size(1420,1100);d.get(url);tab()
    assert page('return window.__sim.mannequins.size')==2
    assert page('return window.__sim.room.visible')
    assert len(d.find_elements(By.CSS_SELECTOR,'[role="tab"]'))==7
    assert not el('naming').get_attribute('open')
    check('Scale room, fixed mannequins and collapsed naming are available before import')
    el('model').send_keys(str(Path('artifacts/sim-creator/guided/humanoid.glb').resolve()));aligned()
    assert page('return window.__sim.models.has("before")')
    assert not page('return window.__sim.models.has("after")')
    assert not el('build').is_enabled()
    check('Import prepares source geometry and suggestions without running a fit')
    el('confirm-align').click();w.until(lambda _:el('marker').is_displayed())
    original=page('return window.__sim.markers.neck')
    d.find_element(By.CSS_SELECTOR,'#sim-viewer canvas').send_keys(Keys.ARROW_UP)
    w.until(lambda _:page('return window.__sim.markers.neck[2]')>original[2])
    el('undo').click();assert page('return window.__sim.markers.neck')==original
    el('redo').click();assert page('return window.__sim.markers.neck[2]')>original[2]
    el('undo').click()
    Select(el('marker-group')).select_by_value('arms');Select(el('marker')).select_by_value('l_elbow')
    before=page('return window.__sim.markers.l_elbow')
    d.find_element(By.CSS_SELECTOR,'#sim-viewer canvas').send_keys(Keys.ARROW_UP)
    assert page('return window.__sim.markers.l_elbow[2]===window.__sim.markers.r_elbow[2]')
    assert abs(page('return window.__sim.markers.l_elbow[0]+window.__sim.markers.r_elbow[0]'))<1e-6
    el('undo').click();click('[data-sim-view="side"]')
    d.find_element(By.CSS_SELECTOR,'#sim-viewer canvas').send_keys(Keys.ARROW_RIGHT)
    assert page('return window.__sim.markers.l_elbow[0]')==before[0]
    assert page('return window.__sim.markers.l_elbow[1]')!=before[1]
    el('undo').click()
    Select(el('marker-group')).select_by_value('torso');Select(el('marker')).select_by_value('neck');click('[data-sim-view="front"]')
    d.execute_script("arguments[0].scrollIntoView({block:'center'})",el('viewer'))
    point=page("const v=window.__sim;const p=v.markerMeshes.get('neck').position.clone().project(v.camera);const r=v.renderer.domElement.getBoundingClientRect();return [Math.round(r.left+(p.x+1)*r.width/2),Math.round(r.top+(1-p.y)*r.height/2)];")
    original=page('return window.__sim.markers.neck')
    actions=ActionBuilder(d,mouse=PointerInput('touch','finger'))
    actions.pointer_action.move_to_location(*point);actions.pointer_action.pointer_down();actions.pointer_action.pause(.1);actions.pointer_action.move_to_location(point[0]+10,point[1]-10);actions.pointer_action.pointer_up();actions.perform()
    assert page('return window.__sim.markers.neck')!=original
    el('undo').click();assert page('return window.__sim.markers.neck')==original
    check('Front/Side keyboard and touch editing, mirrored pairs, Undo and Redo work')
    el('confirm-markers').click();w.until(lambda _:el('confirm-head').is_displayed() and el('confirm-head').is_enabled())
    assert page('return window.__sim.neck.visible')
    assert page('return [...window.__sim.models.get("before").children].filter(m=>m.isMesh).every(m=>m.material.vertexColors)')
    el('confirm-head').click();el('preview').click();fitted()
    assert page('return window.__sim.models.get("after").userData.bones[7].parent.name')=='neck'
    assert page('return window.__sim.models.get("after").userData.bones[64].parent.name')=='head'
    assert page('return window.__sim.renderer.getContext().getError()')==0;el('viewer').screenshot(str(out/'fit-scene.png'));check('Head/body overlay, hierarchy-based fit and disabled package gate work')
    camera=page('return [window.__sim.camera.position.toArray(),window.__sim.controls.target.toArray()]')
    for motion in ['head','arms','knees','none']:Select(el('motion')).select_by_value(motion)
    Select(el('morph')).select_by_value('fat');Select(el('comparison')).select_by_value('before');Select(el('comparison')).select_by_value('after')
    assert page('return [window.__sim.camera.position.toArray(),window.__sim.controls.target.toArray()]')==camera
    geometry=page('return window.__sim.models.get("after").children.find(m=>m.isSkinnedMesh).geometry.attributes.position.array.length')
    click('.sim-display summary');Select(el('mannequin')).select_by_value('af');Select(el('environment')).select_by_value('studio')
    assert not page('return window.__sim.room.visible')
    assert geometry==page('return window.__sim.models.get("after").children.find(m=>m.isSkinnedMesh).geometry.attributes.position.array.length')
    el('confirm-check').click();w.until(lambda _:el('naming').get_attribute('open'))
    el('creator').send_keys('GuidedTest');el('name').send_keys('Reviewed Sim');w.until(lambda _:jobs()[0]['parameters']['sim_name']=='Reviewed Sim')
    original_job=jobs()[0];assert original_job['manifest']['sims']['guided_version']==2
    check('Movement/morph comparison preserves camera, display controls preserve geometry, naming autosaves')
    stage('markers');d.find_element(By.CSS_SELECTOR,'#sim-viewer canvas').send_keys(Keys.ARROW_UP)
    assert el('stale').is_displayed();w.until(lambda _:not jobs()[0]['parameters']['review']['check'])
    el('undo').click();w.until(lambda _:jobs()[0]['parameters']['review']['head']);check('Fitting edits mark the previous result outdated and reset review')
    # Reopen from IndexedDB, then rebuild explicitly from the preserved markers.
    d.refresh();tab();click('#sim-saved summary');click('#sim-saved .saved-batch-row button');aligned()
    assert jobs()[0]['id']==original_job['id'];assert not page('return window.__sim.models.has("after")')
    stage('check');el('preview').click();fitted();check('Reload restores guides and pinned assets without an automatic expensive fit')
    d.set_window_size(390,900);assert page('return document.documentElement.scrollWidth<=window.innerWidth')
    d.save_screenshot(str(out/'narrow.png'));d.set_window_size(1420,1100);d.save_screenshot(str(out/'desktop.png'))
    check('Narrow layout has no horizontal overflow')
    # A separate tab owning the same origin lease blocks fitting.
    d.switch_to.new_window('tab');d.get(url);token=store("return await r.store.acquire('guided-other-tab');")
    d.switch_to.window(d.window_handles[0]);el('preview').click();w.until(lambda _:'another' in el('status').text.lower() or 'already' in el('status').text.lower())
    store('await r.store.release('+json.dumps(token)+');');el('preview').click();fitted();check('Another tab cannot fit concurrently and retry works after release')
    # Preserve an older draft and require an explicit new guided copy.
    old=store("const j=(await r.store.listJobs()).find(j=>j.kind==='sim');const copy=structuredClone(j);delete copy.id;delete copy.parameters.guided_version;copy.parameters.sim_name='Legacy draft';return await r.saveDraft(copy);")
    if not page("return document.querySelector('#sim-saved details').open"):click('#sim-saved summary')
    w.until(lambda _:len(d.find_elements(By.CSS_SELECTOR,'#sim-saved .saved-batch-row'))>=2)
    for row in d.find_elements(By.CSS_SELECTOR,'#sim-saved .saved-batch-row'):
        if 'Legacy draft' in row.text:row.find_element(By.TAG_NAME,'button').click();break
    w.until(lambda _:el('legacy').is_displayed());el('copy').click();aligned()
    assert len(jobs())==3;assert next(j for j in jobs() if j['id']==old['id'])['parameters'].get('guided_version') is None
    check('Legacy guided-fit copy retains the original draft unchanged')
    assert requests and all(r['method']=='GET' for r in requests)
    check('Network capture contains only GET requests, with no model upload')
    store("for(const j of await r.store.listJobs())if(j.kind==='sim')await r.store.removeJob(j.id);")
    assert not jobs();check('Explicit deletion removes saved Sim drafts')
    (out/'report.json').write_text(json.dumps({'checks':checks,'requests':requests,'gameplay':'not_tested'},indent=2))
finally:
    (out/'last-status.txt').write_text(d.page_source)
    d.quit()
