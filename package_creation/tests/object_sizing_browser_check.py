"""Real Firefox sizing, contextual preview, persistence and download acceptance."""
from pathlib import Path
exec(Path('package_creation/tests/object_browser_check.py').read_text().split('try:\n d.set_window_size')[0])
from package_creation.tests.test_objects import model_fixture,glb
url=os.environ.get('OBJECT_BROWSER_URL','http://192.168.50.213:8002/')
from urllib.request import urlopen
with urlopen(url.rstrip('/')+'/api/v1/package-runtime/manifest') as response: runtime_manifest=json.load(response)
reference_height=runtime_manifest['objects']['reference']['reference_height']
template_heights={t['id']:t['dimensions']['height'] for t in runtime_manifest['objects']['items']}
(out/'model.glb').write_bytes(glb(*model_fixture()))
def select(id,value):
 if id in ["environment","mannequin"] and not by(id).is_displayed():d.find_element(By.CSS_SELECTOR,".object-display-controls summary").click()
 Select(by(id)).select_by_value(value)

def preview():
 w.until(lambda _:by('preview').is_enabled());by('preview').click();w.until(lambda _:by('preview').is_enabled() and bool(by('preview-status').text))
 assert 'Preview ready' in by('preview-status').text,by('preview-status').text

def page(code):
 result=d.script.execute("async()=>JSON.stringify(await(async()=>{"+code+"})())")
 return json.loads(result["value"])

def observed():
 return page("const p=window.__scalePreview;return p?{camera:p.camera.position.toArray(),target:p.controls.target.toArray(),matrix:p.models.get('converted').matrix.toArray(),height:p.layout.height,dimensions:p.layout.dimensions,environment:p.options.environment,mannequin:p.options.mannequin,table:p.table.visible,elevation:p.models.get('original').position.y}:null;")
def new():
 by('new').click();w.until(lambda _:by('creator').is_enabled())
def ack():
 if by('placement-warning').is_displayed() and not by('placement-ack').is_selected():by('placement-ack').click()
def restore_latest():
 d.refresh();w.until(lambda _:d.find_element(By.CSS_SELECTOR,'[data-tab="object"]').is_displayed());d.find_element(By.CSS_SELECTOR,'[data-tab="object"]').click()
 w.until(lambda _:d.find_elements(By.CSS_SELECTOR,'#object-saved summary'));d.find_element(By.CSS_SELECTOR,'#object-saved summary').click()
 w.until(lambda _:d.find_elements(By.CSS_SELECTOR,'#object-saved .saved-batch-row button'));d.find_element(By.CSS_SELECTOR,'#object-saved .saved-batch-row button').click()
 w.until(lambda _:bool(by('inspection').text))
def observe():
 return page("const {ObjectPreview}=await import('/static/object-preview.mjs');const render=ObjectPreview.prototype.render;ObjectPreview.prototype.render=function(){window.__scalePreview=this;return render.call(this)};return true;")
