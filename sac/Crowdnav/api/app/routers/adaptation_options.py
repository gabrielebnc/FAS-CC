from fastapi import APIRouter
import json


router = APIRouter(
    prefix="/adaptation_options",
    tags=["adaptation_options"],
    responses={404: {"description": "Not found"}},
)


@router.get("/")
def adaptation_options_endpoint():
    return {
            "route_random_sigma":           0, 
            "exploration_percentage":       0, 
            "max_speed_and_length_factor":  0,
            "average_edge_duration_factor": 0, 
            "freshness_update_factor":      0, 
            "freshness_cut_off_value":      0,
            "re_route_every_ticks":         0, 
            "total_car_counter":            0, 
            "edge_average_influence":       0
            }
