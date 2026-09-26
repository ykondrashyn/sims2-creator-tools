"""Firefox test-package workflow. No game acceptance or personal drafts are involved."""
import json,os,time
from pathlib import Path
from urllib.parse import urlsplit
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait,Select
from selenium.webdriver.firefox.options import Options
from selenium.webdriver.firefox.service import Service
out=Path(os.environ.get('SIM_BROWSER_OUTPUT','artifacts/sim-creator/experimental-browser')).resolve();out.mkdir(exist_ok=True,parents=True);downloads=out/'downloads';downloads.mkdir(exist_ok=True)
for old in downloads.glob('BrowserTest_Everyday*.package'):old.unlink()
o=Options();o.add_argument('-headless');o.add_argument('-no-remote');o.binary_location='/Applications/Firefox.app/Contents/MacOS/firefox';o.enable_bidi=True
o.set_preference('browser.download.folderList',2);o.set_preference('browser.download.dir',str(downloads));o.set_preference('browser.helperApps.neverAsk.saveToDisk','application/octet-stream')
d=webdriver.Firefox(options=o,service=Service(log_output=str(out/'driver.log')));w=WebDriverWait(d,180);checks=[];requests=[]
def el(n):return d.find_element(By.ID,'sim-'+n)
def click(n):e=el(n);d.execute_script("arguments[0].scrollIntoView({block:'center'})",e);e.click()
def page(code):return json.loads(d.script.execute('async()=>JSON.stringify((await(async()=>{'+code+'})())??null)')['value'])
def store(code):return page("const r=await import('/static/package-runtime/client.mjs');"+code)
def job():return store("return (await r.store.listJobs()).filter(j=>j.kind==='sim').at(-1)")
def tab():d.find_element(By.CSS_SELECTOR,'[data-tab="sim"]').click();w.until(lambda _:d.find_elements(By.CSS_SELECTOR,'#sim-saved summary'))
def check(text):checks.append(text);print(text,flush=True)
def event(e):
 r=e.get('request',{}) if isinstance(e,dict) else e.request
 if not isinstance(r,dict):r=vars(r)
 if urlsplit(r.get('url','')).scheme in ['http','https']:requests.append({'method':r.get('method'),'url':r.get('url')})
