"""Firefox painting downloads, cropping, recovery and local-only network checks."""
import json, os, time, shutil
from pathlib import Path
from urllib.parse import urlsplit
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.firefox.options import Options
from selenium.webdriver.firefox.service import Service
from selenium.webdriver.support.ui import WebDriverWait, Select
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.actions.action_builder import ActionBuilder
from selenium.webdriver.common.actions.pointer_input import PointerInput
from package_creation.tests.test_paintings import fixture

out=Path(os.environ.get('PAINTING_BROWSER_ARTIFACTS','artifacts/painting-creator/browser')).resolve()
out.mkdir(parents=True,exist_ok=True)
downloads=out/('downloads-'+str(int(time.time())));downloads.mkdir()
for ext,fmt in [('png','PNG'),('jpg','JPEG'),('webp','WEBP')]:fixture(out/('diagnostic.'+ext),fmt)
options=Options();options.add_argument('-headless');options.add_argument('-no-remote');options.binary_location='/Applications/Firefox.app/Contents/MacOS/firefox';options.enable_bidi=True
options.set_preference('browser.download.folderList',2);options.set_preference('browser.download.dir',str(downloads));options.set_preference('browser.helperApps.neverAsk.saveToDisk','application/octet-stream')
d=webdriver.Firefox(options=options,service=Service(log_output=str(out/'driver.log')))
w=WebDriverWait(d,90);requests=[];checks=[]
def event(e):
    req=e.get('request',{}) if isinstance(e,dict) else e.request
    if not isinstance(req,dict):req=vars(req)
    if urlsplit(req.get('url','')).scheme in ['http','https']:requests.append({'method':req.get('method'),'url':req.get('url'),'body_size':req.get('bodySize')})
d.network.add_event_handler('before_request_sent',event)
def by(id):return d.find_element(By.ID,'painting-'+id)
def page(code):return json.loads(d.script.execute('async()=>JSON.stringify((await(async()=>{'+code+'})()) ?? null)')['value'])
def store(code):return page("const r=await import('/static/package-runtime/client.mjs');"+code)
def fill(id,text):
    e=by(id);e.send_keys(Keys.COMMAND,'a',Keys.NULL);e.send_keys(str(text))
def check(text):checks.append(text);print(text,flush=True)
def tab():
    w.until(lambda _:d.find_element(By.CSS_SELECTOR,'[data-tab="painting"]').is_displayed());d.find_element(By.CSS_SELECTOR,'[data-tab="painting"]').click()
    w.until(lambda _:len(d.find_elements(By.CSS_SELECTOR,'.painting-frame'))==4)
