"""Sim reference and fitting evidence, independent from gameplay acceptance."""
import base64
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
import zipfile

from package_creation.tests.test_objects import glb, model_fixture

ROOT = Path(__file__).resolve().parents[2]
BINARY = required_artifact("SIM_NATIVE_BINARY")
ASSETS = fixture_root() / 'package_creation/sims/assets'


def call(op, params, assets):
    p = subprocess.run([str(BINARY)], input=json.dumps({'op': op, 'params': params, 'assets': {k: str(v) for k, v in assets.items()}}), capture_output=True, text=True, timeout=90)
    if p.returncode:
        raise ValueError(p.stderr)
    return json.loads(p.stdout)


def numbers(text, fmt='f'):
    raw = base64.b64decode(text, validate=True)
    return struct.unpack('<' + fmt * (len(raw) // struct.calcsize(fmt)), raw)


def write_fixture(path, groups=2):
    doc, data = model_fixture()
    doc['meshes'][0]['primitives'] = [copy.deepcopy(doc['meshes'][0]['primitives'][i % 2]) for i in range(groups)]
    path.write_bytes(glb(doc, data))
    return doc, data


@unittest.skipUnless(BINARY.exists() and (ASSETS / 'am-rig.package').exists(), 'Build Sim example and extract pinned references')
class Sims(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.model = self.root / 'model.glb'
        write_fixture(self.model)
        self.assets = {'model.glb': self.model, 'reference': ASSETS / 'am-rig.package'}
        self.params = {'model': 'model.glb', 'reference': 'reference', 'neck': .84, 'rotation': 0}

    def test_created_scaffolds_and_comparison_samples(self):
        for body, gender in [('am', 2), ('af', 1)]:
            files = [ASSETS / f'{body}-scaffold.package', *sorted((ROOT / 'artifacts/sim-creator/fixtures' / body).glob('*.package'))]
            for file in files:
                result = call('sim_inspect_scaffold', {'asset': 'fixture'}, {'fixture': file})
                self.assertEqual(result['body'], body)
                self.assertEqual(result['age'], 8)
                self.assertEqual(result['resources'], 14)
                self.assertNotIn('00000000-00000000-0000000000000000', result['external_links'])
                self.assertFalse(result['external_dependencies_verified'])

    def test_actual_weights_morphs_and_rigid_head(self):
        for body in ['am', 'af']:
            self.assets['reference'] = ASSETS / f'{body}-rig.package'
            info = call('sim_inspect_model', self.params, self.assets)
            self.params['roles'] = {info['parts'][0]['id']: 'head', info['parts'][1]['id']: 'body'}
            result = call('sim_fit', self.params, self.assets)
            self.assertEqual(result['head_joint'], 7)
            for part in result['parts']:
                weights = numbers(part['weights'])
                joints = numbers(part['joints'], 'H')
                self.assertTrue(all(math.isfinite(v) and 0 <= v <= 1 for v in weights))
                for i in range(0, len(weights), 4):
                    self.assertAlmostEqual(sum(weights[i:i+4]), 1, places=6)
                self.assertTrue(all(j < len(result['bones']) for j in joints))
                self.assertEqual({m['name'] for m in part['morphs']}, {'fatbot', 'pregbot'})
                if part['role'] == 'head':
                    self.assertTrue(all(j == 7 for j in joints[::4]))
                    self.assertTrue(all(w == 1 for w in weights[::4]))
                    self.assertTrue(all(v == 0 for m in part['morphs'] for v in numbers(m['positions'])))
            self.assertEqual(result, call('sim_preview', self.params, self.assets))

    def test_rotation_preserves_height_and_uses_same_transfer(self):
        for angle in [0, 45, 90, -180]:
            result = call('sim_fit', {**self.params, 'rotation': angle}, self.assets)
            z = [v for part in result['parts'] for v in numbers(part['positions'])[2::3]]
            self.assertAlmostEqual(max(z) - min(z), result['height'], places=6)

    def test_zip_and_many_groups(self):
        doc, data = write_fixture(self.model, 24)
        doc['buffers'][0]['uri'] = 'mesh.bin'
        path = self.root / 'model.zip'
        with zipfile.ZipFile(path, 'w') as z:
            z.writestr('scene/model.gltf', json.dumps(doc))
            z.writestr('scene/mesh.bin', data)
        self.assets['model.zip'] = path
        self.assertEqual(len(call('sim_inspect_model', self.params, self.assets)['parts']), 24)
        a = call('sim_fit', self.params, self.assets)
        b = call('sim_fit', {**self.params, 'model': 'model.zip'}, self.assets)
        self.assertEqual(a, b)
        write_fixture(self.model, 65)
        with self.assertRaisesRegex(ValueError, 'Too many material groups'):
            call('sim_inspect_model', self.params, self.assets)

    def test_invalid_settings_and_unsupported_rigs(self):
        for field, value in [('neck', .2), ('rotation', 181), ('roles', {'mesh_0': 'unknown'})]:
            params = {**self.params, field: value}
            if field == 'roles':
                info = call('sim_inspect_model', self.params, self.assets)
                params[field] = {info['parts'][0]['id']: 'unknown'}
            with self.assertRaises(ValueError):
                call('sim_fit', params, self.assets)
        doc, data = model_fixture()
        doc['skins'] = [{'joints': [0]}]
        self.model.write_bytes(glb(doc, data))
        with self.assertRaisesRegex(ValueError, 'neutral A or T pose'):
            call('sim_inspect_model', self.params, self.assets)
        self.model.write_bytes(b'not a model')
        with self.assertRaises(ValueError):
            call('sim_inspect_model', self.params, self.assets)

    def test_gate_blocks_build_and_validation(self):
        for op in ['sim_build', 'sim_validate']:
            with self.assertRaisesRegex(ValueError, 'persistent-head gameplay'):
                call(op, self.params, self.assets)


if __name__ == '__main__':
    unittest.main()
