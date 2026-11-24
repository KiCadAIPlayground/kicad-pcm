#!/usr/bin/env python3
"""Build a KiCad PCM repository into dist/ from manifest.yaml; `--check` validates dist/."""
import hashlib, io, json, re, sys, tarfile, time, urllib.request, zipfile
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent
DIST, CACHE = ROOT / "dist", ROOT / ".cache"
SCHEMA_URL = "https://go.kicad.org/pcm/schemas/v1"
ID_PREFIX = "dev.gedicke."
ZIP_TIME = (2026, 1, 1, 0, 0, 0)  # fixed so identical input gives an identical zip


def sha256(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def fetch_tarball(repo: str, sha: str) -> bytes:
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        sys.exit(f"{repo}: sha must be 40 lowercase hex chars, got {sha!r}")
    f = CACHE / f"{repo.replace('/', '__')}-{sha}.tar.gz"
    if not f.exists():
        CACHE.mkdir(exist_ok=True)
        with urllib.request.urlopen(f"https://codeload.github.com/{repo}/tar.gz/{sha}", timeout=120) as r:
            f.write_bytes(r.read())
    return f.read_bytes()


def collect(pkg: dict) -> dict[str, bytes]:
    """Return {zip path: bytes} for plugins/ and resources/icon.png from the pinned tarball."""
    base = pkg["path"].strip("/")
    base = "" if base == "." else base + "/"
    skip = set(pkg.get("exclude", []))
    files, icon = {}, None
    with tarfile.open(fileobj=io.BytesIO(fetch_tarball(pkg["repo"], pkg["sha"]))) as tf:
        for m in tf:
            if not m.isfile():
                continue
            rel = m.name.split("/", 1)[1]  # drop codeload's <repo>-<sha>/ prefix
            data = lambda: tf.extractfile(m).read()
            if rel == pkg.get("icon"):
                icon = data()
            if not rel.startswith(base):
                continue
            sub = rel[len(base):]
            parts = sub.split("/")
            if parts[0] in skip or any(p.startswith(".") or p == "__pycache__" for p in parts):
                continue
            files["plugins/" + sub] = data()
    if not files:
        sys.exit(f"{pkg['id']}: nothing found under path {pkg['path']!r}")
    if pkg.get("icon") and icon is None:
        sys.exit(f"{pkg['id']}: icon {pkg['icon']!r} not found in repo")
    if icon:
        files["resources/icon.png"] = icon
    return files


def metadata(pkg: dict) -> dict:
    ver = {"version": str(pkg["version"]), "status": "stable", "kicad_version": pkg["kicad_version"],
           "runtime": pkg.get("runtime", "swig")}
    if "kicad_version_max" in pkg:
        ver["kicad_version_max"] = pkg["kicad_version_max"]
    home = "https://github.com/" + pkg["repo"]
    return {
        "$schema": SCHEMA_URL,
        "name": pkg["name"],
        "description": pkg["description"],
        "description_full": pkg.get("description_full", pkg["description"]),
        "identifier": ID_PREFIX + pkg["id"],
        "type": pkg["type"],
        "author": {"name": pkg["author"], "contact": {"web": home}},
        "license": pkg["license"],
        "resources": {"homepage": home},
        "tags": pkg.get("tags", [pkg["id"]]),
        "versions": [ver],
    }


def write_zip(files: dict[str, bytes], meta: dict) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in {"metadata.json": json.dumps(meta, indent=2).encode(), **dict(sorted(files.items()))}.items():
            zi = zipfile.ZipInfo(name, ZIP_TIME)
            zi.external_attr = 0o644 << 16
            zi.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(zi, data)
    return buf.getvalue()


def stamp(url: str, blob: bytes, now: int) -> dict:
    return {"url": url, "sha256": sha256(blob), "update_timestamp": now,
            "update_time_utc": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(now))}


