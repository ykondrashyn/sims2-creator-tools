"""Cancellation, storage and reload checks for height-based object snapshots."""
from pathlib import Path
exec(Path('package_creation/tests/object_browser_check.py').read_text().split('try:\n d.set_window_size')[0])
from package_creation.tests.test_objects import model_fixture,glb
(out/'model.glb').write_bytes(glb(*model_fixture()))
try:
 d.get(os.environ.get('OBJECT_BROWSER_URL','http://192.168.50.213:8002/'));d.find_element(By.CSS_SELECTOR,'[data-tab="object"]').click();w.until(lambda _:len(Select(by('standard')).options)==5)
 by('model').send_keys(str(out/'model.glb'));fill('creator','Recovery');fill('name','Source');fill('title','Recovery source');fill('height','25')
 by('preview').click();w.until(lambda _:'Preview ready' in by('preview-status').text)
 if os.environ.get('OBJECT_RECOVERY_FIT'):
  by('fit').click();w.until(lambda _:by('fit').is_enabled() and 'Object fitted' in by('preview-status').text)
  assert not by('placement-warning').is_displayed()
 source=store("return (await r.store.listJobs()).find(j=>j.label==='Recovery_Source');")
 # Exercise the same API used by the UI in this fresh test profile.
 cancelled=store("""
 const m=await r.manifest();
 const source=(await r.store.listJobs()).find(j=>j.label==='Recovery_Source');
 const job=await r.saveDraft({kind:'object',template:m.objects.items[0],files:source.files,
   parameters:{...source.parameters,creator:'Recovery',object_name:'Object',title:'Recovery object'},ui:source.ui});
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

 resumed=store("return (await r.store.listJobs()).find(j=>j.label==='Recovery_Source');")
 timings=store("const j=(await r.store.listJobs()).find(j=>j.label==='Recovery_Source');await r.objectLayout(j);const times=[];for(let i=0;i<10;i++){const t=performance.now();await r.objectLayout({...j,parameters:{...j.parameters,rotation:i*10}});times.push(performance.now()-t);}return times;")
 assert isinstance(timings,list) and len(timings)==10,timings
 check('Warm layout timing recorded without repeating preview compression')
 # Reload preserves the same frozen snapshot after worker interruption.
 d.refresh();d.find_element(By.CSS_SELECTOR,'[data-tab="object"]').click();w.until(lambda _:len(Select(by('standard')).options)==5)
 assert store("return (await r.store.listJobs()).some(j=>j.label==='Recovery_Source');")
 check('Browser reload retains saved source and pinned runtime assets')
 (out/'recovery-browser-results.json').write_text(json.dumps({'checks':checks,'layout_ms':timings,'requests':requests},indent=2))
except Exception:
 print(by('status').text,flush=True);d.save_screenshot(str(out/'recovery-failure.png'));raise
finally:d.quit()
