"""Real Firefox workflow and storage tests against an isolated fixture runtime.

Run with Selenium on PYTHONPATH and the staged service listening on port 8002.
The fixture must never be installed in the production template catalog.
"""
import json,time,os,hashlib
from pathlib import Path
from urllib.parse import urlsplit
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.firefox.options import Options
from selenium.webdriver.firefox.service import Service
from selenium.webdriver.support.ui import Select,WebDriverWait
root=Path.cwd();out=Path(os.environ.get('OBJECT_BROWSER_ARTIFACTS',root/'artifacts/object-creator'));downloads=out/('downloads-'+str(int(time.time())));downloads.mkdir()
o=Options();o.add_argument('-headless');o.add_argument('-no-remote');o.binary_location='/Applications/Firefox.app/Contents/MacOS/firefox';o.enable_bidi=True
o.set_preference('browser.download.folderList',2);o.set_preference('browser.download.dir',str(downloads));o.set_preference('browser.helperApps.neverAsk.saveToDisk','application/zip,application/octet-stream')
d=webdriver.Firefox(options=o,service=Service(log_output=str(out/'firefox-driver.log')));w=WebDriverWait(d,90);d.set_script_timeout(90)
requests=[];checks=[]
def event(e):
 req=e.get('request',{}) if isinstance(e,dict) else e.request
 if not isinstance(req,dict):req=vars(req)
 if urlsplit(req.get('url','')).scheme in ['http','https']:requests.append({'method':req.get('method'),'url':req.get('url'),'body_size':req.get('bodySize')})
d.network.add_event_handler('before_request_sent',event)
def by(id):return d.find_element(By.ID,'object-'+id)
def fill(id,text):
 e=by(id);e.send_keys(Keys.COMMAND,'a',Keys.NULL);e.send_keys(text)