def build() -> None:
    man = yaml.safe_load((ROOT / "manifest.yaml").read_text())
    repo, base = man["repository"], man["repository"]["base_url"].rstrip("/")
    (DIST / "packages").mkdir(parents=True, exist_ok=True)
    for old in DIST.glob("packages/*.zip"):
        old.unlink()
    now, packages, icons = int(time.time()), [], io.BytesIO()
    ids = [p["id"] for p in man["packages"]]
    assert len(ids) == len(set(ids)), "duplicate package id in manifest.yaml"
    with zipfile.ZipFile(icons, "w", zipfile.ZIP_DEFLATED) as rz:
        for pkg in man["packages"]:
            files, meta = collect(pkg), metadata(pkg)
            blob = write_zip(files, meta)
            fname = f"{meta['identifier']}-{pkg['version']}.zip"
            (DIST / "packages" / fname).write_bytes(blob)
            v = meta["versions"][0]
            v.update(download_url=f"{base}/packages/{fname}", download_sha256=sha256(blob),
                     download_size=len(blob), install_size=sum(len(d) for d in files.values()))
            packages.append(meta)
            if "resources/icon.png" in files:
                rz.writestr(zipfile.ZipInfo(f"{meta['identifier']}/icon.png", ZIP_TIME), files["resources/icon.png"])
            print(f"{fname}: {len(blob)} bytes zip, {len(files)} files")
    pj = json.dumps({"packages": packages}, indent=2).encode()
    (DIST / "packages.json").write_bytes(pj)
    (DIST / "resources.zip").write_bytes(icons.getvalue())
    rj = {"$schema": SCHEMA_URL, "name": repo["name"],
          "maintainer": {"name": repo["maintainer"]["name"], "contact": {"web": repo["maintainer"]["web"]}},
          "packages": stamp(f"{base}/packages.json", pj, now),
          "resources": stamp(f"{base}/resources.zip", icons.getvalue(), now)}
    (DIST / "repository.json").write_text(json.dumps(rj, indent=2))
    (DIST / "CNAME").write_text(base.split("//", 1)[1] + "\n")


def check() -> int:
    errs = []
    schema = json.loads((ROOT / "schema" / "pcm.v1.schema.json").read_text())
    try:
        import jsonschema

        def validate(doc, name):
            v = jsonschema.Draft7Validator({"definitions": schema["definitions"], "$ref": f"#/definitions/{name}"})
            errs.extend(f"{name}: {'/'.join(map(str, e.absolute_path))}: {e.message}" for e in v.iter_errors(doc))
    except ImportError:
        print("jsonschema not installed: using minimal required-field check")

        def validate(doc, name):
            if name == "PackageArray":
                for p in doc["packages"]:
                    validate(p, "Package")
                return
            for k in schema["definitions"][name]["required"]:
                if k not in doc:
                    errs.append(f"{name}: missing required field {k!r}")
            for v in doc.get("versions", []):
                errs.extend(f"{name}: version missing {k!r}" for k in ("version", "status", "kicad_version") if k not in v)

    rj = json.loads((DIST / "repository.json").read_text())
    pj_bytes = (DIST / "packages.json").read_bytes()
    pj = json.loads(pj_bytes)
    validate(rj, "Repository")
    validate(pj, "PackageArray")
    for key, f in (("packages", "packages.json"), ("resources", "resources.zip")):
        if rj[key]["sha256"] != sha256((DIST / f).read_bytes()):
            errs.append(f"repository.json {key}.sha256 does not match {f}")
    names = zipfile.ZipFile(DIST / "resources.zip").namelist()
    for p in pj["packages"]:
        v = p["versions"][0]
        zf = DIST / "packages" / v["download_url"].rsplit("/", 1)[1]
        if not zf.exists():
            errs.append(f"{zf.name}: missing")
            continue
        blob = zf.read_bytes()
        if sha256(blob) != v["download_sha256"] or len(blob) != v["download_size"]:
            errs.append(f"{zf.name}: sha256/size mismatch with packages.json")
        z = zipfile.ZipFile(zf)
        inner = json.loads(z.read("metadata.json"))
        validate(inner, "Package")
        if inner["identifier"] != p["identifier"]:
            errs.append(f"{zf.name}: inner metadata identifier differs")
        if not any(n.startswith("plugins/") for n in z.namelist()):
            errs.append(f"{zf.name}: no plugins/ entries")
        if v["install_size"] != sum(i.file_size for i in z.infolist() if i.filename != "metadata.json"):
            errs.append(f"{zf.name}: install_size mismatch")
        if "resources/icon.png" in z.namelist() and f"{p['identifier']}/icon.png" not in names:
            errs.append(f"{p['identifier']}: icon missing from resources.zip")
    if not (DIST / "CNAME").read_text().strip():
        errs.append("CNAME empty")
    for e in errs:
        print("ERROR:", e)
    print("check failed" if errs else f"check ok: {len(pj['packages'])} packages")
    return 1 if errs else 0


if __name__ == "__main__":
    if "--check" in sys.argv[1:]:
        sys.exit(check())
    build()
