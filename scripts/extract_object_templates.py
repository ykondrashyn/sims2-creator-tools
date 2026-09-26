"""Offline extraction, reads the game and writes only the chosen output directory.

Run on the reference machine with --game-root and --output. Uses Python stdlib.
"""
import argparse
import hashlib
import json
import struct
from pathlib import Path

TEMPLATES = [
    ("urn", "Ancient Transport Urn Sculpture", "floor-decor", 0x7f372fac, "sculptureUrn"),
    ("fruit-bowl", "Bowl of Plastic Fruit", "tabletop-decor", 0x7f6146db, "foodFruitBowl"),
    ("venus", '"On A Pedestal" by Yucan Byall', "floor-decor", 0x7fccb85f, "sculptureVenus"),
    ("chimes", '"Immobile Chimes" Mobile in Steel', "floor-decor", 0x7faf6c4a, "sculptureChimes"),
    ("chi", "The My-Chi Sculpture Form", "floor-decor", 0x7fbbdbbc, "sculptureChi"),
    ("dining-chair", "Tea Party in Teak", "chair", 0x7f02cfd9, "chairDiningValue1"),
    ("dining-table", "The Talking Table", "table", 0x7f4a7676, "tableDiningQuaint"),
    ("table-lamp", "Pix-Arm Drafting Lamp", "lamp", 0x7ffd3a1e, "lightingTableValue1"),
]
VERIFIED_MESHES = {
    "venus": "8024255f9ef1e79d889f1de09c8c00558ae30e9156c1b11b67ef7d229e3ac6d6",
    "chimes": "7d34d8c7ac35a1acb7e0dfd626a032cab6f66b6b090340d8ba01510b973aad23",
    "chi": "de2faad3b892c70b6c39dc8d4af8eeca453b6119fb7a959dd1318d34a591bb94",
    "urn": "9b08d5f52cd958c10d76c0f6fb8786bd07526dbc9b21b87eb845e098fc6740f1",
    "fruit-bowl": "642d43297b98e6677d267ce59b797993ec032ca818dee16b9438258840c1180b",
    "dining-chair": "d5d0724a699b0dbe832e12a8ecdeeda0489f6246804135f0bfd68eda54c5d6f6",
    "dining-table": "1b87e5b902ba053d97b9376af2bf093521d9579c0c485c553988f6291db4dfe6",
    "table-lamp": "9db8e25fd099389291186cb76d4afbd28d4e74a64fb0ebaf18cb449ce6631fb2",
}

def unpack(b, limit=None):
    if b[4:6] != b"\x10\xfb":
        return b if limit is None else b[:limit]
    out, p = bytearray(), 9
    while p < len(b) and (limit is None or len(out) < limit):
        c = b[p]; p += 1
        if c >= 252:
            out.extend(b[p:p+(c&3)]); break
        if c >= 224:
            n = ((c&31)<<2)+4; out.extend(b[p:p+n]); p += n; continue
        if c >= 192:
            a,d,e = b[p:p+3]; p += 3
            lit,off,n = c&3,((c&16)<<12)+(a<<8)+d+1,((c&12)<<6)+e+5
        elif c >= 128:
            a,d = b[p:p+2]; p += 2
            lit,off,n = a>>6,((a&63)<<8)+d+1,(c&63)+4
        else:
            a = b[p]; p += 1
            lit,off,n = c&3,((c&96)<<3)+a+1,((c&28)>>2)+3
        out.extend(b[p:p+lit]); p += lit
        for _ in range(n): out.append(out[-off])
    if limit is None:
        assert len(out) == int.from_bytes(b[6:9], "big")
    return bytes(out)

