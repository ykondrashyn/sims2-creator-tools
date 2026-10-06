# Static release and GitHub Pages

The default branch is `ekondrashyn/static-release` in the public repository
`ykondrashyn/sims2-creator-tools`. GitHub Actions verifies the committed `site/`
artifact and deploys it to `https://ykondrashyn.github.io/sims2-creator-tools/`.
No inference, package building, Python service or game installation runs in Actions.

Build on the reference-equipped development machine:

```sh
PROJECT_FIXTURE_ROOT=/path/to/reference-checkout npm run build:static
python3 -m tools.static_release verify
```

This always compiles the checked-out native and WASM engine and frontend. There is
no skip-compile publication path. Use the pinned environment in development.md.
Model exports are hash-checked in `tools/local-upscale/models.json`.

Commit source and the validated `site/` together. `site-integrity.json` records all
published files. The verifier rejects changed source, missing or changed files,
unsafe paths, server API dependencies, files of 100 MiB or more, and a site of
1,000,000,000 bytes or more. These budgets include retained immutable assets.
The builder retains older assets and manifests instead of deleting saved-job
engines. Inspect total size before every publication.

Assets have content-addressed names and MIME extensions. The entry page uses
relative paths and a versioned manifest, with a same-origin CSP. Test using a plain
static server at both `/` and `/sims2-creator-tools/`. GitHub Pages serves original
bytes, without depending on dynamic gzip negotiation. All model data remains local.

Rollback: choose an earlier successful Pages deployment in GitHub Actions and
rerun its deployment, or restore an entire validated source and site snapshot to
the deployment branch. Keep newer immutable assets if users have saved jobs using
them. Never roll back only the HTML or only the WASM engine. Verify the complete
snapshot before pushing. No LAN service restart is involved.
