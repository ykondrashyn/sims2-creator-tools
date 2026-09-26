"""Guided fitting structural checks. These do not grant gameplay acceptance."""
import copy
import json
import math
import os
from pathlib import Path
from package_creation.tests.artifacts import required_artifact, fixture_root
import struct
import subprocess
import tempfile
import unittest
from package_creation.tests.test_objects import glb, model_fixture

ROOT=Path(__file__).resolve().parents[2]
BINARY = required_artifact("SIM_GUIDED_BINARY")
ASSETS=fixture_root()/'package_creation/sims/assets'


def native(op, params, model, body='am', output=None):
    request={'op':op,'params':params,'assets':{'model.glb':str(model),'reference':str(ASSETS/f'{body}-rig.package')}}
    if output:request.update(output=str(output),output_asset='sim-buffer')
    p=subprocess.run([str(BINARY)],input=json.dumps(request),text=True,capture_output=True,timeout=600)
    if p.returncode:raise ValueError(p.stderr)
    return json.loads(p.stdout)


def humanoid(path):
    r=native('sim_reference',{'asset':'reference'},path)
    doc,data=model_fixture()
    materials=copy.deepcopy(doc['materials'])
    images=copy.deepcopy(doc['images'])
    # Keep the fixture's embedded material images, append the real rigged body geometry.
    views=copy.deepcopy(doc['bufferViews']);accessors=copy.deepcopy(doc['accessors']);data=bytearray(data)
    def array(values,kind,components):
        while len(data)%4:data.append(0)
        offset=len(data);flat=[x for row in values for x in row] if components>1 else values
        data.extend(struct.pack('<'+('f' if kind==5126 else 'I')*len(flat),*flat))
        view=len(views);views.append({'buffer':0,'byteOffset':offset,'byteLength':len(data)-offset})
        a={'bufferView':view,'componentType':kind,'count':len(values),'type':{1:'SCALAR',2:'VEC2',3:'VEC3'}[components]}
        if components==3:a.update(min=[min(v[i] for v in values) for i in range(3)],max=[max(v[i] for v in values) for i in range(3)])
        accessors.append(a);return len(accessors)-1
    body=r['positions'];tri=r['indices'];head=[[-.09,-.07,1.65],[.09,-.07,1.65],[.09,.09,1.65],[-.09,.09,1.65],[-.09,-.07,1.8788737],[.09,-.07,1.8788737],[.09,.09,1.8788737],[-.09,.09,1.8788737]]
    headtri=[0,2,1,0,3,2,4,5,6,4,6,7,0,1,5,0,5,4,1,2,6,1,6,5,2,3,7,2,7,6,3,0,4,3,4,7]
    primitives=[]
    for i,(positions,indices) in enumerate([(body,tri),(head,headtri)]):
        # Inverse of the importer game-coordinate transform.
        positions=[[-p[0],p[2],p[1]] for p in positions]
        primitives.append({'attributes':{'POSITION':array(positions,5126,3),'TEXCOORD_0':array([[.5,.5] for p in positions],5126,2)},'indices':array(indices,5125,1),'material':i})
    doc.update(bufferViews=views,accessors=accessors,meshes=[{'name':'GuidedHumanoid','primitives':primitives}],nodes=[{'mesh':0}],scenes=[{'nodes':[0]}],scene=0,buffers=[{'byteLength':len(data)}],materials=materials,images=images)
    path.write_bytes(glb(doc,bytes(data)))


def values(raw,spec):
    return struct.unpack_from('<'+('f' if spec['type']=='f32' else 'I')*spec['length'],raw,spec['offset'])


