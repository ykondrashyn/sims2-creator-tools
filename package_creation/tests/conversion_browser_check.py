"""Firefox local conversion, recovery, storage and download acceptance."""
from pathlib import Path
import os
os.environ.setdefault('OBJECT_BROWSER_ARTIFACTS',str(Path('artifacts/body-conversion/browser').resolve()))
Path(os.environ['OBJECT_BROWSER_ARTIFACTS']).mkdir(parents=True,exist_ok=True)
setup=Path('package_creation/tests/object_browser_check.py').read_text().split('try:\n d.set_window_size')[0]
setup=setup.replace("'application/zip,application/octet-stream'","'application/zip,application/octet-stream,image/png'").replace("o.enable_bidi=True","o.enable_bidi=True; o.set_preference('dom.disable_beforeunload',True)")
exec(setup)
from PIL import Image
url=os.environ.get('CONVERSION_BROWSER_URL','http://192.168.50.213:8002/')
def el(id):return d.find_element(By.ID,id)
def jobs():return store("return (await r.store.listJobs()).filter(j=>j.kind==='conversion');")
def ready():w.until(lambda _: 'Ready to convert' in el('conversion-status').text or 'ready to convert' in el('conversion-status').text)
def open_saved():
 summary=d.find_element(By.CSS_SELECTOR,'#converter-saved summary')
 if not d.find_element(By.CSS_SELECTOR,'#converter-saved details').get_attribute('open'):summary.click()
 buttons=d.find_elements(By.CSS_SELECTOR,'#converter-saved .saved-batch-row button')
 return buttons
