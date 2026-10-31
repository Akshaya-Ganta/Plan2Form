
"""Poly Haven material catalogue and local PBR texture cache for Archscale."""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query


API_BASE = "https://api.polyhaven.com"
USER_AGENT = "Archscale/1.0 (Wall Material Browser; contact: archscale project)"

REPO_ROOT = Path(__file__).resolve().parent.parent
MATERIAL_CACHE_DIR = REPO_ROOT / "viewer" / "material_cache"
MATERIAL_CACHE_DIR.mkdir(parents=True, exist_ok=True)

router = APIRouter(tags=["materials"])


# ---------------------------------------------------------
# POLY HAVEN API HELPERS
# ---------------------------------------------------------

def _request_json(url: str):
    """Request JSON data from the Poly Haven API."""

    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
        },
    )

    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))

    except urllib.error.HTTPError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Poly Haven API returned HTTP {exc.code}",
        ) from exc

    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Could not reach Poly Haven: {type(exc).__name__}",
        ) from exc


def _asset_info(slug: str):
    """Fetch information about one material."""

    encoded_slug = urllib.parse.quote(slug, safe="")
    return _request_json(f"{API_BASE}/info/{encoded_slug}")


# ---------------------------------------------------------
# MATERIAL SEARCH ENDPOINT
# ---------------------------------------------------------

@router.get("/materials/search")
def search_materials(
    q: str = Query(default="marble", min_length=1, max_length=100),
    limit: int = Query(default=18, ge=1, le=30),
):
    """Search Poly Haven texture assets."""

    query = q.strip().lower()

    if not query:
        raise HTTPException(
            status_code=400,
            detail="Search query cannot be empty",
        )

    params = urllib.parse.urlencode({
        "q": query,
        "t": "textures",
        "limit": limit,
    })

    search_data = _request_json(
        f"{API_BASE}/search?{params}"
    )

    ranked = search_data.get("results", [])[:limit]

    def load_one(item):
        slug = item.get("slug")

        if not slug:
            return None

        info = _asset_info(slug)

        return {
            "id": slug,
            "name": info.get(
                "name",
                slug.replace("_", " ").title(),
            ),
            "description": info.get("description", ""),
            "thumbnail_url": info.get("thumbnail_url"),
            "category": info.get("category", ""),
            "tags": info.get("tags", []),
            "max_resolution": info.get("max_resolution"),
            "score": item.get("score"),
        }

    with ThreadPoolExecutor(max_workers=6) as pool:
        results = [
            item
            for item in pool.map(load_one, ranked)
            if item
        ]

    return {
        "query": search_data.get("query", query),
        "total": search_data.get("total", len(results)),
        "results": results,
        "credit": "Powered by Poly Haven",
    }


# ---------------------------------------------------------
# RAW MATERIAL FILE METADATA ENDPOINT
# ---------------------------------------------------------

@router.get("/materials/{asset_id}/files")
def material_files(asset_id: str):
    """Return the raw file metadata for a material."""

    if not re.fullmatch(r"[a-zA-Z0-9_-]{1,120}", asset_id):
        raise HTTPException(
            status_code=400,
            detail="Invalid material ID",
        )

    encoded_id = urllib.parse.quote(asset_id, safe="")

    return _request_json(
        f"{API_BASE}/files/{encoded_id}"
    )


# ---------------------------------------------------------
# RECURSIVE FILE METADATA PARSER
# ---------------------------------------------------------

def _walk_files(node, path=""):
    """
    Recursively find downloadable file records.

    Handles:
    - Direct texture maps
    - Diffuse / AO / Rough categories
    - Nested blend includes
    - Nested glTF includes
    - Nested MaterialX includes
    """

    if isinstance(node, dict):

        # A downloadable file record contains a URL.
        if isinstance(node.get("url"), str):
            yield path, node

        # Continue searching other fields too.
        # Important: do NOT return after finding a URL.
        # Some records contain nested 'include' dictionaries.
        for key, value in node.items():

            if key == "url":
                continue

            if isinstance(value, (dict, list)):
                next_path = (
                    f"{path}/{key}" if path else key
                )

                yield from _walk_files(value, next_path)

    elif isinstance(node, list):

        for index, value in enumerate(node):

            yield from _walk_files(
                value,
                f"{path}/{index}",
            )


# ---------------------------------------------------------
# PBR TEXTURE SELECTION
# ---------------------------------------------------------

