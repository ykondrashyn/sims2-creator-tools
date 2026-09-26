"""Compare on/off outputs by every decompressed resource, excluding DIR bookkeeping."""
import hashlib,json,struct,sys
from pathlib import Path
from package_creation.hair.standard_extract import unpack_refpack

def inspect(path):
 data=path.read_bytes();assert data[:4]==b'DBPF'
 count,offset,length=struct.unpack_from('<III',data,36);stride=length//count
 assert stride in [20,24] and stride*count==length
 resources={};compressed=0
 for i in range(offset,offset+length,stride):
  row=struct.unpack_from('<'+'I'*(stride//4),data,i)
  if row[0]==0xe86b1eef:continue
  key=tuple(row[:-2]);stored=data[row[-2]:row[-2]+row[-1]];assert len(stored)==row[-1]
  compressed+=stored[4:6]==b'\x10\xfb'
  assert key not in resources
  resources[key]=hashlib.sha256(unpack_refpack(stored)).hexdigest()
 return resources,compressed,len(data)

def main(root):
 pairs=[]
 for group,a,b,pattern in [
  ('hair/tattoo','hair-tattoo/wasm-parity','hair-tattoo-off/wasm-parity','*.package'),
  ('object','objects-on','objects-off','*.package'),
  ('painting','paintings-on','paintings-off','*.package'),
  ('sim','sim-on','sim-off','humanoid-wasm.package')]:
  for off in sorted((root/b).glob(pattern)):
   on=root/a/off.name
   if not on.exists() or 'template' in off.name:continue
   x,cx,sx=inspect(on);y,cy,sy=inspect(off)
   assert x==y,(group,off.name,'resource bytes differ')
   assert cy==0,(group,off.name,'disabled output still uses RefPack')
   assert sx<=sy,(group,off.name,'compression made output larger')
   pairs.append(dict(tool=group,name=off.name,enabled_bytes=sx,disabled_bytes=sy,resources=len(x),compressed_resources=cx,all_decompressed_bytes_identical=True))
 assert len(pairs)>=30,len(pairs)
 (root/'resource-comparisons.json').write_text(json.dumps(pairs,indent=2))
 print('PASS',len(pairs),'complete on/off resource-map comparisons')
 for r in pairs:
  if r['name'] in ['Rose_Dynamite.package','parity-am-af.package','twenty-tattoos.package','parity-urn-model.package','lady-png-fill.package','humanoid-wasm.package']:print(r)
if __name__=='__main__':main(Path(sys.argv[1] if len(sys.argv)>1 else 'artifacts/refpack-option'))
