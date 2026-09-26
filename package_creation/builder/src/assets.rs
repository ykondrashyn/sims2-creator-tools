//! Asset buffers shared by native CLI adapters and browser sessions.
//! A scoped memory store never falls through to the host filesystem.
use image::{DynamicImage, RgbaImage};
use sha2::{Digest, Sha256};
use std::{
    cell::{OnceCell, RefCell},
    collections::BTreeMap,
    io,
    path::{Path, PathBuf},
    rc::Rc,
};

struct AssetBuffer {
    bytes: Rc<Vec<u8>>,
    digest: OnceCell<String>,
    revision: u64,
}
impl AssetBuffer {
    fn digest(&self) -> &str {
        self.digest
            .get_or_init(|| format!("{:x}", Sha256::digest(self.bytes.as_slice())))
    }
}
/// Immutable buffers ensure every replacement also replaces its cached identity.
/// Keep the adapters' insert/get/remove interface, without exposing mutable bytes.
#[derive(Default)]
pub struct AssetBuffers {
    entries: BTreeMap<PathBuf, AssetBuffer>,
    revision: u64,
}
impl AssetBuffers {
    pub fn insert(&mut self, path: PathBuf, bytes: Vec<u8>) -> Option<Vec<u8>> {
        self.revision = self
            .revision
            .checked_add(1)
            .expect("Asset revision overflow");
        self.entries
            .insert(
                path,
                AssetBuffer {
                    bytes: Rc::new(bytes),
                    digest: OnceCell::new(),
                    revision: self.revision,
                },
            )
            .map(|b| Rc::unwrap_or_clone(b.bytes))
    }
    pub fn get(&self, path: &Path) -> Option<&Vec<u8>> {
        self.entries.get(path).map(|b| b.bytes.as_ref())
    }
    pub fn remove(&mut self, path: &Path) -> Option<Vec<u8>> {
        self.entries
            .remove(path)
            .map(|b| Rc::unwrap_or_clone(b.bytes))
    }
    pub fn contains_key(&self, path: &Path) -> bool {
        self.entries.contains_key(path)
    }
    pub(crate) fn digest(&self, path: &Path) -> Option<&str> {
        self.entries.get(path).map(AssetBuffer::digest)
    }
}

