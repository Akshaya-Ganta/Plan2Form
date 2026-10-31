"""Local inference server: loads the model once at startup and serves the viewer plus extraction endpoints."""

from __future__ import annotations

import base64
import os
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path

import cairosvg
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from buildingcv.extract_polygons import PolygonExtractor
from buildingcv.svg_render import filtered_svg_bytes

# Default location for downloaded weights — clean clone-and-go path. Local
# training writes `.pt` to `runs/<version>/<timestamp>/`; export with
# `scripts/export_safetensors.py` and point the env var here.
DEFAULT_RUN_DIR = "weights"
DEFAULT_CKPT = "best.safetensors"
DEFAULT_DEVICE = "auto"

# CubiCasa SVGs go up to a few MB. 16 MB ceiling is generous and keeps the
# server from being a free upload sink if it ever leaves localhost.
MAX_UPLOAD_BYTES = 16 * 1024 * 1024

# Repo root, derived once at import time. Used to resolve the viewer HTML
# and the sample SVGs without depending on the cwd uvicorn was launched from.
REPO_ROOT = Path(__file__).resolve().parent.parent
VIEWER_HTML = REPO_ROOT / "viewer" / "index.html"

# Curated samples shown as "Try a sample" buttons in the viewer. Keys are
# short ids (used in the URL); values are dataset-relative paths. The set
# is small on purpose — three plans of contrasting shape are enough to
# show the viewer handles different aspect ratios and complexities.
SAMPLES: dict[str, str] = {
    "1191": "high_quality_architectural/1191",
    "4068": "high_quality_architectural/4068",
    "3676": "high_quality/3676",
}


@asynccontextmanager
async def lifespan(app: FastAPI):
    run_dir = Path(os.environ.get("BUILDINGCV_RUN_DIR", DEFAULT_RUN_DIR))
    ckpt = os.environ.get("BUILDINGCV_CKPT", DEFAULT_CKPT)
    device = os.environ.get("BUILDINGCV_DEVICE", DEFAULT_DEVICE)
    extractor = PolygonExtractor(run_dir=run_dir, ckpt=ckpt, device=device)
    print(f"[server] loaded {run_dir/ckpt} on {extractor.device} (epoch {extractor.epoch})")
    app.state.extractor = extractor
    yield


app = FastAPI(title="BuildingCV inference", lifespan=lifespan)

# Vite's dev server runs on a different port than this one (usually 5173).
# Permissive CORS is fine on localhost — the server isn't reachable
# off-host. Tighten this if you ever expose it.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/healthz")
def healthz() -> dict:
    extractor: PolygonExtractor = app.state.extractor
    return {
        "ok": True,
        "device": str(extractor.device),
        "epoch": extractor.epoch,
        "image_size": list(extractor.image_size),
        "letterbox": extractor.letterbox,
    }


@app.get("/")
def index() -> FileResponse:
    """Serve the viewer HTML at the root URL."""
    return FileResponse(VIEWER_HTML)


# The viewer loads its sample plans from `./demos/{key}.json` so it works
# unchanged on the static Pages deploy. Mount the same files here so local
# dev hits the prebuilt JSON (no inference per click) — regenerate with
# `python scripts/build_demos.py` after retraining.
DEMOS_DIR = REPO_ROOT / "viewer" / "demos"
if DEMOS_DIR.is_dir():
    app.mount("/demos", StaticFiles(directory=DEMOS_DIR), name="demos")


def _attach_input_image(svg_path: Path, result: dict) -> dict:
    """Render a transparent-background PNG of the structural SVG at content
    size and inline it as base64 on `result`. The viewer uses this as the
    floor texture under the rising 3D geometry — same content the model saw,
    so what the user sees on the floor is exactly what the polygons came from.
    """
    _, _, inner_w, inner_h = result["content_rect"]
    png = cairosvg.svg2png(
        bytestring=filtered_svg_bytes(svg_path),
        output_width=inner_w,
        output_height=inner_h,
        # No background_color → transparent PNG. The viewer renders this
        # over a tinted floor plane, so transparency lets the floor color
        # show through where the SVG has no ink.
    )
    result["input_image_b64"] = base64.b64encode(png).decode("ascii")
    return result


@app.get("/sample/{key}")
def sample(key: str) -> dict:
    """Run extraction on a server-known dataset SVG.

    Used by the viewer's "Try a sample" buttons so we don't need to send
    multi-megabyte SVGs over the wire just to render plans we already
    have on disk.
    """
    if key not in SAMPLES:
        raise HTTPException(status_code=404, detail=f"unknown sample: {key}")
    data_dir = Path(os.environ.get("BUILDINGCV_DATA_DIR", REPO_ROOT / "data" / "cubicasa5k"))
    svg_path = data_dir / SAMPLES[key] / "model.svg"
    if not svg_path.exists():
        raise HTTPException(status_code=404, detail=f"sample SVG not found: {svg_path}")
    extractor: PolygonExtractor = app.state.extractor
    return _attach_input_image(svg_path, extractor.extract(svg_path))


@app.post("/extract")
async def extract(svg: UploadFile = File(...)) -> dict:
    """Run the model on the uploaded SVG and return the polygon JSON.

    The extractor reads from a path (parses the SVG twice — once for native
    dims, once for cairosvg). Easiest is to write the upload to a tempfile
    and let it use the same code path as the CLI; the file is small (< MB
    typical) and the temp dir is RAM-backed on macOS.
    """
    raw = await svg.read()
    if not raw:
        raise HTTPException(status_code=400, detail="empty upload")
    if len(raw) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail=f"upload exceeds {MAX_UPLOAD_BYTES} bytes")

    extractor: PolygonExtractor = app.state.extractor
    with tempfile.NamedTemporaryFile(suffix=".svg", delete=False) as tmp:
        tmp.write(raw)
        tmp.flush()
        tmp_path = Path(tmp.name)
    try:
        try:
            result = extractor.extract(tmp_path)
        except Exception as e:
            # Bad SVGs (cairosvg parse failures, NaN transforms in CubiCasa)
            # land here. Return 422 so the viewer can show "this file
            # couldn't be processed" rather than "server crashed".
            raise HTTPException(status_code=422, detail=f"failed to extract: {e}") from e
        return _attach_input_image(tmp_path, result)
    finally:
        tmp_path.unlink(missing_ok=True)
