# Shared creator engine and reference tools

The current application has six browser tools, described in the
[project README](../README.md). One Rust crate implements package rules and body
conversion, exposed through native wrappers and WebAssembly protocol version 1.

- [Hair rules and palette processing](hair/README.md)
- [Object templates and imported geometry](objects/README.md)
- [Painting composition and frames](paintings/README.md)
- [Experimental Sim fitting and export gates](sims/README.md)
- [Body conversion reference](conversion/README.md)
- [Runtime and saved work](service/PACKAGE_RUNTIME.md)
- [Shared architecture](../docs/architecture.md)

`harness.py`, `hair/service.py` and `tests/reference` are offline regression
oracles. The production service only delivers assets and historical downloads.
Retired creation endpoints return HTTP 410 without parsing uploaded files.
Use the root `tools.project` entrypoint to build and verify fresh artifacts.

The original multi-package harness documentation is retained as a
[historical reference](../docs/history/native-package-harness.md).
