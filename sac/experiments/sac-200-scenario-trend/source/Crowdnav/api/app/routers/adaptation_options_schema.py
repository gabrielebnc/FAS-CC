from fastapi import APIRouter
import json


router = APIRouter(
    prefix="/adaptation_options_schema",
    tags=["adaptation_options_schema"],
    responses={404: {"description": "Not found"}},
)


@router.get("/")
def adaptation_options_schema_endpoint():
    data = None

    with open("/code/specifications/adaptation_options_schema.json") as json_file:
        data = json.load(json_file)
        print(data)

    return data
