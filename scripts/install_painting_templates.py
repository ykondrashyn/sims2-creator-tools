"""Verify extracted wall paintings and publish pinned artwork recipes.

Artwork polygons are selected from the inspected subset and checked as a planar
quad. Recipe coordinates are derived from those polygons, not texture sizes.
"""
import argparse
import base64
import hashlib
import io
import json
import math
import struct
import subprocess
from pathlib import Path
from PIL import Image
from scripts.extract_object_templates import index, unpack, sgname, key
from scripts.install_object_templates import call

ROOT = Path(__file__).resolve().parents[1]
PINNED = {
    "bella": "dcc3369997479dd0094dd6893ecc927af742b6c2fa420902c163849e2d5565b9",
    "lady": "9b615e4314f7123709154094c1bf0a9e118c2e491486c7437757c9078af4deba",
    "city": "b276d7e4b9feb9c12c02b8778fc07c91d5a1e8563d55a9ae81b55efc00578e2b",
    "hills": "3ac4926059988d90bc8b307c104952155e4fafeb858a4b27b1a52c7931ff251c",
}


def geometry(scene):
    result = []
    for mesh in scene["meshes"]:
        b = base64.b64decode(mesh["glb"])
        n = struct.unpack_from("<I", b, 12)[0]
        doc, data = json.loads(b[20:20+n]), b[28+n:]
        def accessor(i):
            a = doc["accessors"][i]
            v = doc["bufferViews"][a["bufferView"]]
            fmt = "<" + {5126:"f",5123:"H",5125:"I"}[a["componentType"]] * {"VEC2":2,"VEC3":3,"SCALAR":1}[a["type"]]
            start = v.get("byteOffset", 0) + a.get("byteOffset", 0)
            return [struct.unpack_from(fmt, data, start+j*v.get("byteStride", struct.calcsize(fmt))) for j in range(a["count"])]
        for m in doc["meshes"]:
            if m["name"] == "b_mesh" or "shadow" in m["name"].lower():
                continue
            for p in m["primitives"]:
                vertices = accessor(p["attributes"]["POSITION"])
                uv = accessor(p["attributes"]["TEXCOORD_0"])
                ids = [x[0] for x in accessor(p["indices"])]
                for i in range(0, len(ids), 3):
                    points = [vertices[k] for k in ids[i:i+3]]
                    u = [points[1][k]-points[0][k] for k in range(3)]
                    v = [points[2][k]-points[0][k] for k in range(3)]
                    cross = [u[1]*v[2]-u[2]*v[1], u[2]*v[0]-u[0]*v[2], u[0]*v[1]-u[1]*v[0]]
                    result.append({"subset":m["name"], "positions":points, "uv":[uv[k] for k in ids[i:i+3]], "area":sum(x*x for x in cross)**.5/2})
    return result


