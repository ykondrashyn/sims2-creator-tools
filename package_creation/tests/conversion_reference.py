"""Development-only pinned Blender corpus generator and native comparison.

Requires NumPy and Pillow. Run from the checkout after building the native
`conversion` example. The browser/WASM comparison is conversion_wasm_parity.mjs.
"""
from __future__ import annotations
import argparse, json, struct, subprocess, time, zlib
from pathlib import Path
import numpy as np
from PIL import Image, PngImagePlugin, ImageCms
ROOT=Path(__file__).resolve().parents[2]

def corpus(folder):
    folder.mkdir(parents=True,exist_ok=True)
    x,y=np.meshgrid(np.arange(1024,dtype=np.uint32),np.arange(2048,dtype=np.uint32))
    rng=np.random.default_rng(341)
    gradient=np.stack([x*255//1023,y*255//2047,(x+y)%256,(3*x+7*y)%256],-1).astype('u1')
    random=rng.integers(0,256,size=(2048,1024,4),dtype=np.uint8)
    seams=np.zeros((2048,1024,4),dtype=np.uint8)
    seams[::37,:,:]=[255,1,127,129];seams[:,::31,:]=[1,255,40,254];seams[401:900,501:504]=[20,100,255,1]
    grid=np.stack([x%256,y%256,(x^y)%256,np.where((x//16+y//16)%2,1,254)],-1).astype('u1')
    diagnostic=np.zeros((2048,1024,4),dtype='u1')
    area=(y>200)&(y<1750)&(x>220)&(x<820)
    diagnostic[area]=np.stack([50+(x//16)%180,30+(y//16)%180,np.full_like(x,80),((x+y)//16)%256],-1)[area]
    for name,data in dict(gradient=gradient,random=random,seams=seams,**{'uv-grid':grid},diagnostic=diagnostic).items():Image.fromarray(data).save(folder/f'{name}.png')
    for name in ['ash_majima','ash_kiryu']:Image.open(ROOT/f'{name}.png').save(folder/f'{name}.png')
    image=Image.fromarray(gradient)
    info=PngImagePlugin.PngInfo();info.add(b'gAMA',struct.pack('>I',100000));info.add_text('Comment','body converter metadata acceptance');image.save(folder/'metadata-gamma.png',pnginfo=info)
    image.save(folder/'metadata-icc.png',icc_profile=ImageCms.ImageCmsProfile(ImageCms.createProfile('sRGB')).tobytes())
    rgba16=(gradient.astype('u2')*257+rng.integers(0,129,gradient.shape,dtype='u2')).astype('>u2')
    def chunk(kind,data):return struct.pack('>I',len(data))+kind+data+struct.pack('>I',zlib.crc32(kind+data)&0xffffffff)
    raw=b''.join(b'\0'+row.tobytes() for row in rgba16)
    (folder/'rgba16.png').write_bytes(b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',1024,2048,16,6,0,0,0))+chunk(b'IDAT',zlib.compress(raw))+chunk(b'IEND',b''))

def main():
    p=argparse.ArgumentParser();p.add_argument('--blender',type=Path,required=True);p.add_argument('--output',type=Path,default=ROOT/'artifacts/body-conversion/corpus');p.add_argument('--native',type=Path,required=True);args=p.parse_args()
    corpus(args.output)
    report=[]
    for body,profile in [('am','body_4t2.json'),('af','body_4t2_af.json')]:
        for source in sorted(args.output.glob('*.png')):
            (args.output/'blender').mkdir(exist_ok=True);(args.output/'rust').mkdir(exist_ok=True)
            expected=args.output/'blender'/f'{body}-{source.name}';actual=args.output/'rust'/expected.name
            command=[str(args.blender),'-b',str(ROOT/f'templates/{body.upper()}-body-4t2-1024.blend'),'--python-exit-code','1','--python',str(ROOT/'scripts/bake_texture.py'),'--','--input',str(source.resolve()),'--output',str(expected.resolve()),'--profile',str(ROOT/'profiles'/profile)]
            started=time.monotonic()
            with expected.with_suffix('.log').open('w') as log:subprocess.run(command,stdout=log,stderr=subprocess.STDOUT,check=True)
            blender_seconds=time.monotonic()-started;started=time.monotonic()
            subprocess.run([str(args.native),body,str(ROOT/f'package_creation/conversion/assets/{body}.zip'),str(source),str(actual)],check=True,capture_output=True)
            a=np.array(Image.open(actual));b=np.array(Image.open(expected));changed=int(np.count_nonzero(a!=b))
            row=dict(body=body,input=source.name,decoded_differing_bytes=changed,blender_seconds=blender_seconds,native_seconds=time.monotonic()-started)
            report.append(row);print(json.dumps(row),flush=True)
            assert a.shape==b.shape==(1024,1024,4) and changed==0,row
    (args.output/'rust/report.json').write_text(json.dumps(report,indent=2)+'\n')
if __name__=='__main__':main()