def index(path):
    f = path.open("rb"); h = f.read(96)
    assert h[:4] == b"DBPF"
    n,p,l = struct.unpack_from("<III",h,36); width=l//n
    assert width in (20,24)
    f.seek(p); b=f.read(l); result={}
    for p in range(0,l,width):
        v=struct.unpack_from("<"+"I"*(width//4),b,p)
        result[(v[0],v[1],v[2]+((v[3]<<32) if width==24 else 0))]=v[-2:]
    return f,result

def bigstring(b,p):
    n=0;shift=0
    while True:
        c=b[p];p+=1;n|=(c&127)<<shift
        if c<128:break
        shift+=7
    return b[p:p+n].decode(),p+n

def sgname(b):
    p=b.find(b"cSGResource")
    return bigstring(b,p+19)[0] if p>=0 else None

def key(k): return f"{k[0]:08x}-{k[1]:08x}-{k[2]:016x}"
def digest(b): return hashlib.sha256(b).hexdigest()
def pack(resources):
    b=bytearray(96);b[:4]=b"DBPF"
    for p,v in [(4,1),(8,1),(32,7),(60,2)]:struct.pack_into("<I",b,p,v)
    idx=bytearray()
    for k,data in sorted(resources.items()):
        idx.extend(struct.pack("<6I",k[0],k[1],k[2]&0xffffffff,k[2]>>32,len(b),len(data)));b.extend(data)
    struct.pack_into("<III",b,36,len(resources),len(b),len(idx));b.extend(idx);return b

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument("--game-root",type=Path,required=True);p.add_argument("--output",type=Path,required=True);args=p.parse_args()
    root=args.game_root;out=args.output;out.mkdir(parents=True,exist_ok=True)
    db=root/"EP9/TSData/Res/Objects/objects.package"
    f,idx=index(db);groups={t[3] for t in TEMPLATES};private={};guids=set()
    game_groups=sorted({f"{k[1]:08x}" for k in idx})
    for k,loc in idx.items():
        if k[0]==0x4f424a44 or k[1] in groups:
            f.seek(loc[0]);b=unpack(f.read(loc[1]))
            if k[0]==0x4f424a44:guids.add(f"{struct.unpack_from('<I',b,92)[0]:08x}")
            if k[1] in groups:private[k]=b
    f.close()
    resources={t[0]:{k:b for k,b in private.items() if k[1]==t[3]} for t in TEMPLATES}
    recipes={t[0]:[{"key":key(k),"source":str(db.relative_to(root)),"sha256":digest(b)} for k,b in resources[t[0]].items()] for t in TEMPLATES}
    names={};keys=[];verified=set()
    scene_root = root/"Base/TSData/Res/Sims3D"
    for path in sorted([*scene_root.glob("Objects*.package"), scene_root/"Textures.package", scene_root/"LightRigs.package"]):
        f,idx=index(path)
        for k,loc in idx.items():
            if k[0] in (0xe86b1eef,0xfb00791e):continue
            f.seek(loc[0]);raw=f.read(loc[1]);head=unpack(raw,2048)
            try:name=sgname(head)
            except (UnicodeDecodeError,IndexError):continue
            if not name:continue
            names.setdefault(name.lower(),[]).append(key(k));keys.append(key(k))
            for ident,_,_,_,prefix in TEMPLATES:
                if name.lower().startswith(prefix.lower()) and name[len(prefix):len(prefix)+1] in ("", "_", "-", "~"):
                    # This is a candidate set. Installation follows actual scene
                    # references and removes unrelated resources before publishing.
                    b=unpack(raw);resources[ident][k]=b
                    if k[0] == 0xac4f8687 and digest(b) == VERIFIED_MESHES.get(ident):
                        verified.add(ident)
                    recipes[ident].append({"key":key(k),"source":str(path.relative_to(root)),"sha256":digest(b)})
        f.close()
        print(path.name,flush=True)
    if verified != set(VERIFIED_MESHES):
        raise ValueError("Missing or changed inspected meshes: " + ", ".join(sorted(set(VERIFIED_MESHES) - verified)))
    path=root/"Base/TSData/Res/Catalog/Materials/Materials.package";f,idx=index(path)
    object_guids={t[0]:{struct.unpack_from('<I',b,92)[0] for k,b in resources[t[0]].items() if k[0]==0x4f424a44} for t in TEMPLATES}
    for k,loc in idx.items():
        if k[0]!=0x4c697e5a:continue
        f.seek(loc[0]);b=unpack(f.read(loc[1]));pos=b.find(b'objectGUID')
        if pos<0:continue
        guid=struct.unpack_from('<I',b,pos+10)[0]
        for ident,guids_for_object in object_guids.items():
            if guid in guids_for_object:
                resources[ident][k]=b;recipes[ident].append({"key":key(k),"source":str(path.relative_to(root)),"sha256":digest(b)})
    items=[]
    for ident,label,kind,group,prefix in TEMPLATES:
        b=pack(resources[ident]);(out/(ident+'.package')).write_bytes(b)
        items.append({"id":ident,"label":label,"kind":kind,"group":f"{group:08x}","model":prefix,"sha256":digest(b),"requirements":"The Sims 2 Legacy Collection (EP9 behavior profile)","resources":recipes[ident]})
    (out/'catalog.json').write_text(json.dumps({"schema_version":1,"items":items},indent=2)+'\n')
    (out/'game.json').write_text(json.dumps({"schema_version":1,"names":names,"keys":sorted(set(keys)),"guids":sorted(guids),"groups":game_groups},separators=(',',':'))+'\n')

if __name__=="__main__":main()