def thumbnail(triangles, materials, filename):
    """Orthographic front view of the actual source geometry and frame textures."""
    vertices = [p for t in triangles for p in t["positions"]]
    lo = [min(p[i] for p in vertices) for i in range(3)]
    hi = [max(p[i] for p in vertices) for i in range(3)]
    scale = min(288/(hi[0]-lo[0]), 224/(hi[2]-lo[2]))
    textures = {n:Image.open(io.BytesIO(base64.b64decode(m["image"].split(",")[1]))).convert("RGBA") for n,m in materials.items() if m.get("image")}
    out = Image.new("RGB", (320,256), (224,225,218))
    depth = [-math.inf] * (320*256)
    for tri in triangles:
        pts = [[160+((lo[0]+hi[0])/2-p[0])*scale,128+((lo[2]+hi[2])/2-p[2])*scale] for p in tri["positions"]]
        a,b,c = pts
        d = (b[1]-c[1])*(a[0]-c[0])+(c[0]-b[0])*(a[1]-c[1])
        if abs(d)<1e-7: continue
        texture = textures.get(tri["subset"])
        for y in range(max(0,int(min(p[1] for p in pts))),min(256,math.ceil(max(p[1] for p in pts)))):
            for x in range(max(0,int(min(p[0] for p in pts))),min(320,math.ceil(max(p[0] for p in pts)))):
                u=((b[1]-c[1])*(x+.5-c[0])+(c[0]-b[0])*(y+.5-c[1]))/d
                v=((c[1]-a[1])*(x+.5-c[0])+(a[0]-c[0])*(y+.5-c[1]))/d
                weights=[u,v,1-u-v]
                if min(weights)<0: continue
                z=sum(weights[i]*tri['positions'][i][1] for i in range(3))
                if z<depth[y*320+x]:continue
                depth[y*320+x]=z
                if texture:
                    uv=[sum(weights[j]*tri['uv'][j][i] for j in range(3))%1 for i in range(2)]
                    color=texture.getpixel((min(texture.width-1,int(uv[0]*texture.width)),min(texture.height-1,int(uv[1]*texture.height))))[:3]
                else:color=tuple(round(255*c) for c in materials.get(tri['subset'],{}).get('diffuse',[.5,.5,.5]))
                out.putpixel((x,y),color)
    out.save(filename)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=ROOT/"artifacts/painting-creator/extracted")
    parser.add_argument("--output", type=Path, default=ROOT/"package_creation/paintings/assets")
    parser.add_argument("--reports", type=Path, default=ROOT/"artifacts/painting-creator/recipes")
    args=parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    args.reports.mkdir(parents=True, exist_ok=True)
    catalog=json.loads((args.input/"catalog.json").read_text())
    for item in catalog['items']:
        path=args.input/(item['id']+'.package')
        if hashlib.sha256(path.read_bytes()).hexdigest()!=PINNED[item['id']]:raise ValueError('Inspected source changed: '+item['id'])
        target=args.reports/(item['id']+'.package')
        item['graph']=call('object_extract',{'source':path},{'asset':'source'},target,'extracted')
        item.update(call('object_profile',{'source':target},{'asset':'source'}))
        item['placement']={'known':True}
    profiles=args.reports/'profiles.json';profiles.write_text(json.dumps(catalog))
    for item in catalog['items']:
        ident=item['id'];path=args.reports/(ident+'.package')
        assets={'source.package':path,'object-catalog':profiles,'object-game':ROOT/'package_creation/objects/assets/game.json'}
        target=args.output/(ident+'.package')
        inspection=call('object_inspect',assets,{'files':['source.package'],'trusted':True},target,'object-template')
        assets['object-template']=target
        job={'id':'abcdef0123456789abcdef0123456789','creator':'Recipe','object_name':ident,'title':item['label'],'description':'Painting recipe verification','price':100,'mode':'clone'}
        preview=call('object_preview',assets,{'job':job})
        tris=geometry(preview['original'])
        face=sorted((t for t in tris if t['subset']==item['subset']),key=lambda t:-t['area'])[:2]
        points={tuple(p) for t in face for p in t['positions']}
        lo=[min(p[i] for p in points) for i in range(3)];hi=[max(p[i] for p in points) for i in range(3)]
        if len(points)!=4 or hi[1]-lo[1]>1e-5 or abs(sum(t['area'] for t in face)-(hi[0]-lo[0])*(hi[2]-lo[2]))>1e-5:raise ValueError('Artwork is not the inspected planar quad: '+ident)
        texture=next(t for t in inspection['textures'] if t['name']==item['texture'])
        f,idx=index(target);texture_key=None
        for k,loc in idx.items():
            if k[0]!=0x1c4a276c:continue
            f.seek(loc[0]);b=unpack(f.read(loc[1]))
            if sgname(b)==item['texture']:texture_key=key(k)
        f.close()
        if texture_key is None:raise ValueError('Missing artwork texture')
        visible=[p for t in tris for p in t['positions']]
        bounds=[[min(p[i] for p in visible) for i in range(3)],[max(p[i] for p in visible) for i in range(3)]]
        dims={'width':bounds[1][0]-bounds[0][0],'height':bounds[1][2]-bounds[0][2],'depth':bounds[1][1]-bounds[0][1]}
        recipe={'version':1,'id':ident,'template_sha256':hashlib.sha256(target.read_bytes()).hexdigest(),'texture':item['texture'],'texture_key':texture_key,'subset':item['subset'],
            **{k:texture[k] for k in ['width','height','format']},'aspect':(hi[0]-lo[0])/(hi[2]-lo[2]),'dimensions':dims,'wall_anchor':[(lo[0]+hi[0])/2,(lo[1]+hi[1])/2,(lo[2]+hi[2])/2],
            'triangles':[{'uv':t['uv'],'art':[[(hi[0]-p[0])/(hi[0]-lo[0]),(hi[2]-p[2])/(hi[2]-lo[2])] for p in t['positions']]} for t in face],
            'geometry_bounds':bounds,'source_hashes':item['sources'],'verification':'Two largest artwork triangles form the pinned planar quad. Front-facing coordinates preserve source proportions.'}
        (args.output/(ident+'-recipe.json')).write_text(json.dumps(recipe,indent=2)+'\n')
        thumbnail(tris,preview['original']['materials'],args.output/(ident+'.png'))
        item.update(sha256=recipe['template_sha256'],dimensions=dims,aspect=recipe['aspect'],placement=inspection['placement'],price=inspection['objects'][0]['price'],shape='Square' if ident=='bella' else 'Portrait' if ident=='lady' else 'Large landscape' if ident=='hills' else 'Landscape')
        item['recipe']=ident+'-recipe.json';item['thumbnail']=ident+'.png'
        (args.reports/(ident+'.json')).write_text(json.dumps({'inspection':inspection,'recipe':recipe,'clone_report':preview['report']},indent=2))
        print(ident,'atlas',texture['width'],texture['height'],'physical artwork aspect',recipe['aspect'],'wall tiles',inspection['placement']['tile_count'])
    (args.output/'catalog.json').write_text(json.dumps(catalog,indent=2)+'\n')


if __name__=='__main__': main()