try:
 d.set_window_size(1280,1000);d.get(url)
 w.until(lambda _:el('conversion-new').is_enabled())
 assert el('converter-form').find_element(By.CSS_SELECTOR,'input[value="am"]').is_selected()
 assert len(d.find_elements(By.CSS_SELECTOR,'[role="tab"]'))==7
 el('converter-texture').send_keys(str(root/'artifacts/body-conversion/corpus/diagnostic.png'));ready()
 first=jobs()[0];assert first['state']=='draft'
 # A conversion's assets contain just the shared engine and selected profile.
 refs=first['runtimeBlobs'];m=first['manifest']
 assert set(refs)=={f"asset:{m['assets'][n]['sha256']}" for n in ['worker','glue','wasm','conversion:am']}
 el('convert-button').click();w.until(lambda _:el('conversion-download').is_displayed())
 output=downloads/'diagnostic_am_ts2.png';w.until(lambda _:output.exists())
 assert Image.open(output).tobytes()==Image.open(root/'artifacts/body-conversion/corpus/blender/am-diagnostic.png').tobytes()
 w.until(lambda _:d.find_elements(By.CSS_SELECTOR,'#converted-body-preview canvas'))
 assert not el('converter-texture').is_enabled();complete=jobs()[0]
 check('AM browser conversion automatically downloads an exactly matching PNG, with Three.js preview')
 d.save_screenshot(str(out/'firefox-complete.png'))
 d.refresh();w.until(lambda _:d.find_elements(By.CSS_SELECTOR,'#converter-saved summary'));open_saved()[0].click()
 w.until(lambda _:el('conversion-download').is_displayed());assert not el('converter-texture').is_enabled()
 w.until(lambda _:d.find_elements(By.CSS_SELECTOR,'#converted-body-preview canvas'))
 assert jobs()[0]['snapshotHash']==complete['snapshotHash']
 check('Reload restores a completed read-only conversion and its preview')
 el('conversion-new').click();d.find_element(By.CSS_SELECTOR,'#converter-form input[value="af"]').click()
 el('converter-texture').send_keys(str(root/'artifacts/body-conversion/corpus/rgba16.png'));ready()
 el('convert-button').click();w.until(lambda _:el('conversion-cancel').is_displayed());el('conversion-cancel').click()
 w.until(lambda _:el('conversion-retry').is_displayed());cancelled=next(j for j in jobs() if j['parameters']['body']=='af')
 assert cancelled['state']=='cancelled' and not cancelled.get('output')
 el('conversion-retry').click();w.until(lambda _:el('conversion-download').is_displayed());afout=downloads/'rgba16_af_ts2.png';w.until(lambda _:afout.exists())
 assert Image.open(afout).tobytes()==Image.open(root/'artifacts/body-conversion/corpus/blender/af-rgba16.png').tobytes()
 af=next(j for j in jobs() if j['parameters']['body']=='af');assert af['id']==cancelled['id']
 check('AF 16-bit PNG cancellation and retry retain inputs and download exact pixels')
 # Reload while a real conversion is processing, which terminates its worker.
 el('conversion-new').click();el('converter-texture').send_keys(str(root/'artifacts/body-conversion/corpus/random.png'));ready()
 el('convert-button').click();w.until(lambda _:el('conversion-progress').is_displayed() and float(el('conversion-progress').get_attribute('value'))>0)
 interrupted=next(j for j in jobs() if j['files'][0]['filename']=='random.png')
 assert interrupted['state']=='building' and interrupted.get('snapshotHash')
 d.refresh();w.until(lambda _:d.find_elements(By.CSS_SELECTOR,'#converter-saved summary'))
 # pagehide releases a normal reload's lease. A crashed process instead waits for expiry.
 w.until(lambda _:not store('return await r.store.lease();'))
 open_saved()[0].click();w.until(lambda _:el('conversion-retry').is_displayed());assert el('conversion-retry').text=='Resume conversion'
 el('conversion-retry').click();w.until(lambda _:el('conversion-download').is_displayed())
 resumed=store(f"return await r.store.getJob('{interrupted['id']}');")
 assert resumed['snapshotHash']==interrupted['snapshotHash']
 check('Reload during processing stops the worker and resumes the unchanged immutable snapshot')
 w.until(lambda _:not store('return await r.store.lease();'))
 token=store("return await r.store.acquire('conversion-lease-test');")
 original=d.current_window_handle;d.switch_to.new_window('tab');d.get(url)
 conflict=store("try {await r.store.acquire('other-tool');return false;}catch(e){return e.message;}")
 assert 'Another package build' in conflict,conflict
 d.close();d.switch_to.window(original);store(f"await r.store.release('{token}');return true;")
 newer=store("return await r.store.acquire('newer-attempt-test');")
 store(f"await r.store.interrupt('{token}');return true;")
 assert store('return (await r.store.lease()).token;')==newer
 stale=store(f"try{{await r.store.putBlob('stale-output-probe',new Blob(['x']),'{token}');return false;}}catch(e){{return e.message;}}")
 assert 'interrupted or replaced' in stale
 store(f"await r.store.release('{newer}');return true;")
 check('Conversion shares the origin-wide lease with other tabs and package tools')
 # Failure injection is confined to this disposable Firefox profile.
 quota=store("""const db=await new Promise(resolve=>{const q=indexedDB.open('sims2-creator-packages',1);q.onsuccess=()=>resolve(q.result);});
 await new Promise((resolve,reject)=>{const t=db.transaction('meta','readwrite');t.objectStore('meta').put({id:'usage',bytes:r.store.BUDGET});t.oncomplete=resolve;t.onerror=()=>reject(t.error);});db.close();
 try{await r.store.putBlob('quota-probe',new Blob(['x']));return false;}catch(e){return e.message;}finally{await r.store.cleanup();}""")
 assert '2 GiB limit' in quota,quota
 assert store(f"return (await r.store.getJob('{complete['id']}')).state;")=='complete'
 check('Quota failure preserves saved inputs and completed conversions')
 damaged=store("const j=(await r.store.listJobs()).find(j=>j.kind==='conversion');const copy=await r.saveDraft({kind:'conversion',template:j.template,files:j.files,manifest:j.manifest,parameters:j.parameters});await r.store.putBlob(copy.runtimeBlobs.find(k=>k==='asset:'+copy.manifest.assets['conversion:'+copy.parameters.body].sha256),new Blob(['damaged']));return copy;")
 error=store(f"try{{await r.restore('{damaged['id']}');return false;}}catch(e){{return e.message;}}")
 assert 'damaged' in error.lower(),error
 assert store(f"return !!(await r.store.getJob('{damaged['id']}'));")
 # Repair test asset using already verified local HTTP asset, for following tests.
 store(f"const j=await r.store.getJob('{damaged['id']}');const a=j.manifest.assets['conversion:'+j.parameters.body];await r.store.putBlob('asset:'+a.sha256,await (await fetch(a.url)).blob());return true;")
 store(f"await r.store.removeJob('{damaged['id']}');return true;")
 check('Damaged pinned assets produce a recovery error without deleting the conversion')
 el('conversion-new').click();invalid=out/'invalid.png';Image.new('RGBA',(8,8),(1,2,3,128)).save(invalid)
 el('converter-texture').send_keys(str(invalid));w.until(lambda _:'1024×2048' in el('conversion-status').text)
 assert not el('conversion-download').is_displayed()
 check('Invalid image dimensions are rejected locally')
 d.set_window_size(390,844);assert d.execute_script('return document.documentElement.scrollWidth<=innerWidth+2');d.save_screenshot(str(out/'firefox-narrow.png'))
 check('Conversion controls fit a narrow viewport')
 # Existing tabs still initialize their browser interfaces.
 for tab,selector in [('package','#tattoo-body-preview'),('hair','#hair-template-select'),('object','#object-standard')]:
  d.find_element(By.CSS_SELECTOR,f'[data-tab="{tab}"]').click();w.until(lambda _:d.find_element(By.CSS_SELECTOR,selector).is_displayed())
 d.find_element(By.CSS_SELECTOR,'[data-tab="package"]').click();w.until(lambda _:d.find_elements(By.CSS_SELECTOR,'#tattoo-body-preview canvas'))
 check('Tattoo, hair and object tabs initialize, including the tattoo Three.js viewer')
 assert requests and all(r['method']=='GET' for r in requests),requests
 loaded={r['url'].split('/')[-1] for r in requests};assert m['assets']['palette']['sha256'] not in loaded
 check('Network capture contains only GET requests, with no source/output uploads and no hair palette fetch')
 # Save an idle profile, close Firefox, then launch another process with it.
 import shutil
 profile=Path(d.capabilities['moz:profile']);backup=out/'restart-profile'
 if backup.exists():shutil.rmtree(backup)
 d.get('about:blank');shutil.copytree(profile,backup,ignore=shutil.ignore_patterns('lock','.parentlock'))
 d.quit()
 restarted=Options();restarted.add_argument('-headless');restarted.add_argument('-no-remote');restarted.binary_location='/Applications/Firefox.app/Contents/MacOS/firefox';restarted.profile=str(backup)
 d=webdriver.Firefox(options=restarted,service=Service(log_output=str(out/'firefox-restart.log')));w=WebDriverWait(d,90);d.set_script_timeout(90)
 d.get(url);w.until(lambda _:d.find_elements(By.CSS_SELECTOR,'#converter-saved summary'));open_saved()[0].click();w.until(lambda _:el('conversion-download').is_displayed())
 assert any(j['id']==complete['id'] for j in jobs())
 w.until(lambda _:d.find_elements(By.CSS_SELECTOR,'#converted-body-preview canvas'))
 check('A new Firefox process restores saved conversions and their preview')
 d.refresh();w.until(lambda _:d.find_elements(By.CSS_SELECTOR,'#converter-saved summary'))
 d.execute_script("const original=HTMLCanvasElement.prototype.getContext;HTMLCanvasElement.prototype.getContext=function(kind,...args){return kind.startsWith('webgl')?null:original.call(this,kind,...args);};")
 open_saved()[0].click();w.until(lambda _:el('conversion-download').is_displayed());w.until(lambda _:el('converted-preview-error').is_displayed())
 assert el('conversion-download').get_attribute('href').startswith('blob:')
 check('A WebGL preview failure leaves the validated PNG download available')
 before=len(jobs());open_saved()[-1].click();w.until(lambda _:len(jobs())==before-1)
 check('Explicit Delete removes only the selected conversion')
 (out/'firefox-results.json').write_text(json.dumps({'checks':checks,'browser':d.capabilities['browserVersion'],'requests':requests,'download_directory':str(downloads),'am':complete['runtimeMetrics'],'af':af['runtimeMetrics']},indent=2))
except Exception:
 print('STATUS',el('conversion-status').text,flush=True);d.save_screenshot(str(out/'firefox-failure.png'));(out/'firefox-failure.html').write_text(d.page_source);raise
finally:d.quit()
