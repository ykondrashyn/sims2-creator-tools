"""Custom curve import, composition, job isolation and real package routing."""
import copy
import hashlib
import io
import json
import time
import unittest
import zipfile
from pathlib import Path

from PIL import Image

from package_creation.hair.colors import ASSETS, BY_NAME, parse_curve
from package_creation.hair.custom_colors import CHANNELS, MAX_BYTES, normalize_colors, normalize_curve, parse_gimp
from package_creation.hair.processing import load_texture, prepare
from package_creation.hair.service import HairManager, thumbnail
from package_creation.hair.worker import atomic_json
from package_creation.tests import test_hair as fixtures


def color(index=0, category=0):
    points = {c: [[0, 0], [255, 255]] for c in CHANNELS}
    points['red'] = [[0, 10], [128, 160], [255, 240]]
    return {'id': 'custom:' + f'{index:032x}', 'name': f'My Color {index}', 'bin': category,
            'curve': {'source': 'editor', 'points': points}}


def gimp(channels=None):
    channels = channels or {c: [i / 255 for i in range(256)] for c in (*CHANNELS, 'alpha')}
    return ('# GIMP curves tool settings\n' + '\n'.join(
        f'(channel {c})\n(curve (curve-type free) (n-samples 256) (samples 256 ' + ' '.join(f'{n:.9f}' for n in v) + '))'
        for c, v in channels.items())).encode()


class CustomCurveTests(unittest.TestCase):
    def test_import_samples_match_all_installed_presets(self):
        for path in (ASSETS / 'curves').rglob('Pooklet*'):
            if path.is_file() and not path.suffix:
                with self.subTest(path=path.name):
                    _, tables = normalize_curve(parse_gimp(path.read_bytes(), path.name))
                    self.assertEqual(tables, [list(t) for t in parse_curve(path)])

    def test_identity_value_composition_and_piecewise_sampling(self):
        _, identity = normalize_curve(parse_gimp(gimp()))
        self.assertEqual(identity, [list(range(256))] * 3)
        definition = color()['curve']
        definition['points']['value'] = [[0, 0], [255, 127.5]]
        _, tables = normalize_curve(definition)
        self.assertEqual(tables[0][128], 80)
        self.assertEqual(tables[0][0], 5)
        self.assertEqual(tables[1][128], 64)
        definition['points']['red'] = [[0, 255], [255, 0]]
        _, tables = normalize_curve(definition)
        self.assertEqual(tables[0][0], 128)  # Channel then Value, not the reverse.
        self.assertEqual(tables[0][255], 0)

    def test_invalid_gimp_and_alpha(self):
        valid = gimp()
        bad = [b'not curves', b'\x00DBPF', valid[:-1], valid + b')', valid + b'(channel red)',
               valid.replace(b'(channel red)', b'(channel value)'),
               valid.replace(b'(n-samples 256)', b'(n-samples 255)', 1),
               valid.replace(b'0.000000000', b'nan', 1),
               valid + b'(trc linear)', valid + b'(linear yes)', valid + b'(trc (linear))',
               valid + b'(unknown setting)', b'x' * (MAX_BYTES + 1)]
        channels = {c: [i / 255 for i in range(256)] for c in (*CHANNELS, 'alpha')}
        channels['alpha'][100] = 0
        bad.append(gimp(channels))
        for data in bad:
            with self.subTest(data=data[-35:]), self.assertRaises(ValueError):
                parse_gimp(data)

    def test_names_limits_and_invalid_definitions(self):
        for name in ['Dynamite', 'depthcharge', '../Hair', '<script>', '', 'A' * 49, ' trailing ']:
            value = color()
            value['name'] = name
            with self.subTest(name=name), self.assertRaises(ValueError):
                normalize_colors([value])
        for bad in [True, -1, 5, [], 'red']:
            value = color()
            value['bin'] = bad
            with self.assertRaises(ValueError): normalize_colors([value])
        self.assertEqual(len(normalize_colors([color(i) for i in range(16)])), 16)
        with self.assertRaises(ValueError): normalize_colors([color(i) for i in range(17)])
        with self.assertRaises(ValueError): normalize_colors([color(), color()])
        duplicate = color(1)
        duplicate['name'] = 'MyColor0'
        with self.assertRaises(ValueError): normalize_colors([color(), duplicate])
        for points in [[[0, 0], [0, 255]], [[1, 0], [255, 255]], [[0, 0], [255, float('inf')]], []]:
            value = color()
            value['curve']['points']['red'] = points
            with self.assertRaises(ValueError): normalize_colors([value])


