
import json
from pathlib import Path

import cv2
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.patches import Polygon as MplPolygon

ROOT = Path(__file__).resolve().parent
IMAGE_PATH = ROOT / "tech drawings" / "ref2.jpg"
JSON_PATH = ROOT / "tectly_result.json"

# Load reference image and Tectly output
image = cv2.imread(str(IMAGE_PATH))
if image is None:
    raise FileNotFoundError(f"Could not read: {IMAGE_PATH}")

image = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
height, width = image.shape[:2]

with open(JSON_PATH, encoding="utf-8") as f:
    data = json.load(f)

floor = data[0]
walls = floor.get("walls", [])
openings = floor.get("openings", [])

fig, ax = plt.subplots(figsize=(12, 12))
ax.imshow(image)

# Overlay detected wall polygons
for wall in walls:
    boundary = wall.get("boundary")
    if not boundary:
        continue

    points = np.array([
        [x * width, y * height] for x, y in boundary
    ])

    patch = MplPolygon(
        points,
        closed=True,
        fill=False,
        edgecolor="lime",
        linewidth=2
    )
    ax.add_patch(patch)

# Overlay opening centerlines
for opening in openings:
    details = opening.get("details", {})
    start = details.get("from_")
    end = details.get("to")

    if start is None or end is None:
        continue

    x1, y1 = start[0] * width, start[1] * height
    x2, y2 = end[0] * width, end[1] * height

    ax.plot([x1, x2], [y1, y2], color="red", linewidth=3)

ax.set_title("Tectly wall overlay: green = walls, red = openings")
ax.axis("off")
plt.tight_layout()

output = ROOT / "tectly_overlay.png"
plt.savefig(output, dpi=180, bbox_inches="tight")
print(f"Saved overlay: {output}")
print(f"Image dimensions: {width} x {height}")
print(f"Walls: {len(walls)} | Openings: {len(openings)}")

plt.show()
