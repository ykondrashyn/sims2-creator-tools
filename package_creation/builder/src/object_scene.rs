//! Bounded scene-graph reader. Edits target parsed string and reference fields.
//! Numeric fields and unsupported editing fields retain their original bytes.
use anyhow::{bail, ensure, Context, Result};
use std::collections::BTreeMap;

pub type Key = (u32, u32, u64);
#[derive(Clone, Debug)]
pub struct Text {
    pub start: usize,
    pub end: usize,
    pub value: String,
    pub resource_kind: Option<u32>,
}
#[derive(Clone, Debug, Default)]
pub struct Scene {
    pub strings: Vec<Text>,
    pub links: Vec<(usize, Key)>,
    pub name: String,
    pub transforms: Vec<[f32; 7]>,
    pub joints: Vec<(String, u32)>,
    pub skeleton_nodes: BTreeMap<usize, (u32, Vec<usize>)>,
    pub parts: Vec<(String, String)>,
    pub parts_range: Option<(usize, usize)>,
    pub geometry: Vec<String>,
    pub geometry_lods: Vec<(u32, String)>,
    pub design_ranges: Vec<(usize, usize)>,
}
struct Reader<'a> {
    b: &'a [u8],
    p: usize,
    scene: Scene,
    depth: usize,
    block: usize,
}
impl Reader<'_> {
    fn skip(&mut self, n: usize) -> Result<()> {
        self.p = self.p.checked_add(n).context("Scene length overflow")?;
        ensure!(self.p <= self.b.len(), "Truncated scene resource");
        Ok(())
    }
    fn byte(&mut self) -> Result<u8> {
        let p = self.p;
        self.skip(1)?;
        Ok(self.b[p])
    }
    fn u32(&mut self) -> Result<u32> {
        let p = self.p;
        self.skip(4)?;
        Ok(u32::from_le_bytes(self.b[p..self.p].try_into()?))
    }
    fn count(&mut self) -> Result<usize> {
        let n = self.u32()? as usize;
        ensure!(
            n <= 65536,
            "Scene collection exceeds limits at {}: {n:x}",
            self.p - 4
        );
        Ok(n)
    }
    fn text(&mut self, editable: bool) -> Result<String> {
        let start = self.p;
        let mut n = 0usize;
        let mut shift = 0;
        loop {
            let c = self.byte()?;
            ensure!(shift < 28, "Invalid scene string length");
            n |= ((c & 127) as usize) << shift;
            if c & 128 == 0 {
                break;
            }
            shift += 7;
        }
        ensure!(n <= 65536, "Scene string exceeds limits");
        let p = self.p;
        self.skip(n)?;
        let value =
            String::from_utf8(self.b[p..self.p].to_vec()).context("Invalid UTF-8 scene string")?;
        if editable {
            self.scene.strings.push(Text {
                start,
                end: self.p,
                value: value.clone(),
                resource_kind: None,
            });
        }
        Ok(value)
    }
    fn nested(&mut self, expected: &str) -> Result<()> {
        let name = self.text(false)?;
        ensure!(name == expected, "Expected {expected}, found {name}");
        self.u32()?;
        self.body(&name)
    }
    fn extension(&mut self, depth: usize) -> Result<()> {
        ensure!(depth < 32, "Scene extension nesting exceeds limits");
        let t = self.byte()?;
        self.text(false)?;
        match t {
            2 | 3 => self.skip(4)?,
            5 => self.skip(12)?,
            6 => {
                self.text(true)?;
            }
            7 => {
                for _ in 0..self.count()? {
                    self.extension(depth + 1)?;
                }
            }
            8 => self.skip(16)?,
            9 => {
                let n = self.count()?;
                self.skip(n)?;
            }
            _ => bail!("Unsupported scene extension type {t}"),
        }
        Ok(())
    }
    fn body(&mut self, name: &str) -> Result<()> {
        self.depth += 1;
        ensure!(self.depth <= 64, "Scene nesting exceeds limits");
        let v = self.u32()?;
        match name {
            "cStandardLightBase" => {ensure!(v == 11, "Unsupported light base version");},
            "cLightT" => {ensure!([1, 11].contains(&v), "Unsupported light texture version");self.nested("cSGResource")?;},
            "cPointLight" => {
                ensure!([1, 5].contains(&v), "Unsupported point light version");
                self.nested("cStandardLightBase")?; self.nested("cSGResource")?; self.nested("cLightT")?;
                self.nested("cReferentNode")?; self.nested("cObjectGraphNode")?; self.text(false)?; self.skip(28)?;
            },
            "cSGResource"=>{ensure!(v==2,"Unsupported scene name version"); let n=self.text(true)?;if self.scene.name.is_empty(){self.scene.name=n;}},
            "cCompositionTreeNode"|"cRenderableNode"|"cBoundedNode"|"cViewerRefNodeBase"|"cReferentNode"=>{},
            "cObjectGraphNode"=>{ensure!([3,4].contains(&v),"Unsupported object graph version");let n=self.count()?;self.skip(n*6)?;if v==4 {self.text(true)?;}},
            "cResourceNode"=>{
                ensure!(v==7,"Unsupported resource node version");
                match self.byte()? {
                    1=>{self.nested("cSGResource")?; self.nested("cCompositionTreeNode")?; self.nested("cObjectGraphNode")?;let n=self.count()?;self.skip(n*6+1)?;},
                    0=>{self.nested("cObjectGraphNode")?;self.skip(6)?;}, _=>bail!("Unsupported resource node type")
                } self.skip(4)?;
            },
            "cTransformNode"=>{
                ensure!(v==7,"Unsupported transform version"); self.nested("cCompositionTreeNode")?;self.nested("cObjectGraphNode")?;
                let joint_name=self.scene.strings.last().map(|s|s.value.clone()).unwrap_or_default();
                let n=self.count()?; let mut children=Vec::new();
                for _ in 0..n {let _enabled=self.byte()?;let external=self.byte()?;let child=self.u32()?;if external==0 {children.push(child as usize);}}
                let p=self.p; self.skip(28)?; let joint=self.u32()?; self.scene.transforms.push(std::array::from_fn(|i| f32::from_le_bytes(self.b[p+i*4..p+i*4+4].try_into().unwrap())));
                self.scene.skeleton_nodes.insert(self.block,(joint,children));
                if joint != 0x7fffffff {self.scene.joints.push((joint_name,joint));}
            },
            "cShapeRefNode"=>{
                ensure!([20,21].contains(&v),"Unsupported shape reference version");
                self.nested("cRenderableNode")?; self.nested("cBoundedNode")?; self.nested("cTransformNode")?;
                self.skip(6)?;self.text(true)?;self.skip(5)?;let n=self.count()?;self.skip(n*6+4)?;
                let n=self.count()?;self.skip(n*4)?;if v==21 {for _ in 0..n {self.text(true)?;}}
                let n=self.count()?;self.skip(n+4)?;
            },
            "cLightRefNode"=>{
                self.nested("cRenderableNode")?;self.nested("cBoundedNode")?;self.nested("cTransformNode")?;
                self.skip(2)?;for _ in 0..self.count()? {self.text(true)?;}self.skip(13)?;
            },
            "cViewerRefNode"|"cViewerRefNodeRecursive"=>{
                self.nested("cViewerRefNodeBase")?; self.nested("cRenderableNode")?;self.nested("cBoundedNode")?;self.nested("cTransformNode")?;
                self.skip(2)?;for _ in 0..self.count()? {self.text(true)?;}
                if name=="cViewerRefNode" {self.skip(160)?;} else {self.skip(6)?;self.text(true)?;self.skip(64)?;}
            },
            "cGeometryNode"=>{
                ensure!([11,12].contains(&v),"Unsupported geometry node version");
                self.nested("cObjectGraphNode")?;self.nested("cSGResource")?;if v==11{self.skip(2)?;}self.skip(3)?;
                let n=self.count()?;ensure!(n==0,"Inline geometry extensions require preparation in SimPE");
            },
            "cShape"=>{
                ensure!([6,7,8].contains(&v),"Unsupported shape version");
                self.nested("cSGResource")?;self.nested("cReferentNode")?;self.nested("cObjectGraphNode")?;
                if v!=6{let n=self.count()?;self.skip(n*4)?;}
                for _ in 0..self.count()? {let lod=self.u32()?;self.skip(1)?;if v<=7{self.skip(5)?;}else{let n=self.text(true)?;self.scene.strings.last_mut().unwrap().resource_kind=Some(0x7ba3838c);self.scene.geometry.push(n.clone());self.scene.geometry_lods.push((lod,n));}}
                let start=self.p;let n=self.count()?;ensure!(n<=64,"Use at most 64 object subsets");
                for _ in 0..n {let subset=self.text(false)?;let material=self.text(true)?;self.scene.strings.last_mut().unwrap().resource_kind=Some(0x49596978);self.skip(9)?;self.scene.parts.push((subset,material));}
                self.scene.parts_range=Some((start,self.p));
            },
            "cDataListExtension"|"cBoneDataExtension"=>{
                let n=self.text(false)?;ensure!(n=="cExtension","Invalid scene extension");self.u32()?;let ev=self.u32()?;let t=self.byte()?;
                if t>=7 {let name=self.text(false)?;let start=self.p;for _ in 0..self.count()?{self.extension(0)?;}if ["tsDesignModeEnabled","tsDesignModeSlaveSubsets","tsMaterialsMeshName"].contains(&name.as_str()){self.scene.design_ranges.push((start,self.p));}}
                else {let size=if t<=3&&v==4{31}else if t<=3&&ev==3{if v==5{31}else{15}}else if t==3&&v!=4{16}else{31};self.skip(size)?;}
            },
            _=>bail!("Unsupported scene block {name}. Choose a supported template or prepare this object in SimPE")
        }
        self.depth -= 1;
        Ok(())
    }
}
pub fn read(bytes: &[u8]) -> Result<Scene> {
    let mut r = Reader {
        b: bytes,
        p: 0,
        scene: Scene::default(),
        depth: 0,
        block: 0,
    };
    let marker = r.u32()?;
    let modern = marker == 0xffff0001;
    let n = if modern { r.count()? } else { marker as usize };
    ensure!(n <= 4096, "Too many scene references");
    for _ in 0..n {
        let p = r.p;
        let g = r.u32()?;
        let low = r.u32()?;
        let high = if modern { r.u32()? } else { 0 };
        let t = r.u32()?;
        r.scene
            .links
            .push((p, (t, g, (high as u64) << 32 | low as u64)));
    }
    let n = r.count()?;
    ensure!(n <= 4096, "Too many scene blocks");
    let mut kinds = Vec::new();
    for _ in 0..n {
        kinds.push(r.u32()?);
    }
    for (block, k) in kinds.into_iter().enumerate() {
        r.block = block;
        let name = r.text(false)?;
        ensure!(r.u32()? == k, "Scene block index mismatch");
        r.body(&name)
            .with_context(|| format!("Block {name} at byte {}", r.p))?;
    }
    ensure!(r.p == bytes.len(), "Unparsed scene resource data");
    Ok(r.scene)
}
pub fn string(s: &str) -> Vec<u8> {
    let mut b = vec![];
    let mut n = s.len();
    loop {
        let next = n & 127;
        n >>= 7;
        b.push((next | if n > 0 { 128 } else { 0 }) as u8);
        if n == 0 {
            break;
        }
    }
    b.extend(s.as_bytes());
    b
}
pub fn rewrite(
    bytes: &[u8],
    s: &Scene,
    names: &BTreeMap<String, String>,
    keys: &BTreeMap<Key, Key>,
) -> Result<Vec<u8>> {
    let mut edits = Vec::new();
    for t in &s.strings {
        let key = t
            .resource_kind
            .map(|kind| format!("{kind:08x}:{}", t.value.to_ascii_lowercase()))
            .unwrap_or_else(|| t.value.to_ascii_lowercase());
        if let Some(v) = names.get(&key) {
            edits.push((t.start, t.end, string(v)));
        }
    }
    let modern = bytes.starts_with(&0xffff0001u32.to_le_bytes());
    for (p, k) in &s.links {
        if let Some(n) = keys.get(k) {
            let mut b = vec![];
            b.extend(n.1.to_le_bytes());
            b.extend((n.2 as u32).to_le_bytes());
            if modern {
                b.extend((n.2 >> 32).to_le_bytes()[..4].iter());
            } else {
                ensure!(n.2 >> 32 == 0, "Cannot represent this legacy scene link");
            }
            b.extend(n.0.to_le_bytes());
            edits.push((*p, *p + if modern { 16 } else { 12 }, b));
        }
    }
    edits.sort_by_key(|e| e.0);
    ensure!(
        edits.windows(2).all(|e| e[0].1 <= e[1].0),
        "Overlapping scene edits"
    );
    let mut b = bytes.to_vec();
    for (a, z, v) in edits.into_iter().rev() {
        b.splice(a..z, v);
    }
    Ok(b)
}