class CustomCurveIntegrationTests(unittest.TestCase):
    setUp = fixtures.HairIntegrationTests.setUp
    tearDown = fixtures.HairIntegrationTests.tearDown
    create = fixtures.HairIntegrationTests.create
    wait = fixtures.HairIntegrationTests.wait
    build = fixtures.HairIntegrationTests.build
    import_packages = fixtures.HairIntegrationTests.import_packages

    def preview(self, job, custom, selected=None):
        return self.client.post(f"/api/v1/hair/jobs/{job['id']}/preview", json={
            'settings': {}, 'colors': selected if selected is not None else [c['id'] for c in custom], 'custom_colors': custom})

    def test_parse_api_bounded_same_origin_and_stateless(self):
        script = self.client.get('/static/hair-curves.js')
        self.assertEqual(script.status_code, 200)
        self.assertIn('window.HairCurveEditor', script.text)
        endpoint = '/api/v1/hair/curves/parse'
        before = sorted(self.root.rglob('*'))
        response = self.client.post(endpoint, files={'file': ('NoExtension', gimp())})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['tables'], [list(range(256))] * 3)
        self.assertEqual(sorted(self.root.rglob('*')), before)
        for body, status in [(b'not-gimp', 422), (b'x' * (MAX_BYTES + 1), 413), (b'x' * (MAX_BYTES * 2), 413)]:
            self.assertEqual(self.client.post(endpoint, files={'file': ('curve', body)}).status_code, status)
        self.assertEqual(self.client.post(endpoint, files={'file': ('curve', gimp())}, headers={'Origin': 'http://other.example'}).status_code, 403)

    def test_preview_isolation_atomic_rejection_edit_remove_and_expiry(self):
        job = self.create()
        base = f"/api/v1/hair/jobs/{job['id']}"
        response = self.preview(job, [color()])
        self.assertEqual(response.status_code, 200, response.text[:1000])
        directory = self.root / 'hair' / job['id']
        original = json.loads((directory / 'job.json').read_text())
        self.assertEqual(response.json()['custom_colors'][0]['id'], color()['id'])
        other = self.create()
        self.assertEqual(self.preview(other, [], [color()['id']]).status_code, 422)
        self.assertEqual(len(self.client.get('/api/v1/hair/templates').json()['palette']), 43)
        collision = color()
        collision['name'] = 'Volatile'
        self.assertEqual(self.preview(job, [collision]).status_code, 422)
        self.assertEqual(json.loads((directory / 'job.json').read_text()), original)
        self.assertEqual(self.client.post(base + '/preview', content=b'x' * (MAX_BYTES + 1)).status_code, 413)
        edited = color()
        edited['name'], edited['bin'] = 'Renamed Brown', 2
        edited['curve']['points']['blue'] = [[0, 20], [255, 200]]
        self.assertEqual(self.preview(job, [edited]).status_code, 200)
        current = json.loads((directory / 'job.json').read_text())
        self.assertEqual(current['identities'], original['identities'])
        self.assertNotEqual(current['custom_colors'][0]['tables'], original['custom_colors'][0]['tables'])
        self.assertEqual(self.preview(job, [], ['TNT']).status_code, 200)
        self.assertEqual(self.client.get(base).json()['custom_colors'], [])
        # Restoring the same definition ID keeps retry identities.
        self.assertEqual(self.preview(job, [edited]).status_code, 200)
        self.assertEqual(json.loads((directory / 'job.json').read_text())['identities'], original['identities'])
        self.client.post(base + '/cancel')
        self.assertEqual(self.client.get(base).json()['state'], 'cancelled')
        manager = HairManager(self.config)
        manager.start()
        try:
            self.assertEqual(manager.status(job['id'])['custom_colors'][0]['name'], 'Renamed Brown')
        finally:
            manager.stop()
        self.build(job)
        self.assertEqual(json.loads((directory / 'job.json').read_text())['identities'], original['identities'])
        state = json.loads((directory / 'status.json').read_text())
        state['updated'] = time.time() - self.config.retention_seconds - 1
        atomic_json(directory / 'status.json', state)
        self.app.state.hair_manager.expire()
        self.assertFalse(directory.exists())

    def test_standard_and_rose_all_categories_preview_and_package_validation(self):
        evidence = Path(__file__).resolve().parents[2] / 'artifacts/hair-validation/custom-curves'
        evidence.mkdir(exist_ok=True)
        rose = evidence.parent / 'embedded-bundle'
        packages = [rose / 'mesh_rosehair_0124.package', rose / 'recolor_3555b7d0_rose72.package']
        response = self.import_packages([(p.name, p.read_bytes()) for p in packages])
        self.assertEqual(response.status_code, 201, response.text[:1000])
        rose_id = response.json()['items'][0]['id']
        seen = set()
        for template, label in [('standard-base-female-bun', 'Bun'), (rose_id, 'Rose')]:
            response = self.client.post('/api/v1/hair/jobs', data={'creator': 'CurveCheck', 'hair_name': label, 'template_id': template})
            self.assertEqual(response.status_code, 201, response.text[:1000])
            job = response.json()
            custom = [color(i, i) for i in range(5)]
            # Include a real imported curve, not just synthetic editor points.
            imported = color(5)
            path = next((ASSETS / 'curves').rglob('Pooklet Dynamite*'))
            imported['curve'] = parse_gimp(path.read_bytes(), path.name)
            custom.append(imported)
            selected = [c['id'] for c in custom] + (['Dynamite', 'Depth Charge'] if label == 'Rose' else [])
            response = self.preview(job, custom, selected)
            self.assertEqual(response.status_code, 200, response.text[:1000])
            directory = self.root / 'hair' / job['id']
            saved = json.loads((directory / 'job.json').read_text())
            definitions = {c['id']: c for c in saved['custom_colors']}
            for preview in response.json()['previews']:
                image = load_texture(directory / 'template-textures' / (preview['slot'] + '.png'))
                base_image = prepare(image, preview['settings'])
                for target in preview['targets']:
                    if target['rendered_color'] == 'Mail Bomb' or target['color'] not in definitions:
                        continue
                    tables = definitions[target['color']]['tables']
                    # Independent reference application of the stored tables.
                    expected = Image.merge('RGB', tuple(channel.point(table) for channel, table in zip(base_image.split(), tables)))
                    expected.putalpha(image.getchannel('A'))
                    self.assertEqual(target['image'], thumbnail(expected))
            data = self.build(job)
            report = json.loads((directory / 'validation.json').read_text())
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                self.assertEqual(len(archive.namelist()), len(selected) + 1 + (label == 'Rose'))
                self.assertIsNone(archive.testzip())
                self.assertIn('CUSTOM COLORS FOR THIS JOB', archive.read('README.txt').decode())
                self.assertFalse(any(name.endswith(('.json', '.curves')) for name in archive.namelist()))
                if label == 'Rose':
                    self.assertEqual(hashlib.sha256(archive.read('Meshes/' + packages[0].name)).hexdigest(), hashlib.sha256(packages[0].read_bytes()).hexdigest())
            families = []
            for package in report['packages']:
                keys = set(package['resource_keys'])
                self.assertFalse(seen & keys)
                seen.update(keys)
                self.assertTrue(all(t['alpha_max_error'] == 0 for t in package['textures']))
                self.assertTrue(all(t['mips'] in (10, 11) and t['format'] == 'DXT3' for t in package['textures']))
                if package['color_id'].startswith('custom:'):
                    families.append(package['family'])
                    self.assertNotEqual(package['family'], package['hairtone'])
            self.assertEqual(len(families), len(set(families)))
            (evidence / f'{label}-custom-curves.zip').write_bytes(data)
            atomic_json(evidence / f'{label}-validation.json', report)
