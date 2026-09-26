"""Actual Firefox optimization, storage and package downloads on the LAN site."""
from pathlib import Path
exec(Path("package_creation/tests/object_browser_check.py").read_text().split("try:\n d.set_window_size")[0])
import subprocess
import threading
from package_creation.tests.test_objects import model_fixture, glb

url = os.environ.get("OBJECT_BROWSER_URL", "http://192.168.50.213:8002/")
source = Path(os.environ.get("OBJECT_OPTIMIZER_MODEL", Path.home() / "Downloads/ronald_mcdonald.glb"))
source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
server = int(subprocess.check_output(['lsof','-ti','tcp:'+str(urlsplit(url).port),'-sTCP:LISTEN']))
stop = threading.Event()
children = []
def sample_children():
 while not stop.wait(.2):
  rows = subprocess.check_output(['ps','-axo','pid,ppid,comm'],text=True).splitlines()[1:]
  for row in rows:
   fields = row.split(None,2)
   if len(fields)==3 and int(fields[1])==server:children.append(fields)
thread = threading.Thread(target=sample_children,daemon=True)
thread.start()
doc, binary = model_fixture()
(out / "fixture.glb").write_bytes(glb(doc, binary))
import zipfile
doc["buffers"][0]["uri"] = "model.bin"
with zipfile.ZipFile(out / "fixture.zip", "w") as z:
 z.writestr("nested/scene.gltf", json.dumps(doc))
 z.writestr("nested/model.bin", binary)