def ready():w.until(lambda _:'Preview ready' in by('preview-status').text)
def saved():return store("return (await r.store.listJobs()).filter(j=>j.kind==='painting');")
url=os.environ.get('PAINTING_BROWSER_URL','http://192.168.50.213:8002/')
try:
    d.set_window_size(1320,1100);d.get(url);tab()
    assert len(d.find_elements(By.CSS_SELECTOR,'[role="tab"]'))==7
    page("const {PaintingPreview}=await import('/static/painting-preview.mjs');const render=PaintingPreview.prototype.render;PaintingPreview.prototype.render=function(){window.__paintingPreview=this;return render.call(this)};return true;")
    for i,ident in enumerate(['bella','lady','city','hills']):
        if i:by('new').click();w.until(lambda _:by('image').is_enabled())
        by('image').send_keys(str(out/('diagnostic.'+['png','jpg','webp','png'][i])));ready()
        d.find_element(By.CSS_SELECTOR,f'.painting-frame[data-id="{ident}"]').click();ready()
        assert page('return window.__paintingPreview.key;')==ident
        assert Select(by('texture-encoder')).first_selected_option.get_attribute('value')=='directxtex'
        alternative=by('texture-encoder').find_element(By.CSS_SELECTOR,'option[value="bodyshop"]')
        assert alternative.get_attribute('disabled') is None
        if not by('compression-options').get_attribute('open'):
            by('compression-options').find_element(By.TAG_NAME,'summary').click()
        Select(by('texture-encoder')).select_by_value('bodyshop');ready()
        fill('creator','BrowserTest');fill('title',ident);fill('price',100+i)
        if i==0:
            stage=by('crop-stage');stage.send_keys('+');stage.send_keys(Keys.ARROW_LEFT)
            w.until(lambda _:saved()[0]['parameters']['crop']['zoom']>1)
            before=saved()[0]['parameters']['crop']['x']
            touch=ActionBuilder(d,mouse=PointerInput('touch','painting-touch'))
            touch.pointer_action.move_to(stage).pointer_down().move_by(20,10).pointer_up();touch.perform()
            w.until(lambda _:saved()[0]['parameters']['crop']['x']!=before)
            camera=page('return window.__paintingPreview.camera.position.toArray();')
            Select(by('fit')).select_by_value('contain');ready()
            assert page('return window.__paintingPreview.camera.position.toArray();')==camera
            by('front').click();by('reset-view').click()
            check('Keyboard and touch cropping, whole-image mode and camera persistence')
        w.until(lambda _:by('build').is_enabled());by('build').click();w.until(lambda _:by('download').is_displayed())
        file=downloads/f'BrowserTest_{ident}.package';w.until(lambda _:file.exists())
        assert file.read_bytes()[:4]==b'DBPF';assert not by('creator').is_enabled()
        assert page('return window.__paintingPreview.key;')==ident
        by('viewer').screenshot(str(out/(ident+'-wall.png')))
        check(ident+' direct package download and actual-frame preview')
    originals=saved();assert len(originals)==4 and all(j['state']=='complete' for j in originals)
    assert len({j['parameters']['identities']['prefix'] for j in originals})==4
    d.refresh();tab();d.find_element(By.CSS_SELECTOR,'#painting-saved summary').click()
    w.until(lambda _:d.find_elements(By.CSS_SELECTOR,'#painting-saved .saved-batch-row button'))
    d.find_element(By.CSS_SELECTOR,'#painting-saved .saved-batch-row button').click();w.until(lambda _:by('download').is_displayed());ready()
    assert not by('creator').is_enabled();check('Reload restores completed image, crop, pinned assets and download')
    d.set_window_size(390,844);d.save_screenshot(str(out/'narrow.png'));assert page('return document.documentElement.scrollWidth<=innerWidth+2;');check('Narrow layout has no horizontal overflow')
    cancelled=store("""
      const source=(await r.store.listJobs()).find(j=>j.kind==='painting');
      const job=await r.saveDraft({kind:'painting',manifest:source.manifest,template:source.template,files:source.files,parameters:{creator:'Recovery',title:'Recovery',price:100,description:'',mode:'clone',crop:source.parameters.crop},ui:source.ui});
      const stop=new Promise((resolve,reject)=>{const h=e=>{if(e.detail.id===job.id&&e.detail.progress===35){r.events.removeEventListener('job',h);r.cancel(job.id).then(resolve,reject);}};r.events.addEventListener('job',h);});
      await r.start(job);return await stop;
    """)
    assert cancelled['state']=='cancelled' and cancelled.get('snapshotHash') and not cancelled.get('output'),cancelled
    key=cancelled['id'];store(f"await r.start(await r.restore('{key}'));return true;")
    w.until(lambda _:store(f"return (await r.store.getJob('{key}')).state;")=='complete')
    resumed=store(f"return await r.store.getJob('{key}');")
    assert resumed['parameters']['identities']==cancelled['parameters']['identities'] and resumed['snapshotHash']==cancelled['snapshotHash']
    check('Cancelled build retries with unchanged snapshot and GUIDs')
    w.until(lambda _:not store('return await r.store.lease();'))
    # Use a large but supported source so the real worker remains busy while
    # WebDriver reloads the page. This does not simulate a completed output.
    from PIL import Image
    maximum=out/'maximum.png';Image.new('RGBA',(4000,8000),(40,90,150,128)).save(maximum)
    d.set_window_size(1320,1100);by('new').click();w.until(lambda _:by('image').is_enabled())
    by('image').send_keys(str(maximum));ready();fill('creator','Recovery');fill('title','Interrupted')
    w.until(lambda _:any(j['label']=='Interrupted' for j in saved()))
    interrupted_id=next(j['id'] for j in saved() if j['label']=='Interrupted')
    store(f"r.events.addEventListener('job',e=>{{if(e.detail.id==='{interrupted_id}'&&e.detail.progress===35)location.reload();}});return true;")
    old_document=d.find_element(By.TAG_NAME,'body');by('build').click();w.until(EC.staleness_of(old_document));tab()
    interrupted=store(f"return await r.restore('{interrupted_id}');")
    assert interrupted['state']=='interrupted' and interrupted.get('snapshotHash') and not interrupted.get('output'),interrupted['state']
    store(f"await r.start(await r.restore('{interrupted_id}'));return true;")
    w.until(lambda _:store(f"return (await r.store.getJob('{interrupted_id}')).state;")=='complete')
    restored=store(f"return await r.store.getJob('{interrupted_id}');")
    assert restored['parameters']['identities']==interrupted['parameters']['identities'] and restored['snapshotHash']==interrupted['snapshotHash']
    check('Reload during processing resumes the same immutable snapshot')
    w.until(lambda _:not store('return await r.store.lease();'))
    token=store("return await r.store.acquire('painting-lock-test');")
    primary=d.current_window_handle;d.switch_to.new_window('tab');d.get(url)
    conflict=store("try {await r.store.acquire('other-tab');return false;} catch(e){return e.message;}")
    assert 'Another package build' in conflict,conflict
    d.close();d.switch_to.window(primary);store(f"await r.store.release('{token}');return true;");check('Cross-tab lease prevents concurrent creation')
    quota=store("""
      const db=await new Promise((resolve,reject)=>{const q=indexedDB.open('sims2-creator-packages',1);q.onsuccess=()=>resolve(q.result);q.onerror=()=>reject(q.error);});
      await new Promise((resolve,reject)=>{const t=db.transaction('meta','readwrite');t.objectStore('meta').put({id:'usage',bytes:r.store.BUDGET});t.oncomplete=resolve;t.onerror=()=>reject(t.error);});db.close();
      try{await r.store.putBlob('painting-quota-test',new Blob(['x']));return false;}catch(e){return e.message;}finally{await r.store.cleanup();}
    """)
    assert '2 GiB limit' in quota,quota
    assert store(f"return (await r.store.getJob('{key}')).state;")=='complete';check('Storage budget failure retains completed paintings')
    damaged=store("""
      const source=(await r.store.listJobs()).find(j=>j.kind==='painting');
      const draft=await r.saveDraft({kind:'painting',manifest:source.manifest,template:source.template,files:source.files,parameters:{creator:'Recovery',title:'Damaged',price:50,crop:source.parameters.crop}});
      const asset=draft.manifest.assets[draft.template.recipe_asset];await r.store.putBlob('asset:'+asset.sha256,new Blob(['damaged']));return draft;
    """)
    error=store(f"try{{await r.restore('{damaged['id']}');return false;}}catch(e){{return e.message;}}")
    assert 'damaged' in error.lower(),error
    assert store(f"return !!(await r.store.getJob('{damaged['id']}'));")
    store(f"const j=await r.store.getJob('{damaged['id']}');const a=j.manifest.assets[j.template.recipe_asset];await r.store.putBlob('asset:'+a.sha256,await(await fetch(a.url)).blob());await r.store.removeJob(j.id);return true;")
    check('Damaged pinned recipes produce recovery errors and retain the source image')
    store(f"await r.store.removeJob('{key}');return true;");assert store(f"return (await r.store.getJob('{key}'))||null;") is None;check('Explicit deletion removes selected painting only')
    d.set_window_size(1320,1100);by('new').click();w.until(lambda _:by('image').is_enabled())
    page("const {PaintingPreview}=await import('/static/painting-preview.mjs');PaintingPreview.prototype.show=async()=>{throw new Error('Isolated WebGL failure test')};return true;")
    by('image').send_keys(str(out/'diagnostic.png'));w.until(lambda _:'3D preview unavailable' in by('preview-status').text)
    fill('creator','Fallback');fill('title','Painting');w.until(lambda _:by('build').is_enabled());by('build').click();w.until(lambda _:by('download').is_displayed())
    w.until(lambda _:(downloads/'Fallback_Painting.package').exists());check('A failed 3D preview still permits a validated package download')
    w.until(lambda _:not store('return await r.store.lease();'))
    # Preserve only this disposable test profile before geckodriver removes it.
    backup=out/('restart-profile-'+str(int(time.time())))
    shutil.copytree(d.capabilities['moz:profile'],backup,ignore=shutil.ignore_patterns('parent.lock','.parentlock','lock'))
    d.quit();options.profile=str(backup)
    d=webdriver.Firefox(options=options,service=Service(log_output=str(out/'restart-driver.log')));w=WebDriverWait(d,90)
    d.network.add_event_handler('before_request_sent',event);d.get(url);tab()
    assert len(saved())==6 and all(j['state']=='complete' for j in saved())
    d.find_element(By.CSS_SELECTOR,'#painting-saved summary').click();w.until(lambda _:d.find_elements(By.CSS_SELECTOR,'#painting-saved .saved-batch-row button'))
    d.find_element(By.CSS_SELECTOR,'#painting-saved .saved-batch-row button').click();w.until(lambda _:by('download').is_displayed());ready()
    check('Firefox restart from preserved test-profile data restores completed paintings and preview')
    assert requests and all(req['method']=='GET' for req in requests),requests
    assert not any('/hair/' in req['url'] for req in requests)
    check('Network capture is GET only, images and packages stay in browser')
    (out/'results.json').write_text(json.dumps({'browser':d.capabilities['browserVersion'],'checks':checks,'requests':requests,'jobs':[{'id':j['id'],'metrics':j['runtimeMetrics'],'validation':j['validation']} for j in originals],'downloads':str(downloads)},indent=2))
except Exception:
    print(d.find_element(By.TAG_NAME,'body').text[-5000:],flush=True);d.save_screenshot(str(out/'failure.png'));raise
finally:d.quit()
