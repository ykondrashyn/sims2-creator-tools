"""Real Firefox defaults, persistence, downloads and legacy RefPack controls."""
import json, os, time
from pathlib import Path
from urllib.parse import urlsplit
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.firefox.options import Options
from selenium.webdriver.firefox.service import Service
from selenium.webdriver.support.ui import Select, WebDriverWait
from package_creation.tests.test_objects import model_fixture, glb

out=Path(os.environ.get('REFPACK_BROWSER_OUTPUT','artifacts/refpack-option/firefox')).resolve()
out.mkdir(parents=True,exist_ok=True)
downloads=out/'downloads';downloads.mkdir(exist_ok=True)
(out/'model.glb').write_bytes(glb(*model_fixture()))
options=Options();options.add_argument('-headless');options.add_argument('-no-remote')
options.binary_location='/Applications/Firefox.app/Contents/MacOS/firefox';options.enable_bidi=True
options.set_preference('browser.download.folderList',2);options.set_preference('browser.download.dir',str(downloads));options.set_preference('browser.helperApps.neverAsk.saveToDisk','application/octet-stream')
d=webdriver.Firefox(options=options,service=Service(log_output=str(out/'driver.log')))
w=WebDriverWait(d,180);requests=[];checks=[]
def page(code):return json.loads(d.script.execute('async()=>JSON.stringify((await(async()=>{'+code+'})())??null)')['value'])
def runtime(code):return page("const r=await import('/static/package-runtime/client.mjs');"+code)
def el(id):return d.find_element(By.ID,id)
def click(id):
 e=el(id);d.execute_script('arguments[0].scrollIntoView({block:"center"})',e);e.click()
def tab(name):d.find_element(By.CSS_SELECTOR,f'[data-tab="{name}"]').click()
def job(kind='object'):return runtime("return (await r.store.listJobs()).sort((a,b)=>b.updated-a.updated).find(j=>j.kind==="+json.dumps(kind)+")")
def check(text):checks.append(text);print(text,flush=True)
def event(e):
 req=e.get('request',{}) if isinstance(e,dict) else vars(e.request)
 if urlsplit(req.get('url','')).scheme in ['http','https']:requests.append({'method':req.get('method'),'url':req.get('url')})
def advanced(prefix):
 node=el(prefix+'-compression-options')
 if not node.get_attribute('open'):node.find_element(By.TAG_NAME,'summary').click()
def restore_object(id, wait_template=True):
 d.refresh();tab('object')
 w.until(lambda _:d.find_elements(By.CSS_SELECTOR,'#object-saved summary'))
 d.find_element(By.CSS_SELECTOR,'#object-saved summary').click()
 w.until(lambda _:d.find_elements(By.CSS_SELECTOR,'#object-saved .saved-batch-row button'))
 label=runtime('return (await r.store.getJob('+json.dumps(id)+')).label')
 row=w.until(lambda _:next((e for e in d.find_elements(By.CSS_SELECTOR,'#object-saved .saved-batch-row') if e.text.startswith(label+' ·')),None))
 row.find_element(By.TAG_NAME,'button').click()
 if wait_template:w.until(lambda _:'Template ready' in el('object-inspection').text)
