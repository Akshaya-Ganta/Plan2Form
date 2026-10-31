
"""Poly Haven door/window catalogue and GLTF cache for Archscale."""
from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query

API = "https://api.polyhaven.com"
UA = "Archscale/1.0 (Architectural Openings; contact: archscale project)"

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "viewer" / "openings_cache"
CACHE.mkdir(parents=True, exist_ok=True)

router = APIRouter(tags=["openings"])

SEARCH_TERMS = {
    "door": ["door", "wooden door", "interior door"],
    "window": ["window", "window frame", "glass window"],
}


def req_json(url: str):
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": UA,
            "Accept": "application/json",
        },
    )

    try:
        with urllib.request.urlopen(request, timeout=35) as response:
            return json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Poly Haven request failed: {type(exc).__name__}",
        ) from exc


def valid_id(asset_id: str):
    if not re.fullmatch(r"[a-zA-Z0-9_-]{1,120}", asset_id):
        raise HTTPException(status_code=400, detail="Invalid asset ID")


def has_gltf(tree):
    try:
        record = tree["gltf"]["1k"]["gltf"]
        return bool(record.get("url"))
    except (KeyError, TypeError):
        return False


@router.get("/openings/search")
def search_openings(
    kind: str = Query(..., pattern="^(door|window)$"),
    q: str = Query(default="", max_length=100),
    limit: int = Query(default=12, ge=1, le=20),
):
    terms = [q.strip()] if q.strip() else SEARCH_TERMS[kind]

    assets = req_json(f"{API}/assets?type=models")
    results = []
    seen = set()

    for term in terms:
        params = urllib.parse.urlencode({
            "q": term,
            "t": "models",
            "limit": 50,
        })

        found = req_json(f"{API}/search?{params}")

        for result in found.get("results", []):
            slug = result.get("slug")

            if not slug or slug in seen:
                continue

            seen.add(slug)
            metadata = assets.get(slug)

            if not metadata:
                continue

            # Avoid returning unrelated models.
            text = " ".join([
                metadata.get("name", ""),
                metadata.get("description", ""),
                metadata.get("category", ""),
                " ".join(metadata.get("tags", [])),
            ]).lower()

            keywords = (
                ["door", "doorway", "entrance"]
                if kind == "door"
                else ["window", "glazing", "window frame"]
            )

            if not any(word in text for word in keywords):
                continue

            # Only return models with the GLTF format used by our viewer.
            try:
                files = req_json(
                    f"{API}/files/{urllib.parse.quote(slug, safe='')}"
                )
            except HTTPException:
                continue

            if not has_gltf(files):
                continue

            results.append({
                "id": slug,
                "name": metadata.get("name", slug),
                "thumbnail_url": metadata.get("thumbnail_url"),
                "category": metadata.get("category", ""),
                "credit": "Poly Haven (CC0)",
            })

            if len(results) >= limit:
                break

        if len(results) >= limit:
            break

    return {
        "kind": kind,
        "results": results,
        "total": len(results),
        "credit": "Powered by Poly Haven",
    }


@router.post("/openings/{asset_id}/download")
def download_opening(asset_id: str):
    valid_id(asset_id)

    tree = req_json(
        f"{API}/files/{urllib.parse.quote(asset_id, safe='')}"
    )

    try:
        record = tree["gltf"]["1k"]["gltf"]
    except (KeyError, TypeError):
        raise HTTPException(
            status_code=404,
            detail="This asset does not provide a 1K GLTF model",
        )

    model_url = record.get("url")
    includes = record.get("include", {})

    if not model_url or not model_url.startswith(
        "https://dl.polyhaven.org/"
    ):
        raise HTTPException(
            status_code=502,
            detail="Invalid Poly Haven model URL",
        )

    folder = (CACHE / asset_id).resolve()
    folder.mkdir(parents=True, exist_ok=True)

    def download_file(url: str, destination: Path, expected_size=None):
        if not url.startswith("https://dl.polyhaven.org/"):
            raise ValueError("Untrusted download URL")

        destination = destination.resolve()
        destination.relative_to(folder)
        destination.parent.mkdir(parents=True, exist_ok=True)

        if (
            destination.exists()
            and expected_size
            and destination.stat().st_size == expected_size
        ):
            return

        request = urllib.request.Request(
            url,
            headers={"User-Agent": UA},
        )

        with urllib.request.urlopen(request, timeout=120) as response:
            data = response.read()

        if not data:
            raise ValueError("Downloaded file is empty")

        if expected_size and len(data) != expected_size:
            raise ValueError("Downloaded file size mismatch")

        destination.write_bytes(data)

    try:
        download_file(
            model_url,
            folder / "model.gltf",
            record.get("size"),
        )

        for relative_path, dependency in includes.items():
            relative = Path(relative_path)

            if relative.is_absolute() or ".." in relative.parts:
                raise ValueError("Invalid dependency path")

            download_file(
                dependency["url"],
                folder / relative,
                dependency.get("size"),
            )

    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Opening download failed: {type(exc).__name__}: {exc}",
        ) from exc

    return {
        "id": asset_id,
        "model_url": f"/openings-cache/{asset_id}/model.gltf",
        "format": "gltf",
        "resolution": "1k",
        "credit": "Poly Haven (CC0)",
    }