def select(id,value):Select(by(id)).select_by_value(value)
def store(script):return d.execute_async_script("const done=arguments[arguments.length-1];import('/static/package-runtime/client.mjs').then(async r=>{"+script+"}).then(done,e=>done({error:e.message}));")
def check(text):checks.append(text);print(text,flush=True)
try:
 d.set_window_size(1280,1100);d.get('http://192.168.50.213:8002/');w.until(lambda _:d.find_element(By.CSS_SELECTOR,'[data-tab="object"]').is_displayed());d.find_element(By.CSS_SELECTOR,'[data-tab="object"]').click();w.until(lambda _:len(Select(by('standard')).options)==1)
 assert Select(by('standard')).first_selected_option.text == 'Test fixture only'
 assert len(d.find_elements(By.CSS_SELECTOR,'[role="tab"]'))==4
 assert not d.find_elements(By.ID,'object-mode')
 by('model').send_keys(str(out/'model.glb'));fill('creator','Firefox');fill('name','Model');fill('title','Firefox model')
 by('preview').click();w.until(lambda _:by('preview').is_enabled() and 'Preview ready' in by('preview-status').text);assert d.find_elements(By.CSS_SELECTOR,'#object-viewer canvas');d.save_screenshot(str(out/'firefox-object-preview.png'));check('Three.js source and converted object preview')
 by('build').click();w.until(lambda _:by('download').is_displayed());by('download').click();w.until(lambda _:(downloads/'Firefox_Model.package').exists());assert (downloads/'Firefox_Model.package').read_bytes()[:4]==b'DBPF';check('Model package downloaded directly')
 first=store("return (await r.store.listJobs()).find(j=>j.label==='Firefox_Model');");assert first['state']=='complete';assert not by('creator').is_enabled()
 d.refresh();d.find_element(By.CSS_SELECTOR,'[data-tab="object"]').click();w.until(lambda _:d.find_elements(By.CSS_SELECTOR,'#object-saved summary'));d.find_element(By.CSS_SELECTOR,'#object-saved summary').click();w.until(lambda _:d.find_elements(By.CSS_SELECTOR,'#object-saved .saved-batch-row button'));d.find_element(By.CSS_SELECTOR,'#object-saved .saved-batch-row button').click();w.until(lambda _:by('download').is_displayed());check('Completed saved batch restored after reload')
 by('new').click();w.until(lambda _:by('creator').is_enabled());select('source','upload');by('packages').send_keys(str(out/'native-clone.package'));fill('creator','Firefox');fill('name','Imported');fill('title','Imported model');by('model').send_keys(str(out/'model.glb'));by('inspect').click();w.until(lambda _:'Template ready' in by('inspection').text)
 by('preview').click();w.until(lambda _:by('preview').is_enabled() and 'Preview ready' in by('preview-status').text);d.save_screenshot(str(out/'firefox-import-preview.png'));check('Custom package and GLB with multiple materials previewed')
 by('build').click();w.until(lambda _:by('download').is_displayed());by('download').click();w.until(lambda _:(downloads/'Firefox_Imported.package').exists());assert (downloads/'Firefox_Imported.package').read_bytes()[:4]==b'DBPF';check('Imported-model package downloaded')
 d.set_window_size(390,844);d.save_screenshot(str(out/'firefox-narrow.png'));assert d.execute_script('return document.documentElement.scrollWidth<=innerWidth+2');check('Narrow viewport has no horizontal overflow')
 # Exercise the same API used by the UI in this fresh test profile.
 cancelled=store("""
 const m=await r.manifest();
 const source=(await r.store.listJobs()).find(j=>j.label==='Firefox_Model');
 const job=await r.saveDraft({kind:'object',template:m.objects.items[0],files:source.files,
   parameters:{creator:'Recovery',object_name:'Object',title:'Recovery object',description:'',price:40,mode:'model',model_file:'model.glb',scale:1,rotation:0},ui:{}});
 const stopped=new Promise((resolve,reject)=>{
   const handler=e=>{if(e.detail.id===job.id && e.detail.progress?.color==='Creating and checking object'){
     r.events.removeEventListener('job',handler);
     setTimeout(()=>r.cancel(job.id).then(resolve,reject),0);
   }};
   r.events.addEventListener('job',handler);
 });
 await r.start(job);return await stopped;
 """)
 assert cancelled['state']=='cancelled' and cancelled.get('snapshotHash') and not cancelled.get('output'),cancelled
 key=cancelled['id'];identities=cancelled['parameters']['identities'];snapshot=cancelled['snapshotHash']
 store(f"await r.start(await r.restore('{key}'));return true;")
 w.until(lambda _:store(f"return (await r.store.getJob('{key}')).state;")=='complete')
 resumed=store(f"return await r.store.getJob('{key}');")
 assert resumed['parameters']['identities']==identities and resumed['snapshotHash']==snapshot
 check('Cancellation during object generation retains snapshot and resumes with unchanged GUIDs')
 w.until(lambda _:not store('return await r.store.lease();'))
 token=store("return await r.store.acquire('isolated-tab-lock-test');")
 primary=d.current_window_handle;d.switch_to.new_window('tab');d.get('http://192.168.50.213:8002/')
 conflict=store("try {await r.store.acquire('competing-tab');return false;} catch(e) {return e.message;}")
 assert 'Another package build' in conflict,conflict
 d.close();d.switch_to.window(primary);store(f"await r.store.release('{token}');return true;")
 check('Two browser tabs cannot acquire simultaneous package build leases')
 quota=store("""
 const db=await new Promise((resolve,reject)=>{const q=indexedDB.open('sims2-creator-packages',1);q.onsuccess=()=>resolve(q.result);q.onerror=()=>reject(q.error);});
 await new Promise((resolve,reject)=>{const t=db.transaction('meta','readwrite');t.objectStore('meta').put({id:'usage',bytes:r.store.BUDGET});t.oncomplete=resolve;t.onerror=()=>reject(t.error);});
 db.close();
 try {await r.store.putBlob('quota-test',new Blob(['x']));return false;}
 catch(e) {return e.message;}
 finally {await r.store.cleanup();}
 """)
 assert '2 GiB limit' in quota,quota
 assert store(f"return (await r.store.getJob('{key}')).state;")=='complete'
 check('Storage budget rejection retains saved batches and completed downloads')
 store(f"await r.store.removeJob('{key}');return true;")
 assert store(f"return (await r.store.getJob('{key}')) || null;") is None
 check('Explicit deletion removes the selected saved object batch')
 assert requests and all(r['method']=='GET' for r in requests),requests
 check('Network capture contains GET only, no package or model uploads')
 result={'checks':checks,'browser':d.capabilities['browserVersion'],'requests':requests,'download_directory':str(downloads),'first_job':{'id':first['id'],'identities':first['parameters']['identities'],'runtime_metrics':first['runtimeMetrics']}}
 (out/'firefox-results.json').write_text(json.dumps(result,indent=2))
except Exception:
 print('STATUS',by('status').text,'INSPECTION',by('inspection').text,flush=True);d.save_screenshot(str(out/'firefox-failure.png'));(out/'firefox-failure.html').write_text(d.page_source);raise
finally:d.quit()