try:
 d.set_window_size(1280,1250)
 d.get(url)
 w.until(lambda _:d.find_element(By.CSS_SELECTOR,'[data-tab="object"]').is_displayed())
 d.find_element(By.CSS_SELECTOR,'[data-tab="object"]').click()
 w.until(lambda _:len(Select(by('standard')).options)==2)
 assert not d.find_elements(By.ID,'object-mode')
 select('standard','urn')
 assert not by('optimize').is_enabled()
 by('model').send_keys(str(source));w.until(lambda _:by('optimize').is_enabled())
 by('optimize').click()
 w.until(lambda _:'Using the optimized model' in by('optimization-status').text or 'worker stopped' in by('optimization-status').text)
 assert 'Using the optimized model' in by('optimization-status').text,by('optimization-status').text
 check('Original Ronald GLB optimizes through the website button under the production CSP')
 by('preview').click();w.until(lambda _:by('preview').is_enabled() and 'Preview ready' in by('preview-status').text)
 assert not by('creator').get_attribute('value') and not by('title').get_attribute('value')
 partial=store("return (await r.store.listJobs()).find(j=>j.label==='Creator_Object');")
 assert partial['parameters']['creator']=='' and partial['parameters']['mode']=='model'
 assert partial['parameters']['scale']==1
 check('Model previews before catalog details are filled and preview defaults do not overwrite the draft')
 fill('creator','Optimizer');fill('name','Ronald');fill('title','Ronald McDonald statue')
 fill('description','Original model by patricknc08, CC BY 4.0. https://sketchfab.com/3d-models/ronald-mcdonald-624d5f67ca6e4ed3ba1e4044da7f0d8a . Geometry optimized in the browser.')
 by('preview').click();w.until(lambda _:by('preview').is_enabled() and 'Preview ready' in by('preview-status').text)
 d.find_element(By.CSS_SELECTOR,'#object-viewer canvas').screenshot(str(out/'ronald-preview.png'))
 d.save_screenshot(str(out/'firefox-page.png'))
 job=store("return (await r.store.listJobs()).find(j=>j.label==='Optimizer_Ronald');")
 assert job['optimization']['after']['groups']==9
 assert job['optimization']['after']['triangles']<30000
 assert job['modelSource']['sha256']==source_hash
 original_optimized_hash=job['optimization']['file']['sha256']
 check('Optimized Ronald previews with 9 groups and retained source hash')
 by('optimized-download').click()
 optimized=downloads/'ronald_mcdonald_optimized.glb';w.until(lambda _:optimized.exists())
 assert hashlib.sha256(optimized.read_bytes()).hexdigest()==original_optimized_hash
 check('Real optimized GLB download matches the saved result')
 # Saved source stays available after cleanup and page reload.
 store("await r.store.cleanup();return true;")
 d.refresh();d.find_element(By.CSS_SELECTOR,'[data-tab="object"]').click()
 w.until(lambda _:d.find_elements(By.CSS_SELECTOR,'#object-saved summary'))
 d.find_element(By.CSS_SELECTOR,'#object-saved summary').click()
 w.until(lambda _:d.find_elements(By.CSS_SELECTOR,'#object-saved .saved-batch-row button'))
 d.find_element(By.CSS_SELECTOR,'#object-saved .saved-batch-row button').click()
 w.until(lambda _:'Using the optimized model' in by('optimization-status').text)
 assert by('optimized-download').is_displayed()
 by('use-original').click();w.until(lambda _:'Using the original model' in by('status').text)
 restored=store("return (await r.store.listJobs()).find(j=>j.label==='Optimizer_Ronald');")
 assert next(f for f in restored['files'] if f['name']=='model.glb')['sha256']==source_hash
 check('Reload restores optimization and Use original model restores the exact source')
 # Cancel promptly, then repeat using the same source and settings.
 by('optimize').click();w.until(lambda _:by('optimize-cancel').is_displayed());by('optimize-cancel').click()
 w.until(lambda _:'cancelled' in by('optimization-status').text)
 w.until(lambda _:not store('return await r.store.lease();'))
 assert not store("return (await r.store.listJobs()).find(j=>j.label==='Optimizer_Ronald').optimization;")
 by('optimize').click();w.until(lambda _:'Using the optimized model' in by('optimization-status').text)
 retry=store("return (await r.store.listJobs()).find(j=>j.label==='Optimizer_Ronald');")
 assert retry['optimization']['file']['sha256']==original_optimized_hash
 check('Cancellation releases the lease and retry produces identical optimized bytes')
 by('build').click();w.until(lambda _:by('download').is_displayed());by('download').click()
 package=downloads/'Optimizer_Ronald.package';w.until(lambda _:package.exists())
 complete=store("return (await r.store.listJobs()).find(j=>j.label==='Optimizer_Ronald');")
 assert package.read_bytes()[:4]==b'DBPF'
 assert hashlib.sha256(package.read_bytes()).hexdigest()==complete['validation']['object']['sha256']
 assert b'patricknc08' in package.read_bytes()
 assert not by('optimize').is_enabled() and not by('use-original').is_enabled()
 check('Optimized model builds and downloads a validated package with creator credits')
 d.set_window_size(390,844)
 assert d.execute_script('return document.documentElement.scrollWidth<=innerWidth+2')
 d.save_screenshot(str(out/'firefox-narrow.png'))
 check('Optimization controls fit a narrow screen and completed batches are read-only')
 by('new').click();w.until(lambda _:by('creator').is_enabled())
 by('model').send_keys(str(out/'fixture.zip'))
 by('optimize').click();w.until(lambda _:'Using the optimized model' in by('optimization-status').text)
 fill('creator','Optimizer');fill('name','Zip');fill('title','ZIP model')
 by('preview').click();w.until(lambda _:by('preview').is_enabled() and 'Preview ready' in by('preview-status').text)
 check('A glTF ZIP with nested local buffers and alpha-cutout texture also optimizes and previews')
 by('model').send_keys(str(out/'fixture.glb'))
 assert not by('optimized-download').is_displayed()
 by('preview').click();w.until(lambda _:by('preview').is_enabled() and 'Preview ready' in by('preview-status').text)
 changed=store("return (await r.store.listJobs()).find(j=>j.label==='Optimizer_Zip');")
 assert not changed.get('optimization') and not changed.get('modelSource')
 check('Selecting another source clears the previous optimization')
 d.find_element(By.CSS_SELECTOR,'.object-optimization summary').click()
 fill('target-triangles','0')
 assert d.execute_script("return document.getElementById('object-form').checkValidity()")
 assert not d.execute_script("return document.getElementById('object-target-triangles').checkValidity()")
 fill('target-triangles','20000')
 check('Optimization settings do not block ordinary package creation')
 # A competing tab/build owns the same lease.
 token=store("return await r.store.acquire('other-tab-test');")
 by('optimize').click();w.until(lambda _:'Another package build' in by('status').text)
 store(f"await r.store.release('{token}');return true;")
 check('Optimizer respects the shared build lease')
 assert all(r['method']=='GET' for r in requests)
 assert not any('example.com' in r['url'] for r in requests)
 assert not children,children
 check('All requests are GET requests and package actions launch no server processes')
 assert hashlib.sha256(source.read_bytes()).hexdigest()==source_hash
 (out/'firefox-results.json').write_text(json.dumps({'browser':d.capabilities['browserVersion'],'checks':checks,'requests':requests,'job':complete,'server_children':children,'downloads':str(downloads)},indent=2))
except Exception:
 print('STATUS',by('status').text,flush=True)
 print('PREVIEW',by('preview-status').text,flush=True)
 print('OPTIMIZATION',by('optimization-status').text,flush=True)
 d.save_screenshot(str(out/'firefox-failure.png'))
 raise
finally:
 stop.set();thread.join(timeout=2)
 d.quit()