def _choose_map(
    file_tree,
    resolution: str,
    tokens: tuple[str, ...],
):
    """
    Select a browser-compatible PBR texture.

    Searches both metadata paths and actual file URLs.

    Supports JPG and PNG because the browser's standard
    Three.js TextureLoader cannot directly load EXR files.
    """

    candidates = []

    for path, record in _walk_files(file_tree):

        url = record.get("url", "")

        if not isinstance(url, str):
            continue

        parsed_url = urllib.parse.urlparse(url)
        url_path = parsed_url.path.lower()

        # Only allow downloads from Poly Haven.
        if parsed_url.scheme != "https":
            continue

        if parsed_url.netloc.lower() != "dl.polyhaven.org":
            continue

        # Search both the metadata category and filename.
        searchable = f"{path}/{url_path}".lower()

        if not any(
            token.lower() in searchable
            for token in tokens
        ):
            continue

        # Resolution is checked against the actual filename.
        if f"_{resolution}." not in url_path:
            continue

        # Browser-compatible formats only.
        if not url_path.endswith((".jpg", ".jpeg", ".png")):
            continue

        # Prefer JPEG, then PNG.
        if url_path.endswith((".jpg", ".jpeg")):
            score = 0
        else:
            score = 1

        candidates.append(
            (score, path, record)
        )

    if not candidates:
        return None

    candidates.sort(
        key=lambda item: item[0]
    )

    return candidates[0][2]


# ---------------------------------------------------------
# TEXTURE DOWNLOAD
# ---------------------------------------------------------

def _download(url: str, destination: Path):
    """Download a texture into the local material cache."""

    parsed_url = urllib.parse.urlparse(url)

    if (
        parsed_url.scheme != "https"
        or parsed_url.netloc.lower() != "dl.polyhaven.org"
    ):
        raise HTTPException(
            status_code=400,
            detail="Untrusted texture download host",
        )

    destination.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
        },
    )

    try:
        with urllib.request.urlopen(
            request,
            timeout=90,
        ) as response:

            data = response.read()

        if not data:
            raise ValueError("Empty texture file")

        destination.write_bytes(data)

    except Exception as exc:

        destination.unlink(missing_ok=True)

        raise HTTPException(
            status_code=502,
            detail=f"Texture download failed: {type(exc).__name__}",
        ) from exc


# ---------------------------------------------------------
# MATERIAL DOWNLOAD ENDPOINT
# ---------------------------------------------------------

@router.post("/materials/{asset_id}/download")
def download_material(
    asset_id: str,
    resolution: str = Query(
        default="2k",
        pattern="^(1k|2k|4k|8k)$",
    ),
):
    """
    Download PBR maps for a Poly Haven material.

    Returns local URLs for:
    - Color
    - Normal
    - Roughness
    - Ambient occlusion
    """

    if not re.fullmatch(r"[a-zA-Z0-9_-]{1,120}", asset_id):
        raise HTTPException(
            status_code=400,
            detail="Invalid material ID",
        )

    encoded_id = urllib.parse.quote(
        asset_id,
        safe="",
    )

    tree = _request_json(
        f"{API_BASE}/files/{encoded_id}"
    )

    # Map names and their possible metadata/file-name tokens.
    definitions = {
        "color": (
            "diff",
            "diffuse",
            "basecolor",
            "albedo",
        ),

        "normal": (
            "nor_gl",
            "normal",
        ),

        "roughness": (
            "rough",
        ),

        "ao": (
            "_ao_",
            "/ao/",
            "ambient",
        ),
    }

    output = {}

    folder = (
        MATERIAL_CACHE_DIR
        / asset_id
        / resolution
    )

    for map_name, tokens in definitions.items():

        record = _choose_map(
            tree,
            resolution,
            tokens,
        )

        if not record:
            continue

        url = record["url"]

        suffix = (
            Path(
                urllib.parse.urlparse(url).path
            ).suffix
            or ".jpg"
        )

        destination = folder / f"{map_name}{suffix}"

        expected_size = record.get("size")

        # Reuse a cached texture if its size matches.
        cache_is_valid = (
            destination.exists()
            and destination.stat().st_size > 0
            and (
                expected_size is None
                or destination.stat().st_size == expected_size
            )
        )

        if not cache_is_valid:
            _download(
                url,
                destination,
            )

        output[map_name] = (
            f"/material-cache/{asset_id}/"
            f"{resolution}/{destination.name}"
        )

    # A material must have a colour map to be applied.
    if "color" not in output:

        raise HTTPException(
            status_code=404,
            detail=(
                f"No colour map available for "
                f"{asset_id} at {resolution}"
            ),
        )

    return {
        "id": asset_id,
        "resolution": resolution,
        "maps": output,
        "credit": "Poly Haven (CC0)",
    }