@unittest.skipUnless(BINARY.exists(),'Build the guided Sim example')
class GuidedSims(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name);self.model=self.root/'model.glb'
        # The native wrapper loads a model asset for reference-only calls too.
        doc,data=model_fixture();self.model.write_bytes(glb(doc,data));humanoid(self.model)
        self.p={'model':'model.glb','reference':'reference','alignment':[0,0,0],'include_images':False}
    def aligned(self,body='am'):
        return native('sim_align',self.p,self.model,body,self.root/'before.bin')
    def test_hierarchy_alignment_and_rotations(self):
        for body in ['am','af']:
            r=self.aligned(body);parents=r['reference']['parents'];self.assertEqual(parents[7],6)
            for bone in range(60,65):self.assertEqual(parents[bone],7)
            self.assertEqual(parents[27],26);self.assertEqual(parents[46],45)
            self.assertEqual(len(r['targets']),16)
            for angles in [[0,0,90],[90,0,0],[0,90,0]]:
                rotated=native('sim_align',{**self.p,'alignment':angles},self.model,body)
                self.assertAlmostEqual(rotated['height'],r['height'])
    def test_invalid_markers_and_confirmation(self):
        a=self.aligned();p={**self.p,'markers':a['targets']}
        self.assertTrue(native('sim_landmarks',p,self.model)['valid'])
        for key,other in [('l_knee','l_hip'),('l_wrist','r_wrist'),('neck','pelvis')]:
            bad=copy.deepcopy(p);bad['markers'][key]=bad['markers'][other]
            self.assertFalse(native('sim_landmarks',bad,self.model)['valid'])
        with self.assertRaisesRegex(ValueError,'Review alignment'):
            native('sim_guided_fit',{**p,'guided_version':2},self.model)
    def test_body_fit_weights_morphs_head_and_buffers(self):
        for body in ['am','af']:
            a=self.aligned(body);marks=copy.deepcopy(a['targets']);marks['l_elbow'][0]*=1.13;marks['l_wrist'][0]*=1.13
            p={**self.p,'guided_version':2,'markers':marks,'neck_height':1.64,'review':{'align':True,'markers':True,'head':True},'roles':{a['parts'][1]['id']:'head'}}
            r=native('sim_guided_fit',p,self.model,body,self.root/'fit.bin');raw=(self.root/'fit.bin').read_bytes()
            repeated=native('sim_guided_fit',p,self.model,body,self.root/'repeat.bin');self.assertEqual(r,repeated);self.assertEqual(raw,(self.root/'repeat.bin').read_bytes())
            before=(self.root/'before.bin').read_bytes()
            for i,part in enumerate(r['parts']):
                for key in ['indices','uvs']:self.assertEqual(values(raw,part[key]),values(before,a['parts'][i][key]))
                weights=values(raw,part['weights']);self.assertTrue(all(math.isfinite(w) and w>=0 for w in weights))
                for k in range(0,len(weights),4):self.assertAlmostEqual(sum(weights[k:k+4]),1,places=5)
                self.assertEqual({m['name'] for m in part['morphs']},{'fatbot','pregbot'})
                if i==1:
                    v=values(raw,part['positions']);orig=values(before,a['parts'][i]['positions'])
                    for k in range(3,len(v),3):
                        self.assertAlmostEqual(math.dist(v[:3],v[k:k+3]),math.dist(orig[:3],orig[k:k+3]),places=5)
                    self.assertTrue(all(j==7 for j in values(raw,part['joints'])[::4]))
            self.assertEqual(r['gameplay'],'not_tested')
    def test_connected_pieces_sharing_one_material(self):
        doc, _=model_fixture()
        pos=[(0,0,0),(1,0,0),(0,1,0),(0,0,1),(0,2,0),(1,2,0),(0,3,0),(0,2,1)]
        uv=[0.5]*16
        indices=[0,2,1,0,1,3,0,3,2,1,2,3,4,6,5,4,5,7,4,7,6,5,6,7]
        raw=struct.pack('<24f',*(v for p in pos for v in p))+struct.pack('<16f',*uv)+struct.pack('<24H',*indices)
        doc['bufferViews']=[{'buffer':0,'byteOffset':0,'byteLength':96},{'buffer':0,'byteOffset':96,'byteLength':64},{'buffer':0,'byteOffset':160,'byteLength':48}]
        doc['accessors'][0].update(count=8,min=[0,0,0],max=[1,3,1]);doc['accessors'][1]['count']=8;doc['accessors'][2]['count']=24
        doc['meshes'][0]['primitives']=doc['meshes'][0]['primitives'][:1]
        doc['buffers'][0]['byteLength']=len(raw);self.model.write_bytes(glb(doc,raw))
        a=self.aligned();self.assertEqual(a['parts'][0]['component_count'],2)
        components=values((self.root/'before.bin').read_bytes(),a['parts'][0]['components'])
        self.assertEqual(components,(0,0,0,0,1,1,1,1))
        p={**self.p,'guided_version':2,'markers':a['targets'],'neck_height':1.7,'review':{'align':True,'markers':True,'head':True},'roles':{a['parts'][0]['id']+'#1':'head'}}
        r=native('sim_guided_fit',p,self.model,output=self.root/'fit.bin')
        joints=values((self.root/'fit.bin').read_bytes(),r['parts'][0]['joints'])
        self.assertTrue(all(j==7 for j in joints[16::4]))

    def test_download_gate_is_unchanged(self):
        with self.assertRaisesRegex(ValueError,'persistent-head'):
            native('sim_build',{},self.model)
