"""Real Firefox one-time fitting, old drafts and fixed-size package checks."""
from pathlib import Path
exec(Path('package_creation/tests/object_browser_check.py').read_text().split('try:\n d.set_window_size')[0])
from urllib.request import urlopen
from package_creation.tests.test_objects import model_fixture,glb
url=os.environ.get('OBJECT_BROWSER_URL','http://192.168.50.213:8002/')
with urlopen(url+'api/v1/package-runtime/manifest') as response: latest=json.load(response)
previous=json.loads(Path(os.environ.get('OBJECT_PREVIOUS_MANIFEST','artifacts/object-fixed-fit/previous-manifest.json')).read_text())
def current():return store("return (await r.store.listJobs()).find(j=>j.label==='FloorFit_Ronald');")
def preview():
 by('preview').click();w.until(lambda _:'Preview ready' in by('preview-status').text and by('preview').is_enabled())
def fit():
 by('fit').click();w.until(lambda _:by('fit').is_enabled() and 'Object fitted' in by('preview-status').text)
 assert not by('placement-warning').is_displayed(),by('placement-message').text
 assert by('fit').get_attribute('aria-pressed') is None
def restore():
 d.refresh();w.until(lambda _:d.find_element(By.CSS_SELECTOR,'[data-tab="object"]').is_displayed());d.find_element(By.CSS_SELECTOR,'[data-tab="object"]').click()
 w.until(lambda _:d.find_elements(By.CSS_SELECTOR,'#object-saved summary'));d.find_element(By.CSS_SELECTOR,'#object-saved summary').click()
 w.until(lambda _:d.find_elements(By.CSS_SELECTOR,'#object-saved .saved-batch-row button'));d.find_element(By.CSS_SELECTOR,'#object-saved .saved-batch-row button').click()
 w.until(lambda _:'Template ready' in by('inspection').text)
def rotate(angle):
 before=by('dimensions').text;fill('rotation',str(angle));w.until(lambda _:by('dimensions').text!=before)
 w.until(lambda _:current()['parameters']['rotation']==angle)
try:
 d.set_window_size(1280,1100);d.get(url);w.until(lambda _:d.find_element(By.CSS_SELECTOR,'[data-tab="object"]').is_displayed());d.find_element(By.CSS_SELECTOR,'[data-tab="object"]').click()
 w.until(lambda _:len(Select(by('standard')).options)==5)
 by('model').send_keys(str(root/'artifacts/object-optimizer/ronald_browser_optimized.glb'))
 fill('creator','FloorFit');fill('name','Ronald');fill('title','Ronald floor fit');preview()
 original=current()
 # Reproduce an old automatic-fit draft with its last exact height saved.
 seeded=store("const j=(await r.store.listJobs()).find(j=>j.label==='FloorFit_Ronald');const old={...j,revision:j.revision+1,manifest:"+json.dumps(previous)+",parameters:{...j.parameters,rotation:37,fit_to_template:true,height_from_fit:false},ui:{...j.ui,rotation:37,height_mode:'fit'}};const layout=await r.objectLayout(old);old.parameters.target_height=layout.height;return await r.saveDraft(old);")
 assert seeded.get('manifest',{}).get('release')==previous['release'],seeded
 old_height=seeded['parameters']['target_height'];restore();job=current()
 assert job['id']==original['id'] and job['files']==original['files']
 assert job['manifest']['release']==latest['release']
 assert job['parameters']['target_height']==old_height and job['parameters']['height_from_fit'] and not job['parameters']['fit_to_template']
 preview();check('Old automatic-fit draft reopens at its exact saved height with input files and ID preserved')
 for angle in [0,90,37]:
  rotate(angle);job=current()
  assert job['parameters']['target_height']==old_height
  report=store("return await r.objectLayout((await r.store.listJobs()).find(j=>j.label==='FloorFit_Ronald'));")
  assert report['height']==old_height and not report['fit_to_template']
  assert by('placement-warning').is_displayed()==(angle!=37)
 check('Ronald retains height through rotations and only the overhang warning changes')
 rotate(0);fit();assert current()['parameters']['target_height']<old_height
 fitted=current()['parameters']['target_height'];rotate(37)
 assert current()['parameters']['target_height']==fitted
 fit();assert current()['parameters']['target_height']>fitted
 check('Only another explicit Fit click changes the selected height')
 rotate(0);snapshot=current();restore();assert current()['parameters']['target_height']==snapshot['parameters']['target_height']
 preview();assert by('placement-warning').is_displayed()
 by('build').click();w.until(lambda _:'acknowledge' in by('status').text)
 assert not by('download').is_displayed()
 by('placement-ack').click();by('build').click();w.until(lambda _:by('download').is_displayed());by('download').click()
 output=downloads/'FloorFit_Ronald.package';w.until(lambda _:output.exists() and output.read_bytes()[:4]==b'DBPF')
 complete=current();report=complete['validation']['object']
 assert not report['layout']['fit_to_template'] and report['layout']['height_from_fit'] and report['layout']['requires_acknowledgement']
 assert report['dimensions']['height']==complete['parameters']['target_height']==snapshot['parameters']['target_height']
 restore();assert not by('fit').is_enabled() and by('download').is_displayed()
 check('Reload and acknowledged package download preserve height, and completed output restores read-only')
 # Center the action after restoring a batch, before Firefox scrolls from Saved batches.
 d.execute_script("arguments[0].scrollIntoView({block:'center',behavior:'instant'})",by('new'))
 w.until(lambda _:by('new').is_displayed() and by('new').is_enabled())
 by('new').click();w.until(lambda _:by('creator').is_enabled());select('source','upload');by('packages').send_keys(str(output));by('inspect').click()
 w.until(lambda _:'Template ready' in by('inspection').text and by('inspect').is_enabled())
 doc,binary=model_fixture();doc['nodes'][0]['scale']=[200,1,6];wide=out/'wide.glb';wide.write_bytes(glb(doc,binary))
 by('model').send_keys(str(wide));fill('creator','Small');fill('name','Fitted');fill('title','Small fitted model');preview();fit()
 height=float(by('height').get_attribute('value'));assert 0<height<10
 before=by('dimensions').text;fill('rotation','37');w.until(lambda _:by('dimensions').text!=before)
 assert float(by('height').get_attribute('value'))==height
 if by('placement-warning').is_displayed():by('placement-ack').click()
 by('build').click();w.until(lambda _:by('download').is_displayed())
 small=store("return (await r.store.listJobs()).find(j=>j.label==='Small_Fitted');")
 assert small['validation']['object']['dimensions']['height']==small['parameters']['target_height']
 check('Custom template and a fit below 10% keep their chosen height during rotation and generation')
 d.set_window_size(390,844);by('fit').location_once_scrolled_into_view;d.save_screenshot(str(out/'floor-fit-narrow.png'))
 assert d.execute_script('return document.documentElement.scrollWidth<=innerWidth')
 assert all(r['method']=='GET' for r in requests)
 check('Narrow controls remain usable and package traffic is GET-only')
 (out/'floor-fit-browser-results.json').write_text(json.dumps({'checks':checks,'requests':requests,'ronald':report,'download':str(output),'small':small['validation']['object']['dimensions']},indent=2))
except Exception:
 print(by('status').text,by('inspection').text,by('preview-status').text,flush=True);d.save_screenshot(str(out/'floor-fit-failure.png'));raise
finally:d.quit()
