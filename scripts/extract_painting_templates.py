"""Read the reference game over SSH and install inspected painting assets locally.

No game files are modified. The recipe installer performs the artwork checks
before these assets can be published by build_package_runtime.py.
"""
import argparse
import base64
import hashlib
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = [
    ("bella", "Bella Squared", 0x7F34D4BA, "paintingSquareBella", "paintingsquarebella_paintingfacebella", "paintingsquare-bella"),
    ("lady", "The Lady On Red", 0x7F292E88, "paintingVertical", "paintingvertical_portraitoil", "paintingverticalbella"),
    ("city", '"SimCity at Night"', 0x7F31133F, "paintingHorizontalCity", "paintinghorizontalcity_city", "paintinghorizontal-paintings-timelapse"),
    ("hills", "Rolling Hills by H. Sean", 0x7F04B12D, "paintingHorizontalLandscape", "paintinghorizontallandscape_paintings2", "paintinghorizontallandscape1-landscape1"),
]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ssh", default="yevhenkondrashyn@192.168.50.185")
    parser.add_argument("--game-root", default="Games/The Sims 2 Legacy")
    parser.add_argument("--output", type=Path, default=ROOT / "artifacts/painting-creator/extracted")
    args = parser.parse_args()
    game = json.loads((ROOT / "package_creation/objects/assets/game.json").read_text())
    helpers = (ROOT / "scripts/extract_object_templates.py").read_text().split("def main():")[0]
    remote = helpers + "\nimport base64,re\n"
    remote += "templates=" + repr(TEMPLATES) + "\n"
    remote += "names=" + repr(game["names"]) + "\n"
    remote += "root=Path.home()/" + repr(args.game_root) + "\n"
    remote += r'''
files=[]; locations={}
for path in [root/"EP9/TSData/Res/Objects/objects.package", *sorted((root/"Base/TSData/Res/Sims3D").glob("Objects*.package")), root/"Base/TSData/Res/Sims3D/Textures.package",root/"Base/TSData/Res/Catalog/Materials/Materials.package"]:
    f,idx=index(path);files.append(f)
    for k,loc in idx.items():locations[k]=(f,loc,path)
def read(k):
    f,loc,path=locations[k];f.seek(loc[0]);return unpack(f.read(loc[1]))
def fromkey(s):return tuple(int(n,16) for n in s.split('-'))
result=[]
for ident,label,group,prefix,subset,texture in templates:
    keys={k for k in locations if k[1]==group}
    for name,ks in names.items():
        if name.startswith(prefix.lower()): keys.update(fromkey(k) for k in ks if fromkey(k) in locations)
    guids={struct.unpack_from('<I',read(k),92)[0] for k in keys if k[0]==0x4f424a44}
    for k in locations:
        if k[0]!=0x4c697e5a:continue
        b=read(k);p=b.find(b'objectGUID')
        if p>=0 and struct.unpack_from('<I',b,p+10)[0] in guids:keys.add(k)
    resources={};changed=True
    while changed:
        changed=False
        for k in list(keys):
            if k in resources:continue
            b=read(k);resources[k]=b
            referenced=[]
            if k[0]==0x49596978:
                for match in re.finditer(rb'stdMat[A-Za-z]*TextureName',b):
                    name,_=bigstring(b,match.end());referenced.extend(names.get(name.lower()+'_txtr',[]))
            if k[0]==0x1c4a276c:
                for name,ks in names.items():
                    if name.endswith('_lifo') and name[:-5].encode() in b:referenced.extend(ks)
            for ref in referenced:
                rk=fromkey(ref)
                if rk not in locations:raise ValueError('Missing appearance dependency '+ref)
                if rk not in keys:keys.add(rk);changed=True
    recipes=[{'key':key(k),'source':str(locations[k][2].relative_to(root)),'sha256':digest(b)} for k,b in resources.items()]
    result.append({'id':ident,'label':label,'kind':'wall-decor','requirements':'The Sims 2 Legacy Collection (EP9 behavior profile)','subset':subset,'texture':texture+'_txtr','sources':recipes,'package':base64.b64encode(pack(resources)).decode()})
for f in files:f.close()
print(json.dumps(result))
'''
    result = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", args.ssh, "python3", "-"], input=remote, text=True, capture_output=True, timeout=180, check=True)
    items = json.loads(result.stdout)
    args.output.mkdir(parents=True, exist_ok=True)
    for item in items:
        data = base64.b64decode(item.pop("package"))
        item["source_sha256"] = hashlib.sha256(data).hexdigest()
        (args.output / (item["id"] + ".package")).write_bytes(data)
        print(item["label"], len(data), item["source_sha256"])
    (args.output / "catalog.json").write_text(json.dumps({"schema_version": 1, "items": items}, indent=2) + "\n")


if __name__ == "__main__":
    main()
