"""Experimental body exports are not complete replacement Sims or gameplay proof."""
import copy
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from package_creation.tests.test_guided_sims import BINARY, ASSETS, native, humanoid
from package_creation.tests.test_objects import glb, model_fixture

@unittest.skipUnless(BINARY.exists(), 'Build the native Sim example')
class ExperimentalBody(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        self.model=self.root/'model.glb';d,b=model_fixture();self.model.write_bytes(glb(d,b));humanoid(self.model)
        a=native('sim_align',{'model':'model.glb','reference':'reference','include_images':False},self.model)
        self.job={'id':'experimental-native-test-123','creator':'Test','sim_name':'Body','body':'am','model_file':'model.glb','guided_version':2,'alignment':[0,0,0],'markers':a['targets'],'neck_height':1.64,'roles':{a['parts'][1]['id']:'head'},'review':dict.fromkeys(['align','markers','head','check'],True),'experimental_everyday':True}
        self.assets={'model.glb':str(self.model),'sim-reference':str(ASSETS/'am-rig.package'),'sim-everyday-template':str(ASSETS/'am-everyday-test.package')}
    def call(self,op,p,output=None,assets=None):
        r={'op':op,'assets':assets or self.assets,'params':p}
        if output:r['output']=str(output)
        done=subprocess.run([str(BINARY)],input=json.dumps(r),text=True,capture_output=True,timeout=180)
        if done.returncode:raise ValueError(done.stderr)
        return json.loads(done.stdout)
    def test_embedded_body_stock_head_and_retry(self):
        path=self.root/'body.package';report=self.call('sim_experimental_build',self.job,path)
        self.assertTrue(report['validated']);self.assertEqual(report['gameplay'],'not_tested');self.assertEqual(report['body_geometry_resources'],1)
        self.assertLess(report['bounds']['max'][2],1.65)
        self.assertEqual(report,self.call('sim_experimental_build',self.job,self.root/'retry.package'))
        self.assertEqual(path.read_bytes(),(self.root/'retry.package').read_bytes())
        result=self.call('fixture_report',{'asset':'p'},assets={'p':str(path)})
        source=self.call('fixture_report',{'asset':'sim-everyday-template'})
        before={n['key']:n for n in source['resources']};after={n['key']:n for n in result['resources']}
        # All original private face/geometry/material resources survive unchanged.
        for k,v in before.items():
            if k.split('-')[1]=='ffffffff' and not k.startswith('ac506764'):
                self.assertEqual(v['sha256'],after[k]['sha256'])
        shapes=[n for n in result['resources'] if n['key'].startswith('fc6eb1f7') and n['key'].split('-')[1]!='ffffffff']
        self.assertEqual(len(shapes),1)
        gmnds={f"##0x{n['key'].split('-')[1]}!{n['name']}" for n in result['resources'] if n['key'].startswith('7ba3838c')}
        self.assertTrue(set(shapes[0]['geometry']) <= gmnds)
        outfit=next(n['properties'] for n in result['resources'] if n['key'].startswith('ebcf3e27') and n['key'].split('-')[1]!='ffffffff')
        self.assertEqual((outfit['age'],outfit['category']),(8,1));self.assertEqual(outfit['numoverrides'],report['subsets'])
        changed={**self.job,'id':'another-experimental-batch-123'}
        other=self.call('sim_experimental_build',changed,self.root/'other.package')
        self.assertNotEqual(report['sha256'],other['sha256'])
    def test_scope_and_review_rejections(self):
        for key,value,message in [('experimental_everyday',False,'Acknowledge'),('body','af','Adult Male'),('review',{},'Finish the fit')]:
            with self.subTest(key=key),self.assertRaisesRegex(ValueError,message):self.call('sim_experimental_build',{**self.job,key:value})
        with self.assertRaisesRegex(ValueError,'persistent-head'):self.call('sim_build',self.job)
    def test_empty_body_is_rejected(self):
        aligned=native('sim_align',{'model':'model.glb','reference':'reference','include_images':False},self.model)
        job=copy.deepcopy(self.job);job['roles']={p['id']:'head' for p in aligned['parts']}
        with self.assertRaisesRegex(ValueError,'No body triangles'):self.call('sim_experimental_build',job)
