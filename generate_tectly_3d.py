
import json
from pathlib import Path
from statistics import median

import trimesh
from shapely.geometry import LineString, Polygon
from shapely.ops import unary_union

ROOT = Path(__file__).resolve().parent
DATA_FILE = ROOT / "tectly_result.json"
OUTPUT_FILE = ROOT / "tectly_walls.obj"

WALL_HEIGHT = 2.8
TARGET_WIDTH = 10.0  # Provisional; calibrate from the technical drawing.

data = json.loads(DATA_FILE.read_text(encoding="utf-8"))
floor = data[0] if isinstance(data, list) else data
walls = floor.get("walls", [])
openings = floor.get("openings", [])

if not walls:
    raise RuntimeError("No wall polygons found in tectly_result.json")

# Establish the coordinate bounds from all wall polygons.
all_points = [
    point
    for wall in walls
    for point in (wall.get("boundary") or [])
    if len(point) >= 2
]

if not all_points:
    raise RuntimeError("Wall boundaries are missing")

min_x = min(p[0] for p in all_points)
max_x = max(p[0] for p in all_points)
min_y = min(p[1] for p in all_points)
max_y = max(p[1] for p in all_points)

if max_x <= min_x or max_y <= min_y:
    raise RuntimeError("Invalid wall coordinate bounds")

scale = TARGET_WIDTH / (max_x - min_x)

def transform_point(point):
    """Convert normalized plan coordinates to model coordinates."""
    x, y = point[:2]
    return ((x - min_x) * scale, (max_y - y) * scale)

def make_polygon(boundary):
    points = [transform_point(p) for p in boundary]
    polygon = Polygon(points)

    if not polygon.is_valid:
        polygon = polygon.buffer(0)

    return polygon

wall_polygons = []
thickness_candidates = []

for index, wall in enumerate(walls):
    boundary = wall.get("boundary") or []

    if len(boundary) < 3:
        print(f"Skipping invalid wall boundary {index + 1}")
        continue

    polygon = make_polygon(boundary)

    if polygon.is_empty:
        print(f"Skipping empty wall {index + 1}")
        continue

    if polygon.geom_type == "Polygon":
        parts = [polygon]
    elif polygon.geom_type == "MultiPolygon":
        parts = list(polygon.geoms)
    else:
        print(f"Skipping unsupported geometry for wall {index + 1}")
        continue

    for part in parts:
        if part.area <= 1e-8:
            continue

        wall_polygons.append((index + 1, part))

        # A rough estimate only: the smaller bounding-box dimension
        # often approximates thickness for narrow wall segments.
        width, height = part.bounds[2] - part.bounds[0], (
            part.bounds[3] - part.bounds[1]
        )
        smaller = min(width, height)
        if smaller > 0:
            thickness_candidates.append(smaller)

if not wall_polygons:
    raise RuntimeError("No valid wall polygons remain")

# Estimate thickness in model units. This is only a heuristic:
# long connected L-shaped polygons can distort the estimate.
estimated_thickness = median(thickness_candidates)
print(f"Estimated wall thickness: {estimated_thickness:.3f} m (heuristic)")

# Collect door spans using hinge -> closed, NOT hinge -> open.
door_lines = []
skipped_doors = 0

for opening in openings:
    details = opening.get("details") or {}

    if details.get("type", "").lower() != "door":
        continue

    hinge = details.get("hinge")
    closed = details.get("closed")

    if not hinge or not closed:
        skipped_doors += 1
        continue

    line = LineString([
        transform_point(hinge),
        transform_point(closed),
    ])

    if line.length <= 1e-5:
        skipped_doors += 1
        continue

    door_lines.append(line)

# Cut a narrow rectangular strip across the wall thickness at each door.
# The buffer width is deliberately generous to cross the wall polygon.
# Its length still comes from the detected hinge-to-closed segment.
door_cutters = [
    line.buffer(
        max(estimated_thickness * 1.5, 0.04),
        cap_style="flat",
        join_style="mitre",
    )
    for line in door_lines
]

cutters = unary_union(door_cutters) if door_cutters else None

scene = trimesh.Scene()
created = 0
cut_wall_count = 0

for wall_number, original_polygon in wall_polygons:
    polygon = original_polygon

    if cutters is not None and polygon.intersects(cutters):
        candidate = polygon.difference(cutters)

        if not candidate.is_empty:
            polygon = candidate
            cut_wall_count += 1

    if polygon.is_empty:
        continue

    if polygon.geom_type == "Polygon":
        parts = [polygon]
    elif polygon.geom_type == "MultiPolygon":
        parts = list(polygon.geoms)
    else:
        print(f"Skipping unsupported result for wall {wall_number}")
        continue

    for part_index, part in enumerate(parts, start=1):
        if part.area <= 1e-8:
            continue

        try:
            mesh = trimesh.creation.extrude_polygon(
                part,
                height=WALL_HEIGHT,
            )
            scene.add_geometry(
                mesh,
                node_name=f"Wall_{wall_number}_Part_{part_index}",
            )
            created += 1
        except Exception as error:
            print(
                f"Could not extrude wall {wall_number}, "
                f"part {part_index}: {error}"
            )

if created == 0:
    raise RuntimeError("No wall meshes were generated")

scene.export(str(OUTPUT_FILE))

print(f"\n3D model saved: {OUTPUT_FILE}")
print(f"Wall mesh parts generated: {created}")
print(f"Wall polygons intersecting door cutters: {cut_wall_count}")
print(f"Door spans used: {len(door_lines)}")
print(f"Doors skipped: {skipped_doors}")
print(f"Windows retained for next stage: "
      f"{sum(1 for o in openings if (o.get('details') or {}).get('type', '').lower() == 'window')}")
print(f"Wall height: {WALL_HEIGHT} m (provisional)")
print(f"Model width: {TARGET_WIDTH} m (provisional)")
