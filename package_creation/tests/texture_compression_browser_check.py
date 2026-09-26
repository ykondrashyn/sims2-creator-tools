"""Firefox compression selection, restoration, snapshots and per-tool defaults."""
import json, os, time
from pathlib import Path
from urllib.parse import urlsplit
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.firefox.options import Options
from selenium.webdriver.firefox.service import Service
from selenium.webdriver.support.ui import Select, WebDriverWait
from package_creation.tests.test_objects import model_fixture, glb

out = Path(os.environ.get('ENCODER_BROWSER_OUTPUT','artifacts/texture-encoders/firefox-options')).resolve()
out.mkdir(parents=True,exist_ok=True)
downloads=out/'downloads'
downloads.mkdir(exist_ok=True)
(out/'model.glb').write_bytes(glb(*model_fixture()))
options=Options()
options.add_argument('-headless')
options.add_argument('-no-remote')
options.binary_location='/Applications/Firefox.app/Contents/MacOS/firefox'
options.enable_bidi=True
options.set_preference('browser.download.folderList',2)
options.set_preference('browser.download.dir',str(downloads))
options.set_preference('browser.helperApps.neverAsk.saveToDisk','application/octet-stream')
d=webdriver.Firefox(options=options,service=Service(log_output=str(out/'driver.log')))
w=WebDriverWait(d,180)
requests=[]
checks=[]
def page(code):return json.loads(d.script.execute('async()=>JSON.stringify((await(async()=>{'+code+'})())??null)')['value'])
def runtime(code):return page("const r=await import('/static/package-runtime/client.mjs');"+code)
def el(name):return d.find_element(By.ID,name)
def click(name):
 e=el(name)
 d.execute_script('arguments[0].scrollIntoView({block:"center"})',e)
 e.click()
def tab(name):d.find_element(By.CSS_SELECTOR,f'[data-tab="{name}"]').click()
def check(text):checks.append(text);print(text,flush=True)
def event(e):
 req=e.get('request',{}) if isinstance(e,dict) else vars(e.request)
 if urlsplit(req.get('url','')).scheme in ['http','https']:requests.append({'method':req.get('method'),'url':req.get('url')})
def encoder(tool):return Select(el(tool+'-texture-encoder'))
def opened():return runtime("return (await r.store.listJobs()).sort((a,b)=>b.updated-a.updated).find(j=>j.kind==='object')")
def object_saved_open():
 d.refresh();tab('object')
 w.until(lambda _:d.find_elements(By.CSS_SELECTOR,'#object-saved summary'))
 d.find_element(By.CSS_SELECTOR,'#object-saved summary').click()
 w.until(lambda _:d.find_elements(By.CSS_SELECTOR,'#object-saved .saved-batch-row button'))
 d.find_element(By.CSS_SELECTOR,'#object-saved .saved-batch-row button').click()
 w.until(lambda _:'Template ready' in el('object-inspection').text)
