"""Painting package, image validation and deterministic native/WASM fixtures."""
import base64
import hashlib
import io
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from package_creation.tests.artifacts import required_artifact, fixture_root
from PIL import Image, ImageDraw, ImageCms, PngImagePlugin

ROOT=Path(__file__).resolve().parents[2]
ASSETS=ROOT/'package_creation/paintings/assets'
BINARY = required_artifact("PAINTING_NATIVE_BINARY")


def fixture(path,format='PNG',**kwargs):
    image=Image.new('RGBA',(640,480),'white')
    draw=ImageDraw.Draw(image)
    draw.rectangle((0,0,319,239),fill=(220,30,40,255))
    draw.rectangle((320,0,639,239),fill=(30,180,60,255))
    draw.rectangle((0,240,319,479),fill=(40,70,220,255))
    draw.rectangle((320,240,639,479),fill=(255,180,10,128))
    draw.ellipse((180,100,460,380),outline='black',width=8)
    draw.text((20,20),'TOP LEFT 123',fill='white',stroke_width=1)
    if format=='JPEG':image=image.convert('RGB')
    image.save(path,format=format,**kwargs)
    return image


def call(op,assets,params,output=None):
    req={'op':op,'assets':{k:str(v) for k,v in assets.items()},'params':params}
    if output:req['output']=str(output)
    p=subprocess.run([str(BINARY)],input=json.dumps(req),text=True,capture_output=True)
    if p.returncode:raise ValueError(p.stderr[-3000:])
    return json.loads(p.stdout)


def inputs(ident,image):
    return {'painting-input':image,'painting-template':ASSETS/(ident+'.package'),'painting-recipe':ASSETS/(ident+'-recipe.json'),'object-catalog':ASSETS/'catalog.json','object-game':ROOT/'package_creation/objects/assets/game.json'}


def job(ident='bella'):
    return {'id':'abcdef0123456789abcdef0123456789','creator':'PaintingTest','title':'Test '+ident,'description':'Original creator and image credits','price':125,'mode':'clone','crop':{'mode':'fill','x':.25,'y':.7,'zoom':1.2,'background':[255,255,255]}}


@unittest.skipUnless(BINARY.exists() and (ASSETS/'catalog.json').exists(),'Install painting templates and build the native example')
class Paintings(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.image=self.root/'diagnostic.png';fixture(self.image)
    def tearDown(self):self.tmp.cleanup()
    def test_formats_profiles_and_orientation(self):
        profile=ImageCms.ImageCmsProfile(ImageCms.createProfile('sRGB')).tobytes()
        for format in ['PNG','JPEG','WEBP']:
            path=self.root/('image.'+format.lower());fixture(path,format,icc_profile=profile)
            info=call('painting_inspect_image',{'painting-input':path},{})
            self.assertEqual((info['width'],info['height']),(640,480));self.assertIn('converted to sRGB',info['profile'])
        exif=Image.Exif();exif[274]=6;fixture(self.root/'rotated.jpg','JPEG',exif=exif)
        info=call('painting_inspect_image',{'painting-input':self.root/'rotated.jpg'},{})
        self.assertEqual((info['width'],info['height']),(480,640))
        metadata=PngImagePlugin.PngInfo();metadata.add(b'cICP',bytes([1,13,0,1]))
        fixture(self.root/'srgb.png',pnginfo=metadata)
        self.assertEqual(call('painting_inspect_image',{'painting-input':self.root/'srgb.png'},{})['width'],640)
    def test_invalid_animated_oversized(self):
        paths=[]
        for name,data in [('bad.png',b'not an image'),('large.png',b'a'*(32*1024**2+1))]:
            p=self.root/name;p.write_bytes(data);paths.append(p)
        frames=[Image.new('RGBA',(16,16),c) for c in ['red','blue']]
        for format in ['PNG','WEBP','GIF']:
            p=self.root/('animated.'+format);frames[0].save(p,format=format,save_all=True,append_images=frames[1:],duration=100);paths.append(p)
        p=self.root/'dimensions.png';Image.new('RGB',(8193,1)).save(p);paths.append(p)
        p=self.root/'profile.png';fixture(p,icc_profile=b'broken profile');paths.append(p)
        for name,tag,data in [('hdr',b'cICP',bytes([9,16,0,1])),('gamma',b'gAMA',(100000).to_bytes(4,'big'))]:
            metadata=PngImagePlugin.PngInfo();metadata.add(tag,data)
            p=self.root/(name+'.png');fixture(p,pnginfo=metadata);paths.append(p)
        for path in paths:
            with self.subTest(path=path.name),self.assertRaises(ValueError):call('painting_inspect_image',{'painting-input':path},{})
    def test_all_templates_and_retry(self):
        results=[]
        for ident in ['bella','lady','city','hills']:
            assets=inputs(ident,self.image);request=job(ident)
            preview=call('painting_compose',assets,{'job':request})
            prepared=call('painting_prepare',assets,{'job':request})
            out=self.root/(ident+'.package');report=call('painting_build',assets,{'job':prepared},out)
            self.assertEqual(out.read_bytes()[:4],b'DBPF');self.assertEqual(report['status'],'passed')
            self.assertEqual(report['painting'],preview['report'])
            second=self.root/'retry.package';retry=call('painting_build',assets,{'job':prepared},second)
            self.assertEqual(out.read_bytes(),second.read_bytes());self.assertEqual(report,retry)
            self.assertEqual(report['placement']['surface'],'wall')
            self.assertEqual(report['placement']['tile_count'],{'bella':1,'lady':1,'city':2,'hills':3}[ident])
            self.assertEqual(len(report['objects']),{'bella':1,'lady':1,'city':3,'hills':4}[ident])
            self.assertEqual(report['painting']['format'],'DXT3' if ident=='hills' else 'DXT1')
            other=call('painting_prepare',assets,{'job':{**request,'id':'123456789abcdef0123456789abcdef0'}})
            self.assertNotEqual(prepared['identities'],other['identities'])
            request['crop']['mode']='contain';whole=call('painting_compose',assets,{'job':request})
            self.assertEqual(whole['report']['source_rect'],[0,0,640,480])
            results.append(ident)
        self.assertEqual(len(results),4)
    def test_damaged_recipe_and_snapshot(self):
        assets=inputs('bella',self.image);prepared=call('painting_prepare',assets,{'job':job()})
        prepared['identities']['prefix']='invalid'
        with self.assertRaises(ValueError):call('painting_build',assets,{'job':prepared})
        r=json.loads((ASSETS/'bella-recipe.json').read_text());r['template_sha256']='0'*64;p=self.root/'recipe.json';p.write_text(json.dumps(r));assets['painting-recipe']=p
        with self.assertRaises(ValueError):call('painting_compose',assets,{'job':job()})


if __name__=='__main__':unittest.main()
