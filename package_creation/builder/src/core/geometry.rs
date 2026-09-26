//! Prepared geometry shared by static objects and character fitting.
#[derive(Clone)]
pub struct Part {
    pub name: String,
    pub positions: Vec<[f32; 3]>,
    pub normals: Vec<[f32; 3]>,
    pub uvs: Vec<[f32; 2]>,
    pub indices: Vec<u32>,
    pub image: std::rc::Rc<image::RgbaImage>,
    pub cutout: bool,
    pub double_sided: bool,
}