try:
 d.set_window_size(1280,1100);d.get(url);w.until(lambda _:d.find_element(By.CSS_SELECTOR,'[data-tab="object"]').is_displayed());d.find_element(By.CSS_SELECTOR,'[data-tab="object"]').click()
 w.until(lambda _:len(Select(by('standard')).options)==5)
 assert [o.get_attribute('value') for o in Select(by('standard')).options]==['urn','fruit-bowl','venus','chimes','chi']
 assert not d.find_elements(By.ID,'object-scale') and abs(float(by('height').get_attribute('value'))-template_heights['urn']/reference_height*100)<.01
 assert by('fit').get_attribute('aria-pressed') is None and not by('fit').is_enabled()
 assert not d.find_elements(By.ID,'object-mode')
 check('Five decoration templates, independent height controls, unchanged model workflow')
 observe();by('model').send_keys(str(out/'model.glb'));preview()
 first=observed();assert abs(first['height']-template_heights['urn'])<1e-6 and first['environment']=='room' and first['mannequin']=='am'
 assert by('placement-warning').is_displayed();assert not by('placement-ack').is_selected()
 fill('creator','ScaleBrowser');fill('name','Floor');fill('title','Scale test')
 by('build').click();w.until(lambda _:'acknowledge' in by('status').text)
 assert not by('download').is_displayed();ack()
 camera=observed()['camera'];matrix=observed()['matrix']
 select('mannequin','af');assert observed()['matrix']==matrix and observed()['camera']==camera
 select('environment','studio');assert observed()['environment']=='studio' and observed()['camera']==camera
 select('mannequin','none');assert observed()['matrix']==matrix
 select('mannequin','am');select('environment','room')
 check('Room/Studio and AM/AF/hidden controls preserve object dimensions and camera')
 fill('height','150');w.until(lambda _:abs(observed()['height']-reference_height*1.5)<1e-5)
 assert by('viewer').is_displayed() and not by('placement-ack').is_selected();assert observed()['camera']==camera
 ack();fill('rotation','37');w.until(lambda _:observed()['matrix']!=matrix and not by('placement-ack').is_selected())
 w.until(lambda _:'2.82 high' in by('dimensions').text)
 assert observed()['camera']==camera
 Select(d.find_element(By.CSS_SELECTOR,'#object-viewer select')).select_by_value('original');assert observed()['camera']==camera
 Select(d.find_element(By.CSS_SELECTOR,'#object-viewer select')).select_by_value('converted');assert observed()['camera']==camera
 by('fit').click();w.until(lambda _:by('fit').is_enabled() and 'Object fitted' in by('preview-status').text)
 assert observed()['height']<=template_heights['urn'] and not by('placement-warning').is_displayed()
 assert by('fit').get_attribute('aria-pressed') is None and observed()['camera']==camera
 fitted=observed()['height'];before=observed()['matrix'];fill('rotation','0');w.until(lambda _:observed()['matrix']!=before)
 assert observed()['height']==fitted and observed()['camera']==camera
 before=observed()['matrix'];fill('rotation','37');w.until(lambda _:observed()['matrix']!=before)
 assert observed()['height']==fitted
 d.find_element(By.CSS_SELECTOR,'[data-object-height="50"]').click();w.until(lambda _:abs(observed()['height']-reference_height*.5)<1e-5)
 assert by('fit').get_attribute('aria-pressed') is None
 d.find_element(By.CSS_SELECTOR,'[data-object-height="25"]').click();w.until(lambda _:abs(observed()['height']-reference_height*.25)<1e-5)
 assert not by('placement-warning').is_displayed()
 check('Live size/rotation updates retain camera, preset buttons work, acknowledgement invalidates')
 by('preview').location_once_scrolled_into_view;d.save_screenshot(str(out/'scale-room.png'))
 by('build').click();w.until(lambda _:by('download').is_displayed());by('download').click()
 file=downloads/'ScaleBrowser_Floor.package';w.until(lambda _:file.exists() and file.read_bytes()[:4]==b"DBPF")
 assert file.read_bytes()[:4]==b'DBPF'
 saved=store("return (await r.store.listJobs()).find(j=>j.label==='ScaleBrowser_Floor');")
 assert saved['parameters']['sizing_version']==2 and saved['parameters']['target_height']<.5
 assert saved['validation']['object']['layout']['height']==saved['validation']['object']['dimensions']['height']
 check('Actual .package download has the frozen absolute height and layout validation')
 restore_latest();assert float(by('height').get_attribute('value'))==25 and not by('height').is_enabled() and not by('fit').is_enabled()
 assert by('download').is_displayed();observe();preview()
 check('Completed batch restores read-only with pinned engine, sizing and direct download')
 new();select('standard','fruit-bowl');assert abs(float(by('height').get_attribute('value'))-template_heights['fruit-bowl']/reference_height*100)<.01;by('model').send_keys(str(out/'model.glb'));preview()
 assert observed()['table'] and abs(observed()['elevation']-.8)<1e-5
 assert abs(observed()['height']-template_heights['fruit-bowl'])<1e-6
 select('mannequin','af');select('environment','studio');fill('creator','ScaleBrowser');fill('name','Table');fill('title','On a table')
 by('build').click();w.until(lambda _:by('download').is_displayed());by('download').click();w.until(lambda _:(downloads/'ScaleBrowser_Table.package').exists())
 check('Tabletop defaults to its exact original height, reference table is elevated only in preview, package builds')
 new();select('source','upload');by('packages').send_keys(str(file));by('inspect').click();w.until(lambda _:'Template ready' in by('inspection').text)
 by('model').send_keys(str(out/'model.glb'));preview();ack();fill('creator','ScaleBrowser');fill('name','Custom');fill('title','Custom template')
 by('build').click();w.until(lambda _:by('download').is_displayed())
 check('Uploaded custom decoration uses the same absolute height and placement acknowledgement')
 for name,tiles in [('chimes',2),('chi',4),('venus',1)]:
  new();select('standard',name);by('model').send_keys(str(out/'model.glb'));preview();assert f'{tiles} occupied floor tile' in by('dimensions').text
  assert abs(observed()['height']-template_heights[name])<1e-6
  fill('creator','ScaleBrowser');fill('name',name);fill('title','Scale '+name);ack();by('build').click();w.until(lambda _:by('download').is_displayed());by('download').click();w.until(lambda _:(downloads/f'ScaleBrowser_{name}.package').exists())
 check('Pedestal, two-tile Chimes and four-tile My-Chi each preview and download')
 new();by('model').send_keys(str(out/'model.glb'));preview();fill('height','177');select('mannequin','af');select('environment','studio');by('footprint').click();
 w.until(lambda _:'Saved in this browser' in by('status').text);restore_latest()
 assert float(by('height').get_attribute('value'))==177 and Select(by('mannequin')).first_selected_option.get_attribute('value')=='af'
 assert Select(by('environment')).first_selected_option.get_attribute('value')=='studio' and not by('footprint').is_selected()
 check('Draft sizing and display settings autosave and restore after reload')
 d.set_window_size(390,844);observe();preview();d.save_screenshot(str(out/'scale-narrow.png'))
 assert d.execute_script('return document.documentElement.scrollWidth<=innerWidth+2')
 by('height').send_keys(Keys.ARROW_UP);w.until(lambda _:abs(float(by('height').get_attribute('value'))-177.01)<1e-6)
 check('Narrow layout and keyboard height input work')
 # Legacy drafts stay stored and cannot silently acquire the new height convention.
 legacy=store("const a=(await r.store.listJobs()).find(j=>j.state==='draft');return await r.saveDraft({...a,id:r.store.id(),label:'Legacy draft',parameters:{...a.parameters,sizing_version:undefined,target_height:undefined,scale:1}});")
 assert legacy['id']
 # Other tabs remain present and their existing Three.js preview is still available.
 d.find_element(By.CSS_SELECTOR,'[data-tab="package"]').click();w.until(lambda _:d.find_elements(By.CSS_SELECTOR,'.body-preview-stage canvas'))
 d.find_element(By.CSS_SELECTOR,'[data-tab="hair"]').click();d.find_element(By.CSS_SELECTOR,'[data-tab="texture"]').click()
 check('Converter, hair and tattoo tabs remain available, tattoo Three.js renderer initializes')
 assert requests and all(r['method']=='GET' for r in requests)
 check('Network capture contains GET only, package inputs and outputs stay in the browser')
 (out/'sizing-browser-results.json').write_text(json.dumps({'checks':checks,'browser':d.capabilities['browserVersion'],'requests':requests,'download_directory':str(downloads),'runtime_metrics':saved['runtimeMetrics']},indent=2))
except Exception:
 print('STATUS',by('status').text,'PREVIEW',by('preview-status').text,'INSPECTION',by('inspection').text,flush=True)
 d.save_screenshot(str(out/'sizing-failure.png'));(out/'sizing-failure.html').write_text(d.page_source);raise
finally:d.quit()