try:
 d.network.add_event_handler('before_request_sent',event)
 d.set_window_size(1280,1000)
 d.get(os.environ.get('ENCODER_BROWSER_URL','http://192.168.50.213:8003/'))
 tab('package')
 w.until(lambda _:el('tattoo-compression-options').is_displayed())
 assert not el('tattoo-compression-options').get_attribute('open')
 el('tattoo-compression-options').find_element(By.TAG_NAME,'summary').click()
 assert encoder('tattoo').first_selected_option.get_attribute('value')=='directxtex'
 assert not encoder('tattoo').options[1].get_attribute('disabled')
 assert 'DXT5' in el('tattoo-texture-encoder-help').text
 check('Tattoo defaults to DirectXTex and offers recovered Body Shop DXT5')
 encoder('tattoo').select_by_value('bodyshop')
 el('catalog-name').send_keys('BodyShop Family Tattoo')
 d.find_element(By.CSS_SELECTOR,'#tattoo-list [data-field=label]').send_keys('Diagnostic')
 d.find_element(By.CSS_SELECTOR,'#tattoo-list input[data-gender=am]').send_keys(str(Path('artifacts/wasm-migration/fixtures/am.png').resolve()))
 w.until(lambda _:el('build-button').is_enabled());click('build-button')
 w.until(lambda _:runtime("return (await r.store.listJobs()).find(j=>j.kind==='tattoo')?.state")=='complete')
 tattoo=runtime("return (await r.store.listJobs()).find(j=>j.kind==='tattoo')")
 assert tattoo['parameters']['texture_encoder']=='bodyshop' and tattoo['snapshot']['parameters']['texture_encoder']=='bodyshop'
 assert 'bodyshop' in json.dumps(tattoo['validation'])
 w.until(lambda _:el('download-link').is_displayed());click('download-link')
 w.until(lambda _:list(downloads.glob('*.package')))
 assert list(downloads.glob('*.package'))[0].read_bytes()[:4]==b'DBPF'
 check('Real DXT5 tattoo download preserves the encoder through client normalization and snapshot')
 tab('object')
 w.until(lambda _:len(Select(el('object-standard')).options)==5)
 el('object-model').send_keys(str(out/'model.glb'))
 for key,value in [('creator','EncoderBrowser'),('name','Object'),('title','Encoder test')]:el('object-'+key).send_keys(value)
 click('object-preview')
 w.until(lambda _:'Preview ready' in el('object-preview-status').text)
 assert encoder('object').first_selected_option.get_attribute('value')=='directxtex'
 el('object-compression-options').find_element(By.TAG_NAME,'summary').click()
 encoder('object').select_by_value('bodyshop')
 w.until(lambda _:opened()['parameters'].get('texture_encoder')=='bodyshop')
 assert 'Compression changed' in el('object-preview-status').text
 old_id=opened()['id']
 object_saved_open()
 assert encoder('object').first_selected_option.get_attribute('value')=='bodyshop'
 assert opened()['id']==old_id
 check('Object compression autosaves and survives draft reload without changing identity')
 click('object-preview');w.until(lambda _:'Preview ready' in el('object-preview-status').text)
 current=runtime("const j=await r.store.getJob("+json.dumps(old_id)+");return (await r.objectPreview(j)).report.texture_encoding")
 assert current['selected']=='bodyshop' and current['effective_encoders']==['bodyshop']
 check('Object preview uses the selected compressor')
 if el('object-placement-warning').is_displayed():click('object-placement-ack')
 click('object-build');w.until(lambda _:el('object-download').is_displayed())
 click('object-download');w.until(lambda _:(downloads/'EncoderBrowser_Object.package').exists())
 saved=opened()
 assert saved['snapshot']['parameters']['texture_encoder']=='bodyshop'
 assert saved['validation']['object']['texture_encoding']['effective_encoders']==['bodyshop']
 assert not el('object-texture-encoder').is_enabled()
 object_saved_open();w.until(lambda _:el('object-download').is_displayed())
 assert encoder('object').first_selected_option.get_attribute('value')=='bodyshop'
 assert not el('object-texture-encoder').is_enabled()
 check('Downloaded object locks compression in its immutable snapshot and restores unchanged')
 click('object-new')
 w.until(lambda _:el('object-creator').is_enabled())
 assert encoder('object').first_selected_option.get_attribute('value')=='directxtex'
 tab('sim');w.until(lambda _:d.find_elements(By.CSS_SELECTOR,'#sim-saved summary'))
 assert encoder('sim').first_selected_option.get_attribute('value')=='directxtex'
 tab('painting');w.until(lambda _:len(d.find_elements(By.CSS_SELECTOR,'.painting-frame'))==4)
 assert encoder('painting').first_selected_option.get_attribute('value')=='directxtex'
 check('New batches and other tabs start independently with DirectXTex')
 # Open an actual historical engine with the current form. Do not migrate it.
 previous=json.loads(Path(os.environ.get('ENCODER_HISTORICAL_MANIFEST','artifacts/bodyshop-family/previous-manifest.json')).read_text())
 assert previous['texture_encoders']['version']==1
 historical=runtime("const j=await r.store.getJob("+json.dumps(old_id)+");return await r.saveDraft({kind:'object',manifest:"+json.dumps(previous)+",template:j.template,files:j.files,parameters:{...j.parameters,id:undefined,identities:undefined,texture_encoder:'bodyshop_dxt3',object_name:'Historical'},ui:j.ui});")
 tab('object')
 object_saved_open()
 el('object-compression-options').find_element(By.TAG_NAME,'summary').click()
 assert encoder('object').first_selected_option.get_attribute('value')=='bodyshop_dxt3'
 assert not any(o.get_attribute('value')=='bodyshop' for o in encoder('object').options)
 check('Version-1 pinned drafts restore their selected DXT3-only option without migration')
 d.set_window_size(390,900)
 assert page('return document.documentElement.scrollWidth<=innerWidth+2')
 d.save_screenshot(str(out/'narrow.png'))
 assert not [r for r in requests if r['method'] not in ['GET','HEAD']]
 (out/'report.json').write_text(json.dumps({'checks':checks,'requests':requests,'release':saved['manifest']['release'],'gameplay':'not_tested'},indent=2))
finally:
 d.save_screenshot(str(out/'last.png'))
 d.quit()