#[derive(Default)]
pub struct Assets {
    pub bytes: AssetBuffers,
    pub images: BTreeMap<PathBuf, Rc<RgbaImage>>,
    pub(crate) caches: crate::caches::FeatureCaches,
}
thread_local! { static MEMORY: RefCell<Option<Assets>> = const { RefCell::new(None) }; }
pub fn with<T>(assets: &mut Assets, f: impl FnOnce() -> anyhow::Result<T>) -> anyhow::Result<T> {
    MEMORY.with(|m| {
        assert!(m.borrow().is_none(), "Nested asset session");
        *m.borrow_mut() = Some(std::mem::take(assets));
    });
    let result = f();
    *assets = MEMORY.with(|m| m.borrow_mut().take().unwrap());
    result
}
pub fn image_insert(name: &str, image: RgbaImage) {
    MEMORY.with(|m| {
        m.borrow_mut()
            .as_mut()
            .unwrap()
            .images
            .insert(name.into(), Rc::new(image));
    });
}
pub fn image_get(name: &str) -> Option<RgbaImage> {
    MEMORY.with(|m| {
        m.borrow()
            .as_ref()
            .unwrap()
            .images
            .get(Path::new(name))
            .map(|image| image.as_ref().clone())
    })
}
pub fn clear_images() {
    MEMORY.with(|m| m.borrow_mut().as_mut().unwrap().images.clear());
}
pub fn trim_base_cache() {
    MEMORY.with(|m| {
        let mut m = m.borrow_mut();
        let a = m.as_mut().unwrap();
        let keys: Vec<_> = a
            .images
            .keys()
            .filter(|p| p.starts_with("base"))
            .cloned()
            .collect();
        let mut size: usize = keys.iter().map(|k| a.images[k].len()).sum();
        for key in keys {
            if size <= 128 * 1024 * 1024 {
                break;
            }
            size -= a.images.remove(&key).unwrap().len();
        }
    });
}
pub fn reset_inputs() {
    MEMORY.with(|m| {
        let mut m = m.borrow_mut();
        let a = m.as_mut().unwrap();
        a.images.clear();
        a.caches = Default::default();
        a.bytes
            .entries
            .retain(|k, _| ["palette", "game-meshes"].iter().any(|n| k == Path::new(n)));
    });
}
pub fn exists(path: impl AsRef<Path>) -> bool {
    MEMORY.with(|m| {
        m.borrow()
            .as_ref()
            .map(|a| a.bytes.contains_key(path.as_ref()) || a.images.contains_key(path.as_ref()))
            .unwrap_or_else(|| path.as_ref().exists())
    })
}
pub fn read(path: impl AsRef<Path>) -> io::Result<Vec<u8>> {
    Ok(Rc::unwrap_or_clone(read_shared(path)?))
}
/// Cache misses can parse immutable input without cloning its entire payload.
pub(crate) fn read_shared(path: impl AsRef<Path>) -> io::Result<Rc<Vec<u8>>> {
    let path = path.as_ref();
    MEMORY.with(|m| {
        if let Some(a) = &*m.borrow() {
            return a
                .bytes
                .entries
                .get(path)
                .map(|b| b.bytes.clone())
                .ok_or_else(|| {
                    io::Error::new(
                        io::ErrorKind::NotFound,
                        format!("Missing asset {}", path.display()),
                    )
                });
        }
        #[cfg(not(target_arch = "wasm32"))]
        {
            std::fs::read(path).map(Rc::new)
        }
        #[cfg(target_arch = "wasm32")]
        {
            Err(io::Error::other("No asset session"))
        }
    })
}
pub(crate) fn digest(path: impl AsRef<Path>) -> io::Result<String> {
    let path = path.as_ref();
    if let Some(digest) = MEMORY.with(|m| {
        m.borrow()
            .as_ref()
            .and_then(|a| a.bytes.digest(path).map(str::to_owned))
    }) {
        return Ok(digest);
    }
    Ok(format!(
        "{:x}",
        Sha256::digest(read_shared(path)?.as_slice())
    ))
}
pub(crate) fn cache_identity(path: impl AsRef<Path>) -> io::Result<String> {
    let path = path.as_ref();
    if let Some(identity) = MEMORY.with(|m| {
        m.borrow().as_ref().and_then(|a| {
            a.bytes
                .entries
                .get(path)
                .map(|b| format!("{}:{}", b.revision, b.digest()))
        })
    }) {
        return Ok(identity);
    }
    // Native file reads are never treated as immutable across calls.
    digest(path)
}
pub fn write(path: impl AsRef<Path>, bytes: impl AsRef<[u8]>) -> io::Result<()> {
    MEMORY.with(|m| {
        if let Some(a) = &mut *m.borrow_mut() {
            a.bytes
                .insert(path.as_ref().into(), bytes.as_ref().to_vec());
            return Ok(());
        }
        #[cfg(not(target_arch = "wasm32"))]
        {
            std::fs::write(path, bytes)
        }
        #[cfg(target_arch = "wasm32")]
        {
            Err(io::Error::other("No asset session"))
        }
    })
}
/// Move large temporary buffers without keeping a second copy in the WASM heap.
pub fn put_owned(path: impl AsRef<Path>, bytes: Vec<u8>) -> io::Result<()> {
    MEMORY.with(|m| {
        if let Some(a) = &mut *m.borrow_mut() {
            a.bytes.insert(path.as_ref().into(), bytes);
            return Ok(());
        }
        #[cfg(not(target_arch = "wasm32"))]
        {
            std::fs::write(path, bytes)
        }
        #[cfg(target_arch = "wasm32")]
        {
            Err(io::Error::other("No asset session"))
        }
    })
}
pub fn take(path: impl AsRef<Path>) -> io::Result<Vec<u8>> {
    MEMORY.with(|m| {
        if let Some(a) = &mut *m.borrow_mut() {
            return a
                .bytes
                .remove(path.as_ref())
                .ok_or_else(|| io::Error::other("Missing asset buffer"));
        }
        #[cfg(not(target_arch = "wasm32"))]
        {
            std::fs::read(path)
        }
        #[cfg(target_arch = "wasm32")]
        {
            Err(io::Error::other("No asset session"))
        }
    })
}
pub fn open_image(path: impl AsRef<Path>) -> anyhow::Result<DynamicImage> {
    if let Some(image) = MEMORY.with(|m| {
        m.borrow()
            .as_ref()
            .and_then(|a| a.images.get(path.as_ref()).cloned())
    }) {
        return Ok(DynamicImage::ImageRgba8(image.as_ref().clone()));
    }
    Ok(image::load_from_memory(&read(path)?)?)
}
pub fn save_buffer(
    path: impl AsRef<Path>,
    data: &[u8],
    w: u32,
    h: u32,
    color: image::ColorType,
) -> anyhow::Result<()> {
    let active = MEMORY.with(|m| m.borrow().is_some());
    if active {
        anyhow::ensure!(color == image::ColorType::Rgba8, "Expected RGBA texture");
        let image = RgbaImage::from_raw(w, h, data.to_vec())
            .ok_or_else(|| anyhow::anyhow!("Invalid pixel buffer"))?;
        MEMORY.with(|m| {
            m.borrow_mut()
                .as_mut()
                .unwrap()
                .images
                .insert(path.as_ref().into(), Rc::new(image));
        });
    } else {
        image::save_buffer(path, data, w, h, color)?;
    }
    Ok(())
}
pub(crate) fn save_shared_image(path: &Path, image: Rc<RgbaImage>) -> anyhow::Result<()> {
    let saved = MEMORY.with(|m| {
        if let Some(a) = m.borrow_mut().as_mut() {
            a.images.insert(path.into(), image.clone());
            true
        } else {
            false
        }
    });
    if !saved {
        image::save_buffer(
            path,
            image.as_raw(),
            image.width(),
            image.height(),
            image::ColorType::Rgba8,
        )?;
    }
    Ok(())
}
pub fn create_dir_all(path: impl AsRef<Path>) -> io::Result<()> {
    if MEMORY.with(|m| m.borrow().is_some()) {
        return Ok(());
    }
    #[cfg(not(target_arch = "wasm32"))]
    {
        std::fs::create_dir_all(path)
    }
    #[cfg(target_arch = "wasm32")]
    {
        let _ = path;
        Err(io::Error::other("No asset session"))
    }
}
#[cfg(test)]
pub use std::fs::remove_dir_all;

