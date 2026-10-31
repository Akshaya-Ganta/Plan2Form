"""Local inference server for SVG and raster floor-plan uploads."""

from __future__ import annotations

import base64
import io
import os
import tempfile
import xml.etree.ElementTree as ET
from contextlib import asynccontextmanager
from pathlib import Path
from dotenv import load_dotenv
import cairosvg
import fitz
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from PIL import Image, ImageOps

from buildingcv.extract_polygons import PolygonExtractor
from buildingcv.svg_render import filtered_svg_bytes
import mimetypes
import tectly
from server.polyhaven import router as polyhaven_router, MATERIAL_CACHE_DIR
from server.furniture import router as furniture_router, CACHE as FURNITURE_CACHE
from server.openings import router as openings_router, CACHE as OPENINGS_CACHE
from .billing import router as billing_router
load_dotenv(
    dotenv_path=Path(__file__).resolve().parent.parent / ".env",
    override=True,
)
DEFAULT_RUN_DIR = "weights"
DEFAULT_CKPT = "best.safetensors"
DEFAULT_DEVICE = "auto"

MAX_UPLOAD_BYTES = 16 * 1024 * 1024
MAX_IMAGE_PIXELS = 25_000_000
ALLOWED_EXTENSIONS = {".svg", ".pdf", ".png", ".jpg", ".jpeg", ".webp"}

REPO_ROOT = Path(__file__).resolve().parent.parent
VIEWER_HTML = REPO_ROOT / "viewer" / "index.html"

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
    print(f"[server] loaded {run_dir / ckpt} on {extractor.device} (epoch {extractor.epoch})")
    app.state.extractor = extractor
    yield


app = FastAPI(title="BuildingCV inference", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Poly Haven material catalogue and locally cached PBR textures.
app.include_router(polyhaven_router)
app.include_router(furniture_router)
app.include_router(openings_router)
app.include_router(billing_router)
MATERIAL_CACHE_DIR.mkdir(parents=True, exist_ok=True)
app.mount(
    "/material-cache",
    StaticFiles(directory=MATERIAL_CACHE_DIR),
    name="material-cache",
)
FURNITURE_CACHE.mkdir(parents=True, exist_ok=True)
app.mount("/furniture-cache", StaticFiles(directory=FURNITURE_CACHE), name="furniture-cache")

# Locally cached Poly Haven door/window models.
OPENINGS_CACHE.mkdir(parents=True, exist_ok=True)
app.mount("/openings-cache", StaticFiles(directory=OPENINGS_CACHE), name="openings-cache")


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


@app.get("/materials.js")
def materials_script() -> FileResponse:
    return FileResponse(REPO_ROOT / "viewer" / "materials.js", media_type="text/javascript")
@app.get("/furniture.js")
def furniture_script() -> FileResponse:
    return FileResponse(
        REPO_ROOT / "viewer" / "furniture.js",
        media_type="text/javascript"
    )


@app.get("/openings.js")
def openings_script() -> FileResponse:
    return FileResponse(
        REPO_ROOT / "viewer" / "openings.js",
        media_type="text/javascript",
    )

@app.get("/")
def index() -> FileResponse:
    """Serve the viewer HTML."""
    return FileResponse(VIEWER_HTML)


DEMOS_DIR = REPO_ROOT / "viewer" / "demos"
if DEMOS_DIR.is_dir():
    app.mount("/demos", StaticFiles(directory=DEMOS_DIR), name="demos")


def _attach_input_image(svg_path: Path, result: dict) -> dict:
    """Attach a transparent PNG preview of the structural SVG to the result."""
    _, _, inner_w, inner_h = result["content_rect"]
    png = cairosvg.svg2png(
        bytestring=filtered_svg_bytes(svg_path),
        output_width=inner_w,
        output_height=inner_h,
    )
    result["input_image_b64"] = base64.b64encode(png).decode("ascii")
    return result


def _decode_raster(filename: str, raw: bytes) -> Image.Image:
    """Decode a PDF's first page or a supported raster file into RGB."""
    suffix = Path(filename).suffix.lower()

    try:
        if suffix == ".pdf":
            with fitz.open(stream=raw, filetype="pdf") as document:
                if document.page_count < 1:
                    raise HTTPException(status_code=400, detail="PDF has no pages")
                # Render page 1 at 2x for legible linework.
                pix = document[0].get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
                image = Image.open(io.BytesIO(pix.tobytes("png")))
                image.load()
        else:
            image = Image.open(io.BytesIO(raw))
            image = ImageOps.exif_transpose(image)
            image.load()

        if image.width * image.height > MAX_IMAGE_PIXELS:
            raise HTTPException(
                status_code=413,
                detail="Image exceeds the 25-megapixel limit",
            )

        return image.convert("RGB")

    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=400,
            detail="Could not read the uploaded file. Check that it is a valid PDF, PNG, JPG, or WebP.",
        ) from exc