try:
 d.network.add_event_handler('before_request_sent',event);d.set_window_size(1280,1000)
 d.get(os.environ.get('REFPACK_BROWSER_URL','http://192.168.50.213:8003/'))
 for name,prefix in [('package','tattoo'),('hair','hair'),('object','object'),('painting','painting'),('sim','sim')]:
  tab(name);w.until(lambda _:d.find_elements(By.ID,prefix+'-refpack-compression'))
  assert el(prefix+'-refpack-compression').is_selected()
  assert 'Texture quality, alpha and mipmaps stay unchanged' in el(prefix+'-refpack-help').get_attribute('textContent')
 check('All five package tools default to enabled RefPack and explain lossless storage')
 tab('package');advanced('tattoo')
 assert el('tattoo-refpack-compression').is_enabled()
 assert not Select(el('tattoo-texture-encoder')).options[1].get_attribute('disabled')
 check('Tattoo RefPack remains available independently of DXT5 encoder selection')
 tab('object');w.until(lambda _:len(Select(el('object-standard')).options)==5)
 el('object-model').send_keys(str(out/'model.glb'))
 for key,value in [('creator','RefPackBrowser'),('name','Off'),('title','RefPack test')]:el('object-'+key).send_keys(value)
 click('object-preview');w.until(lambda _:'Preview ready' in el('object-preview-status').text)
 advanced('object');click('object-refpack-compression')
 w.until(lambda _:job()['parameters'].get('refpack_compression') is False)
 assert 'Preview ready' in el('object-preview-status').text
 original_id=job()['id'];restore_object(original_id);advanced('object')
 assert not el('object-refpack-compression').is_selected()
 check('Disabled setting autosaves and reloads without changing identity or invalidating the preview')
 click('object-preview');w.until(lambda _:'Preview ready' in el('object-preview-status').text)
 if el('object-placement-warning').is_displayed():click('object-placement-ack')
 click('object-build');w.until(lambda _:el('object-download').is_displayed())
 click('object-download');w.until(lambda _:(downloads/'RefPackBrowser_Off.package').exists())
 saved=job();assert saved['snapshot']['parameters']['refpack_compression'] is False
 assert saved['validation']['object']['package_compression']['compressed_resources']==0
 assert not el('object-refpack-compression').is_enabled()
 restore_object(original_id);advanced('object')
 assert not el('object-refpack-compression').is_enabled() and not el('object-refpack-compression').is_selected()
 check('Real disabled-mode download has no RefPack and completed snapshots lock and restore the choice')
 click('object-new');w.until(lambda _:el('object-creator').is_enabled())
 assert el('object-refpack-compression').is_selected()
 el('object-model').send_keys(str(out/'model.glb'))
 for key,value in [('creator','RefPackBrowser'),('name','On'),('title','RefPack test')]:el('object-'+key).send_keys(value)
 click('object-preview');w.until(lambda _:'Preview ready' in el('object-preview-status').text)
 if el('object-placement-warning').is_displayed():click('object-placement-ack')
 click('object-build');w.until(lambda _:el('object-download').is_displayed())
 click('object-download');w.until(lambda _:(downloads/'RefPackBrowser_On.package').exists())
 assert job()['validation']['object']['package_compression']['compressed_resources']>0
 assert job()['snapshot']['parameters']['refpack_compression'] is True
 check('New batches reset to enabled RefPack and real enabled-mode packages contain compressed resources')
 historical=json.loads(Path('artifacts/refpack-option/previous-manifest.json').read_text())
 assert 'package_compression' not in historical
 previous=runtime("const j=await r.store.getJob("+json.dumps(original_id)+");return await r.saveDraft({kind:'object',manifest:"+json.dumps(historical)+",template:j.template,files:j.files,parameters:{...j.parameters,id:undefined,identities:undefined,refpack_compression:undefined,object_name:'Historical'},ui:j.ui});")
 restore_object(previous['id']);advanced('object')
 assert not el('object-refpack-compression').is_displayed()
 assert el('object-texture-encoder').is_displayed()
 check('Older pinned drafts keep their original encoder controls and do not acquire RefPack settings')
 click('object-new');tab('package');advanced('tattoo');d.set_window_size(390,900)
 assert page('return document.documentElement.scrollWidth<=innerWidth+2')
 el('tattoo-refpack-compression').send_keys(' ')
 assert not el('tattoo-refpack-compression').is_selected()
 d.save_screenshot(str(out/'narrow-refpack.png'))
 check('Checkbox supports keyboard input and the narrow layout has no horizontal overflow')
 assert all(r['method'] in ['GET','HEAD'] for r in requests),requests
 check('Network capture shows only asset reads, with no package uploads or server build requests')
 (out/'report.json').write_text(json.dumps({'checks':checks,'requests':requests},indent=2))
finally:d.quit()
