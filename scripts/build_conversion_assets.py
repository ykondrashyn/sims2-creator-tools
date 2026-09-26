"""Assemble lossless, versioned body bake maps exported by pinned Blender.

Development only. The production service needs just the generated assets.
Run export_conversion_maps.py for AM and AF first (AM with --quantization).
"""
from __future__ import annotations
import argparse, array, hashlib, json, struct, subprocess, zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def main():
    p=argparse.ArgumentParser()
    p.add_argument('--maps',type=Path,default=ROOT/'artifacts/body-conversion')
    p.add_argument('--output',type=Path,default=ROOT/'package_creation/conversion/assets')
    args=p.parse_args()
    args.output.mkdir(parents=True,exist_ok=True)
    def sha(b): return hashlib.sha256(b).hexdigest()
    for name in ['sampling','margin_recipe']:
        subprocess.run(['clang++','-O2','-ffp-contract=off','-std=c++17',str(ROOT/f'scripts/conversion_{name}.cpp'),'-o',str(args.maps/name)],check=True)
    subprocess.run([str(args.maps/'sampling'),str(args.maps/'pmj.bin')],check=True)
    profiles={}
    for body in ['am','af']:
        folder=args.maps/body
        metadata=json.loads((folder/'mapping.json').read_text())
        metadata['body']=body
        source,target=metadata['source'],metadata['target']
        primitives=array.array('I');primitives.frombytes((folder/'BakePrimitive.f32').read_bytes())
        target_prim=array.array('I');target_prim.frombytes((folder/'TargetBakePrimitive.f32').read_bytes())
        def ints(values): return array.array('I',values).tobytes()
        data=struct.pack('<5i',1024,1024,len(target['polygons']),len(target['loop_edges']),len(target['edges']))
        data+=ints(v for polygon in target['polygons'] for v in (polygon[0],len(polygon)))
        data+=ints(target['loop_edges'])
        data+=array.array('f',(v for uv in target['uv'] for v in uv)).tobytes()
        data+=ints(target['triangle_polygons'][v] if v!=0xffffffff else v for v in target_prim[1::4])
        data+=bytes(int(v!=0xffffffff) for v in primitives[1::4])
        (folder/'margin-input.bin').write_bytes(data)
        subprocess.run([str(args.maps/'margin_recipe'),str(folder/'margin-input.bin'),str(folder/'margin.bin')],check=True)
        files={
            'primitive.bin':(folder/'BakePrimitive.f32').read_bytes(),
            'differential.bin':(folder/'BakeDifferential.f32').read_bytes(),
            'uv.bin':array.array('f',(v for triangle in source['triangle_loops'] for loop in triangle for v in source['uv'][loop])).tobytes(),
            'margin.bin':(folder/'margin.bin').read_bytes(),
            'quantization.bin':(args.maps/'am/quantization.u32').read_bytes(),
            'pmj.bin':(args.maps/'pmj.bin').read_bytes(),
        }
        metadata['exporter_sources']={name:sha((ROOT/'scripts'/name).read_bytes()) for name in ['export_conversion_maps.py','conversion_margin_recipe.cpp','conversion_sampling.cpp','build_conversion_assets.py']}
        metadata['buffers']={name:sha(data) for name,data in files.items()}
        files['metadata.json']=json.dumps(metadata,separators=(',',':')).encode()
        dest=args.output/f'{body}.zip'
        with zipfile.ZipFile(dest,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=9) as z:
            for name,data in sorted(files.items()):
                info=zipfile.ZipInfo(name,date_time=(2026,1,1,0,0,0))
                info.compress_type=zipfile.ZIP_DEFLATED
                z.writestr(info,data)
        profiles[body]={'asset':f'conversion:{body}','sha256':sha(dest.read_bytes()),'size':dest.stat().st_size,'template_sha256':metadata['template_sha256'],'input_dimensions':[1024,2048],'output_dimensions':[1024,1024]}
    (args.output/'manifest.json').write_text(json.dumps({'schema_version':1,'profiles':profiles},indent=2)+'\n')
    print(json.dumps(profiles,indent=2))

if __name__=='__main__':main()