def _attach_raster_preview(image: Image.Image, result: dict) -> dict:
    """Attach a PNG preview sized to the model's non-letterboxed content area."""
    _, _, inner_w, inner_h = result["content_rect"]
    preview = image.convert("RGB").resize((inner_w, inner_h), Image.Resampling.LANCZOS)
    buffer = io.BytesIO()
    preview.save(buffer, format="PNG")
    result["input_image_b64"] = base64.b64encode(buffer.getvalue()).decode("ascii")
    return result


@app.get("/sample/{key}")
def sample(key: str) -> dict:
    """Run extraction on a server-known dataset SVG."""
    if key not in SAMPLES:
        raise HTTPException(status_code=404, detail=f"unknown sample: {key}")

    data_dir = Path(
        os.environ.get("BUILDINGCV_DATA_DIR", REPO_ROOT / "data" / "cubicasa5k")
    )
    svg_path = data_dir / SAMPLES[key] / "model.svg"
    if not svg_path.exists():
        raise HTTPException(status_code=404, detail=f"sample SVG not found: {svg_path}")

    extractor: PolygonExtractor = app.state.extractor
    return _attach_input_image(svg_path, extractor.extract(svg_path))


@app.post("/extract")
async def extract(svg: UploadFile = File(...)) -> dict:
    """Extract geometry from SVG, PDF, PNG, JPG/JPEG, or WebP.

    The multipart field remains named 'svg' for compatibility with the existing viewer.
    Raster uploads use PolygonExtractor.extract_image(), not the SVG parser.
    """
    raw = await svg.read()
    if not raw:
        raise HTTPException(status_code=400, detail="Empty upload")
    if len(raw) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"Upload exceeds {MAX_UPLOAD_BYTES} bytes",
        )

    filename = svg.filename or ""
    suffix = Path(filename).suffix.lower()
    if suffix not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=415,
            detail="Supported formats: SVG, PDF, PNG, JPG, JPEG, WEBP",
        )

    extractor: PolygonExtractor = app.state.extractor

    # Keep native SVG processing unchanged.
    if suffix == ".svg":
        try:
            ET.fromstring(raw)
        except ET.ParseError as exc:
            raise HTTPException(status_code=400, detail="Invalid SVG file") from exc

        with tempfile.NamedTemporaryFile(suffix=".svg", delete=False) as tmp:
            tmp.write(raw)
            tmp.flush()
            tmp_path = Path(tmp.name)

        try:
            try:
                result = extractor.extract(tmp_path)
            except Exception as exc:
                raise HTTPException(
                    status_code=422,
                    detail=f"Failed to extract SVG: {exc}",
                ) from exc
            return _attach_input_image(tmp_path, result)
        finally:
            tmp_path.unlink(missing_ok=True)

    # Raster formats are decoded and passed directly to the model.
    image = _decode_raster(filename, raw)
    try:
        result = extractor.extract_image(image)
    except Exception as exc:
        raise HTTPException(
            status_code=422,
            detail=f"Failed to extract raster image: {exc}",
        ) from exc

    return _attach_raster_preview(image, result)
def _tectly_xy(point, image_width: int, image_height: int) -> list[float]:
    """Convert Tectly normalized coordinates to viewer image pixels."""
    point = getattr(point, "root", point)
    x, y = float(point[0]), float(point[1])

    # Tectly coordinates are normalized to the plan image.
    if abs(x) <= 1.5 and abs(y) <= 1.5:
        x *= image_width
        y *= image_height

    return [x, y]


def _tectly_boundary_polygon(boundary, image_width: int, image_height: int):
    points = [
        _tectly_xy(point, image_width, image_height)
        for point in boundary
    ]
    if len(points) < 3:
        return None

    return {"outer": points, "holes": []}