pub(crate) fn cache_read<T>(f: impl FnOnce(&crate::caches::FeatureCaches) -> T) -> Option<T> {
    MEMORY.with(|m| m.borrow().as_ref().map(|a| f(&a.caches)))
}
pub(crate) fn cache_write(f: impl FnOnce(&mut crate::caches::FeatureCaches)) {
    MEMORY.with(|m| {
        if let Some(a) = m.borrow_mut().as_mut() {
            f(&mut a.caches);
        }
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn immutable_bytes_and_identities_follow_every_mutation() -> anyhow::Result<()> {
        let mut assets = Assets::default();
        assets.bytes.insert("input".into(), vec![1, 2, 3]);
        with(&mut assets, || {
            let bytes = read_shared("input")?;
            assert!(Rc::ptr_eq(&bytes, &read_shared("input")?));
            let first = cache_identity("input")?;
            assert_eq!(first, cache_identity("input")?);
            assert_eq!(digest("input")?, format!("{:x}", Sha256::digest([1, 2, 3])));
            write("input", [4, 5, 6])?;
            assert_ne!(first, cache_identity("input")?);
            assert_eq!(bytes.as_slice(), &[1, 2, 3]);
            assert_eq!(read("input")?, [4, 5, 6]);
            let written = cache_identity("input")?;
            put_owned("input", vec![4, 5, 6])?;
            assert_ne!(written, cache_identity("input")?);
            let replaced = cache_identity("input")?;
            assert_eq!(take("input")?, [4, 5, 6]);
            assert!(cache_identity("input").is_err());
            put_owned("input", vec![4, 5, 6])?;
            assert_ne!(replaced, cache_identity("input")?);
            put_owned("palette", vec![9])?;
            let palette = cache_identity("palette")?;
            reset_inputs();
            assert!(digest("input").is_err());
            assert_eq!(palette, cache_identity("palette")?);
            Ok(())
        })?;
        let before = assets
            .bytes
            .digest(Path::new("palette"))
            .unwrap()
            .to_owned();
        assets.bytes.remove(Path::new("palette"));
        assert!(assets.bytes.digest(Path::new("palette")).is_none());
        assets.bytes.insert("palette".into(), vec![8]);
        assert_ne!(before, assets.bytes.digest(Path::new("palette")).unwrap());
        Ok(())
    }

    #[test]
    fn native_digests_recheck_files_and_sessions_never_fall_through() -> anyhow::Result<()> {
        let dir = tempfile::tempdir()?;
        let path = dir.path().join("asset");
        std::fs::write(&path, [1, 2, 3])?;
        let before = cache_identity(&path)?;
        std::fs::write(&path, [3, 2, 1])?;
        assert_ne!(before, cache_identity(&path)?);
        with(&mut Assets::default(), || {
            assert!(read_shared(&path).is_err());
            assert!(digest(&path).is_err());
            assert!(cache_identity(&path).is_err());
            Ok(())
        })
    }
}
