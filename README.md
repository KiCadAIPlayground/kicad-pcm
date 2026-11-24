# kicad-pcm

A KiCad Plugin and Content Manager (PCM) repository, published to GitHub Pages at
<https://pcm.gedicke.dev>. It packages plugins from the `KiCadAIPlayground` forks at pinned commits.
It serves the PCM v1 format only: GitHub Pages is static, so KiCad 10's v2 format negotiation
(`Accept` header) is not available. KiCad 10 still reads v1.

## Use it in KiCad

Plugin and Content Manager -> Manage... -> add `https://pcm.gedicke.dev/repository.json` -> select it
in the repository dropdown -> install packages.

## Add or bump a package

Edit `manifest.yaml` and open a PR. To bump, change `sha` (a 40-char commit on the fork) and
`version`. To add, copy an existing entry; the field list is at the top of the file. `license` must be
one of the PCM v1 values (see `schema/pcm.v1.schema.json`). Merging to `main` rebuilds and deploys.

## Build locally

```sh
pip install pyyaml jsonschema   # jsonschema is optional; without it --check does a minimal check
python build.py                 # downloads tarballs (cached in .cache/), writes dist/
python build.py --check         # validates dist/ against the v1 schema, checks hashes and sizes
```

`dist/` contains `repository.json`, `packages.json`, `resources.zip`, `packages/*.zip` and `CNAME`.

## One-time setup (not done by this repo)

Settings -> Pages -> Source: GitHub Actions, custom domain `pcm.gedicke.dev`; DNS CNAME
`pcm` -> `kicadaiplayground.github.io`. The `CNAME` file in `dist/` is informational for
Actions-based deploys.

`schema/pcm.v1.schema.json` is vendored from <https://go.kicad.org/pcm/schemas/v1>.
