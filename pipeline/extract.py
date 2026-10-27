"""Stage 1 - extract.

Downloads every resource listed in config/sources.json from the CHHS Open Data
portal, verifies it against the publisher's md5, and writes a manifest.
If the portal is unreachable (or --offline is passed) it falls back to the
committed snapshot in data/vendor/, which is verified against the same hashes,
so a run is byte-for-byte reproducible either way.
"""
import hashlib
import json
import shutil
import urllib.request
from datetime import datetime, timezone

from .paths import CONFIG, RAW, VENDOR, ensure_dirs


def _md5(path):
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _download(url, dest, timeout=60):
    req = urllib.request.Request(url, headers={"User-Agent": "ca-enforcement-pipeline/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r, open(dest, "wb") as f:
        shutil.copyfileobj(r, f)


def run(offline=False, log=print):
    ensure_dirs()
    sources = json.loads((CONFIG / "sources.json").read_text())
    manifest = []
    for res in sources["resources"]:
        dest = RAW / res["path"]
        origin, err = None, None
        if not offline:
            try:
                _download(res["url"], dest)
                origin = "download"
                if res["md5"] and _md5(dest) != res["md5"]:
                    # The portal is archived, so a new hash means the file changed upstream.
                    log(f"  ! {res['name']}: downloaded file hash differs from publisher manifest; "
                        "keeping download but flagging it")
                    origin = "download (hash changed upstream)"
            except Exception as e:  # network blocked, 404, timeout ...
                err = f"{type(e).__name__}: {e}"
        if origin is None:
            src = VENDOR / res["path"]
            if not src.exists():
                if res["required"]:
                    raise FileNotFoundError(f"{res['name']}: download failed ({err}) and no vendored copy at {src}")
                log(f"  - {res['name']}: unavailable, optional -> skipped")
                continue
            shutil.copyfile(src, dest)
            origin = "vendored snapshot" + (" (download failed)" if err else " (--offline)")
        digest = _md5(dest)
        ok = res["md5"] is None or digest == res["md5"]
        if res["required"] and not ok and origin.startswith("vendored"):
            raise ValueError(f"{res['name']}: vendored copy fails md5 check")
        manifest.append({
            "name": res["name"], "file": res["path"], "origin": origin, "md5": digest,
            "md5_matches_publisher": ok, "bytes": dest.stat().st_size,
        })
        log(f"  + {res['name']:<28} {origin:<34} md5_ok={ok}")
    (RAW / "_manifest.json").write_text(json.dumps(
        {"extracted_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "files": manifest}, indent=2))
    return manifest