def _tectly_segment_polygon(start, end, image_width: int, image_height: int):
    """Create a thin visible strip for a door/window opening segment."""
    a = _tectly_xy(start, image_width, image_height)
    b = _tectly_xy(end, image_width, image_height)

    dx, dy = b[0] - a[0], b[1] - a[1]
    length = (dx * dx + dy * dy) ** 0.5
    if length < 1:
        return None

    thickness = max(4.0, min(image_width, image_height) * 0.006)
    nx = -dy / length * thickness / 2
    ny = dx / length * thickness / 2

    return {
        "outer": [
            [a[0] + nx, a[1] + ny],
            [b[0] + nx, b[1] + ny],
            [b[0] - nx, b[1] - ny],
            [a[0] - nx, a[1] - ny],
        ],
        "holes": [],
    }


@app.post("/tectly/extract")
def tectly_extract(file: UploadFile = File(...)) -> dict:
    """Extract floor-plan geometry through Tectly without changing /extract."""
    raw = file.file.read()
    filename = file.filename or "floorplan.jpg"
    suffix = Path(filename).suffix.lower()

    if not raw:
        raise HTTPException(status_code=400, detail="Empty upload")

    if len(raw) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"Upload exceeds {MAX_UPLOAD_BYTES} bytes",
        )

    if suffix not in {".pdf", ".png", ".jpg", ".jpeg", ".webp"}:
        raise HTTPException(
            status_code=415,
            detail="Tectly upload supports PDF, PNG, JPG, JPEG, and WEBP",
        )

    api_key = os.environ.get("TECTLY_API_KEY")
    api_secret = os.environ.get("TECTLY_API_SECRET")
    if not api_key or not api_secret:
        raise HTTPException(
            status_code=503,
            detail="Tectly credentials are not configured on the server",
        )

    # Decode a local preview before making any API requests.
    image = _decode_raster(filename, raw)
    width, height = image.size

    try:
        client = tectly.Client()

        with client.create_session(
            api_key=api_key,
            api_secret=api_secret,
        ) as session:
            project_id = os.environ.get("TECTLY_PROJECT_ID")
            if not project_id:
                raise HTTPException(
                    status_code=500,
                    detail="Set TECTLY_PROJECT_ID to an existing Tectly project",
                )

            document = session.add_document(
                id=project_id,
                file=tectly.UploadFile(
                    file_name=Path(filename).name,
                    mime_type=mimetypes.guess_type(filename)[0]
                    or "application/octet-stream",
                    payload=raw,
                ),
            )

            # Wait for Tectly's asynchronous recognition to finish.
            list(session.watch(document.id, step_timeout=300.0))

            floors = session.fetch_floors(project_id)
            if not floors:
                raise HTTPException(
                    status_code=422,
                    detail="Tectly finished processing but returned no floors",
                )

            floor = floors[0]
            walls = session.fetch_walls(floor.id)
            openings = session.fetch_wall_openings(floor.id)

            wall_polygons = []
            for wall in walls:
                polygon = _tectly_boundary_polygon(
                    wall.boundary, width, height
                )
                if polygon:
                    wall_polygons.append(polygon)

            door_polygons = []
            window_polygons = []

            for opening in openings:
                details = opening.details
                kind = getattr(details, "type", "")

                if kind == "door":
                    start, end = details.hinge, details.closed
                    target = door_polygons
                elif kind == "sliding-door":
                    start, end = details.closed, details.open
                    target = door_polygons
                elif kind == "window":
                    start, end = details.from_, details.to
                    target = window_polygons
                else:
                    continue

                polygon = _tectly_segment_polygon(
                    start, end, width, height
                )
                if polygon:
                    target.append(polygon)

            preview_buffer = io.BytesIO()
            image.save(preview_buffer, format="PNG")

            return {
                "canvas_size": [width, height],
                "content_rect": [0, 0, width, height],
                "polygons": {
                    "wall": wall_polygons,
                    "door": door_polygons,
                    "window": window_polygons,
                },
                "input_image_b64": base64.b64encode(
                    preview_buffer.getvalue()
                ).decode("ascii"),
                "tectly": {
                    "floor_id": str(floor.id),
                    "walls": len(wall_polygons),
                    "doors": len(door_polygons),
                    "windows": len(window_polygons),
                },
            }

    except HTTPException:
        raise
    except Exception as exc:
        # Keep internal credentials and request details out of the response.
        print(f"[tectly] extraction failed: {type(exc).__name__}: {exc}")
        raise HTTPException(
            status_code=502,
            detail=(
                f"Tectly extraction failed ({type(exc).__name__}). "
                "Check the server terminal for details."
            ),
        ) from exc
