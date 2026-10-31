
import os
import json
from pathlib import Path
from dotenv import load_dotenv
from tectly import Client, ProjectCreate, UploadFile
from tectly import PlanCompletedEvent, PlanFailedEvent

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env", override=True)

api_key = os.getenv("TECTLY_API_KEY")
api_secret = os.getenv("TECTLY_API_SECRET")
image_path = ROOT / "tech drawings"/"ref2.jpg"

if not api_key or not api_secret:
    raise RuntimeError("Tectly credentials are missing from .env")

if not image_path.exists():
    raise FileNotFoundError(
        f"Place ref2.jpg in the project root: {ROOT}"
    )

with Client().create_session(api_key, api_secret) as session:
    project = session.create_project(
        ProjectCreate(title="Archscale Floorplan Test")
    )
    print("Project created:", project.id)

    with image_path.open("rb") as image_file:
        document = session.add_document(
            project.id,
            UploadFile(
                file_name=image_path.name,
                mime_type="image/jpeg",
                payload=image_file,
            ),
        )

    print("Uploaded document:", document.id)
    results = []

    for event in session.watch(document.id):
        print("Processing event:", type(event).__name__)

        if isinstance(event, PlanCompletedEvent):
            floor_id = event.floor_id

            floor_data = {
                "floor_id": floor_id,
                "rooms": [
                    item.model_dump(mode="json")
                    for item in session.fetch_rooms(floor_id)
                ],
                "walls": [
                    item.model_dump(mode="json")
                    for item in session.fetch_walls(floor_id)
                ],
                "openings": [
                    item.model_dump(mode="json")
                    for item in session.fetch_wall_openings(floor_id)
                ],
            }
            results.append(floor_data)

        elif isinstance(event, PlanFailedEvent):
            print("A plan failed:", event.plan_id)

    output_path = ROOT / "tectly_result.json"
    output_path.write_text(
        json.dumps(results, indent=2,default=str),
        encoding="utf-8",
    )

    print("Saved results to:", output_path)
    print("Floors extracted:", len(results))
    print("Result file:", output_path)
