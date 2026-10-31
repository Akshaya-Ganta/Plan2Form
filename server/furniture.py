"""Poly Haven model catalogue and local GLB cache for Archscale furniture."""
from __future__ import annotations
import json, re, urllib.parse, urllib.request
from pathlib import Path
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse

API = "https://api.polyhaven.com"
UA = "Archscale/1.0 (Furniture Library; contact: archscale project)"
ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "viewer" / "furniture_cache"
CACHE.mkdir(parents=True, exist_ok=True)
router = APIRouter(tags=["furniture"])

ROOMS = {
 "living-room": ["sofa", "couch", "armchair", "chair", "table", "coffee table", "tv", "cabinet", "shelf", "lamp"],
 "bedroom": ["bed", "mattress", "wardrobe", "dresser", "nightstand", "cabinet", "lamp"],
 "kitchen": ["kitchen", "cabinet", "counter", "stove", "oven", "fridge", "refrigerator", "sink", "chair", "table"],
 "bathroom": ["bath", "bathtub", "toilet", "sink", "shower", "vanity", "towel", "mirror"],
 "dining-room": ["dining", "table", "chair", "sideboard", "cabinet"],
 "office": ["desk", "office", "chair", "bookshelf", "bookcase", "computer", "monitor", "cabinet"],
 "outdoor": ["outdoor", "balcony", "patio", "garden", "bench", "chair", "table", "plant"]
}
ROOM_LABELS = {"living-room":"Living Room", "bedroom":"Bedroom", "kitchen":"Kitchen", "bathroom":"Bathroom", "dining-room":"Dining Room", "office":"Office", "outdoor":"Balcony / Outdoor"}

def req_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept":"application/json"})
    try:
        with urllib.request.urlopen(req, timeout=35) as r: return json.loads(r.read().decode())
    except Exception as e: raise HTTPException(status_code=502, detail=f"Poly Haven request failed: {type(e).__name__}") from e

def infer_rooms(asset):
    hay = " ".join([asset.get("name", ""), asset.get("description", ""), asset.get("category", ""), " ".join(asset.get("tags", []))]).lower()
    return [room for room, words in ROOMS.items() if any(w in hay for w in words)] or ["living-room"]

@router.get("/furniture/rooms")
def furniture_rooms():
    return [{"id":k,"name":v} for k,v in ROOM_LABELS.items()]

@router.get("/furniture/search")
def search_furniture(room: str = Query(default="living-room"), q: str = Query(default="", max_length=100), limit: int = Query(default=24, ge=1, le=40)):
    if room not in ROOMS: raise HTTPException(status_code=400, detail="Unknown room category")
    query = q.strip().lower()
    if not query:
        # Room-specific natural-language prompts work better than a blank global listing.
        query = {"living-room":"sofa furniture", "bedroom":"bed furniture", "kitchen":"kitchen furniture", "bathroom":"bathroom fixtures", "dining-room":"dining table chair", "office":"office desk chair", "outdoor":"outdoor furniture"}[room]
    params = urllib.parse.urlencode({"q":query,"t":"models","limit":80})
    found = req_json(f"{API}/search?{params}")
    slugs = [x.get("slug") for x in found.get("results",[]) if x.get("slug")]
    if not slugs: return {"results":[],"total":0,"credit":"Powered by Poly Haven"}
    # Search endpoint returns ranked slugs; metadata is fetched from /assets once per request.
    assets = req_json(f"{API}/assets?type=models")
    output=[]
    for slug in slugs:
        a=assets.get(slug)
        if not a: continue
        rooms=infer_rooms(a)
        if room not in rooms: continue
        output.append({"id":slug,"name":a.get("name",slug.replace("_"," ").title()),"description":a.get("description",""),"thumbnail_url":a.get("thumbnail_url"),"category":a.get("category",""),"tags":a.get("tags",[]),"rooms":rooms})
        if len(output)>=limit: break
    return {"query":query,"total":len(output),"results":output,"credit":"Powered by Poly Haven"}


def walk(node, path=""):
    if isinstance(node,dict):
        if isinstance(node.get("url"),str): yield path,node; return
        for k,v in node.items(): yield from walk(v,f"{path}/{k}" if path else k)


@router.post("/furniture/{asset_id}/download")
def download_furniture(asset_id: str):
    if not re.fullmatch(r"[a-zA-Z0-9_-]{1,120}", asset_id):
        raise HTTPException(status_code=400, detail="Invalid model ID")

    # Fetch Poly Haven's available model files
    tree = req_json(
        f"{API}/files/{urllib.parse.quote(asset_id, safe='')}"
    )

    # Use lightweight 1K GLTF for browser rendering
    try:
        model_record = tree["gltf"]["1k"]["gltf"]
    except (KeyError, TypeError):
        raise HTTPException(
            status_code=404,
            detail="This model does not provide a 1K GLTF download"
        )

    model_url = model_record.get("url")
    includes = model_record.get("include", {})

    if not model_url or not model_url.startswith("https://dl.polyhaven.org/"):
        raise HTTPException(
            status_code=502,
            detail="Invalid Poly Haven model URL"
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

        req = urllib.request.Request(url, headers={"User-Agent": UA})

        with urllib.request.urlopen(req, timeout=120) as response:
            data = response.read()

        if not data:
            raise ValueError("Downloaded file is empty")

        if expected_size and len(data) != expected_size:
            raise ValueError("Downloaded file size does not match")

        destination.write_bytes(data)

    try:
        # Download the main GLTF file
        model_path = folder / "model.gltf"
        download_file(
            model_url,
            model_path,
            model_record.get("size")
        )

        # Download its binary and texture dependencies
        for relative_path, record in includes.items():
            relative = Path(relative_path)

            if relative.is_absolute() or ".." in relative.parts:
                raise ValueError("Invalid dependency path")

            download_file(
                record["url"],
                folder / relative,
                record.get("size")
            )

    except HTTPException:
        raise
    except Exception as error:
        raise HTTPException(
            status_code=502,
            detail=f"Furniture download failed: {type(error).__name__}: {error}"
        ) from error

    return {
        "id": asset_id,
        "model_url": f"/furniture-cache/{asset_id}/model.gltf",
        "format": "gltf",
        "resolution": "1k",
        "credit": "Poly Haven (CC0)"
    }