try:
 d.network.add_event_handler('before_request_sent',event);d.set_window_size(1400,1100);d.get(os.environ.get('SIM_BROWSER_URL','http://192.168.50.213:8003/'));tab()
 el('model').send_keys(str(Path('artifacts/sim-creator/guided/humanoid.glb').resolve()));w.until(lambda _:'Review the suggested' in el('status').text)
 click('confirm-align');click('confirm-markers');w.until(lambda _:el('confirm-head').is_displayed() and el('confirm-head').is_enabled());click('confirm-head');click('preview');w.until(lambda _:'Fit ready' in el('status').text)
 click('confirm-check');el('creator').send_keys('BrowserTest');el('name').send_keys('Everyday');assert not el('build').is_enabled();click('experimental');w.until(lambda _:el('build').is_enabled());check('Reviewed AM fit enables export only after explicit body-test acknowledgement')
 assert Select(el('texture-encoder')).first_selected_option.get_attribute('value')=='directxtex'
 el('compression-options').find_element(By.TAG_NAME,'summary').click();Select(el('texture-encoder')).select_by_value('bodyshop')
 assert not el('stale').is_displayed(), 'Compression must preserve the reviewed fit'
 print('Invalid controls',page("return [...document.querySelectorAll('#sim-form :invalid')].map(e=>({id:e.id,value:e.value,message:e.validationMessage}))"),flush=True);click('build');time.sleep(1);print('After build:',el('status').text,job()['state'],flush=True);assert job()['state'] in ['building','complete'],el('status').text;w.until(lambda _:el('download').is_displayed());w.until(lambda _:(downloads/'BrowserTest_Everyday.package').exists())
 data=(downloads/'BrowserTest_Everyday.package').read_bytes();assert data[:4]==b'DBPF';j=job();assert j['parameters']['texture_encoder']=='bodyshop';assert j['state']=='complete' and j['validation']['sim']['validated'];assert 'Stock head' in j['validation']['sim']['scope'];check('Direct DBPF download completes with structural report and immutable snapshot')
 first=j['id'];firsthash=j['output']['hashes'];d.refresh();tab();d.find_element(By.CSS_SELECTOR,'#sim-saved summary').click();d.find_element(By.CSS_SELECTOR,'#sim-saved .saved-batch-row button').click();w.until(lambda _:el('download').is_displayed());assert not el('creator').is_enabled();assert Select(el('texture-encoder')).first_selected_option.get_attribute('value')=='bodyshop';assert not el('texture-encoder').is_enabled();check('Completed package and read-only details restore after reload')
 d.set_window_size(390,900);assert page('return document.documentElement.scrollWidth<=innerWidth');d.save_screenshot(str(out/'narrow.png'));d.set_window_size(1400,1100)
 # The generic origin lease and attempt fence also govern this new build branch.
 record=store("const old=(await r.store.listJobs()).find(j=>j.id==="+json.dumps(first)+");const p=structuredClone(old.parameters);delete p.identities;delete p.id;p.sim_name='Retry';return await r.saveDraft({kind:'sim',files:old.files,manifest:old.manifest,template:old.template,parameters:p});")
 rid=record['id'];store("const handler=e=>{if(e.detail.id==="+json.dumps(rid)+"&&e.detail.progress===25){r.events.removeEventListener('job',handler);r.cancel(e.detail.id);}};r.events.addEventListener('job',handler);await r.start(await r.store.getJob("+json.dumps(rid)+"));")
 w.until(lambda _:store("return (await r.store.getJob("+json.dumps(rid)+")).state") in ['cancelled','complete','failed']);print('Cancel state',store("return await r.store.getJob("+json.dumps(rid)+")")['state'],flush=True);before=store("return await r.store.getJob("+json.dumps(rid)+")");assert not before.get('output');store("await r.start(await r.store.getJob("+json.dumps(rid)+"));");w.until(lambda _:store("return (await r.store.getJob("+json.dumps(rid)+")).state") in ['complete','failed']);print('Retry state',store("return await r.store.getJob("+json.dumps(rid)+")")['state'],flush=True);after=store("return await r.store.getJob("+json.dumps(rid)+")");assert before['state']=='cancelled' and after['state']=='complete',after.get('error');assert before['parameters']['identities']==after['parameters']['identities'];assert before['snapshotHash']==after['snapshotHash'];check('Cancellation publishes no stale output and retry preserves identities and snapshot')
 blocked=store("const old=await r.store.getJob("+json.dumps(rid)+");const p=structuredClone(old.parameters);delete p.id;delete p.identities;return await r.saveDraft({kind:'sim',manifest:old.manifest,template:old.template,files:old.files,parameters:p});");token=store("return await r.store.acquire('sim-test-other-tab')");err=store("try{await r.start(await r.store.getJob("+json.dumps(blocked['id'])+"));return ''}catch(e){return e.message}");assert 'another' in err.lower() or 'already' in err.lower();store('await r.store.release('+json.dumps(token)+');');assert store("return (await r.store.getJob("+json.dumps(first)+")).output.hashes")==firsthash
 assert not [r for r in requests if r['method'] not in ['GET','HEAD']];check('Network capture contains only asset reads, inputs and packages stay in the browser')
 (out/'report.json').write_text(json.dumps({'checks':checks,'release':j['manifest']['release'],'package':j['validation'],'metrics':j['runtimeMetrics'],'requests':requests,'gameplay':'not_tested'},indent=2))
finally:
 d.save_screenshot(str(out/'last.png'));d.quit()
