from fastapi import APIRouter
import json


router = APIRouter(
    prefix="/monitor_schema",
    tags=["monitor_schema"],
    responses={404: {"description": "Not found"}},
)


@router.get("/")
def monitor_schema_endpoint():
    data = None

    with open("/code/specifications/monitoring_schema.json") as json_file:
        data = json.load(json_file)
        print(data)

    return data
