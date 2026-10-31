from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from typing import List, Optional

router = APIRouter(prefix="/billing", tags=["Construction Billing"])


# -----------------------------
# 1. INPUT MODELS
# -----------------------------

class Opening(BaseModel):
    type: str = "door"

    width: float = Field(gt=0)
    height: float = Field(gt=0)

    count: int = Field(default=1, ge=1)


class WallMeasurement(BaseModel):
    wall_id: str

    length: float = Field(gt=0)
    height: float = Field(gt=0)
    thickness: float = Field(gt=0)

    # Number of wall faces to plaster/paint
    faces_to_finish: int = Field(default=2, ge=1, le=2)

    openings: List[Opening] = Field(default_factory=list)


class ConstructionRates(BaseModel):
    # INR
    masonry_per_m3: float = Field(default=0, ge=0)
    wall_per_running_m: float = Field(default=0, ge=0)
    plaster_per_m2: float = Field(default=0, ge=0)
    painting_per_m2: float = Field(default=0, ge=0)

    wastage_percent: float = Field(default=0, ge=0, le=100)
    labour_percent: float = Field(default=0, ge=0, le=100)
    overhead_percent: float = Field(default=0, ge=0, le=100)
    tax_percent: float = Field(default=0, ge=0, le=100)


class BillingRequest(BaseModel):
    project_name: str = "ArchScale Project"

    walls: List[WallMeasurement]

    rates: ConstructionRates


# -----------------------------
# 2. HELPER FUNCTIONS
# -----------------------------

def money(value: float) -> float:
    return round(value, 2)


# -----------------------------
# 3. BILLING CALCULATOR
# -----------------------------

@router.post("/estimate")
def generate_estimate(request: BillingRequest):

    if not request.walls:
        raise HTTPException(
            status_code=400,
            detail="No wall measurements provided."
        )

    total_length = 0.0
    gross_area = 0.0
    opening_area = 0.0
    net_area = 0.0
    gross_volume = 0.0
    opening_volume = 0.0
    net_volume = 0.0

    wall_details = []

    for wall in request.walls:

        length = wall.length
        height = wall.height
        thickness = wall.thickness

        wall_length = length
        wall_gross_area = length * height
        wall_gross_volume = wall_gross_area * thickness

        # Calculate door/window openings
        wall_opening_area = 0.0

        opening_details = []

        for opening in wall.openings:

            area = (
                opening.width
                * opening.height
                * opening.count
            )

            wall_opening_area += area

            opening_details.append({
                "type": opening.type,
                "count": opening.count,
                "area_m2": money(area)
            })

        if wall_opening_area > wall_gross_area:
            raise HTTPException(
                status_code=400,
                detail=f"Opening area exceeds wall area: {wall.wall_id}"
            )

        # Net wall area on one face
        wall_net_area = max(
            0,
            wall_gross_area - wall_opening_area
        )

        # Openings are deducted once from masonry volume
        wall_opening_volume = wall_opening_area * thickness

        wall_net_volume = max(
            0,
            wall_gross_volume - wall_opening_volume
        )

        # Plastering and painting account for wall faces
        finished_area = (
            wall_net_area * wall.faces_to_finish
        )

        total_length += wall_length
        gross_area += wall_gross_area
        opening_area += wall_opening_area
        net_area += finished_area
        gross_volume += wall_gross_volume
        opening_volume += wall_opening_volume
        net_volume += wall_net_volume

        wall_details.append({
            "wall_id": wall.wall_id,
            "length_m": money(length),
            "height_m": money(height),
            "thickness_m": money(thickness),
            "gross_area_m2": money(wall_gross_area),
            "opening_area_m2": money(wall_opening_area),
            "net_area_one_face_m2": money(wall_net_area),
            "finished_area_m2": money(finished_area),
            "net_volume_m3": money(wall_net_volume),
            "openings": opening_details
        })

    rates = request.rates

    # -----------------------------
    # 4. COST CALCULATIONS
    # -----------------------------

    masonry_cost = net_volume * rates.masonry_per_m3

    running_wall_cost = (
        total_length * rates.wall_per_running_m
    )

    plaster_cost = (
        net_area * rates.plaster_per_m2
    )

    painting_cost = (
        net_area * rates.painting_per_m2
    )

    base_cost = (
        masonry_cost
        + running_wall_cost
        + plaster_cost
        + painting_cost
    )

    wastage_cost = (
        base_cost * rates.wastage_percent / 100
    )

    labour_cost = (
        base_cost * rates.labour_percent / 100
    )

    overhead_cost = (
        base_cost * rates.overhead_percent / 100
    )

    subtotal = (
        base_cost
        + wastage_cost
        + labour_cost
        + overhead_cost
    )

    tax_amount = (
        subtotal * rates.tax_percent / 100
    )

    grand_total = subtotal + tax_amount

    # -----------------------------
    # 5. RESPONSE
    # -----------------------------

    return {
        "project_name": request.project_name,

        "measurements": {
            "wall_count": len(request.walls),
            "total_wall_length_m": money(total_length),
            "gross_wall_area_m2": money(gross_area),
            "opening_area_m2": money(opening_area),
            "net_finished_area_m2": money(net_area),
            "gross_wall_volume_m3": money(gross_volume),
            "opening_volume_m3": money(opening_volume),
            "net_masonry_volume_m3": money(net_volume)
        },

        "wall_details": wall_details,

        "cost_breakdown": {
            "masonry": money(masonry_cost),
            "running_wall_cost": money(running_wall_cost),
            "plastering": money(plaster_cost),
            "painting": money(painting_cost),
            "wastage": money(wastage_cost),
            "labour": money(labour_cost),
            "overhead": money(overhead_cost),
            "subtotal": money(subtotal),
            "tax": money(tax_amount),
            "tax_percent": rates.tax_percent,
            "grand_total": money(grand_total),
            "currency": "INR"
        }
    }