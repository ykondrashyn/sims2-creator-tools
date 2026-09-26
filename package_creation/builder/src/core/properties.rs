//! Generic CPF editing preserves unknown properties.
use anyhow::{anyhow, bail, ensure, Result};
use dbpf::internal_file::cpf::{Data, Item, CPF};
pub(crate) fn get<'a>(p: &'a CPF, n: &str) -> Result<&'a Data> {
    let mut it = p
        .entries
        .iter()
        .filter(|e| String::from_utf8_lossy(&e.name) == n);
    let e = it.next().ok_or_else(|| anyhow!("Missing property {n}"))?;
    ensure!(it.next().is_none(), "Duplicate property {n}");
    Ok(&e.data)
}
pub(crate) fn number(p: &CPF, n: &str) -> Result<u32> {
    match get(p, n)? {
        Data::UInt(v) => Ok(*v),
        Data::Int(v) => Ok(*v as u32),
        _ => bail!("Invalid integer {n}"),
    }
}
pub(crate) fn string(p: &CPF, n: &str) -> Result<String> {
    match get(p, n)? {
        Data::String(v) => Ok(String::from_utf8(v.to_vec())?),
        _ => bail!("Invalid string {n}"),
    }
}
pub(crate) fn put(p: &mut CPF, n: &str, v: Data) {
    if let Some(e) = p
        .entries
        .iter_mut()
        .find(|e| String::from_utf8_lossy(&e.name) == n)
    {
        e.data = v;
    } else {
        p.entries.push(Item::new(n, v));
    }
}
pub(crate) fn text_prop(p: &mut CPF, n: &str, v: &str) {
    put(p, n, Data::String(v.into()));
}
pub(crate) fn uint(p: &mut CPF, n: &str, v: u32) {
    put(p, n, Data::UInt(v));
}
