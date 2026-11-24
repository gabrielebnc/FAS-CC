from fastapi import APIRouter
import json


router = APIRouter(
    prefix="/execute_schema",
    tags=["execute_schema"],
    responses={404: {"description": "Not found"}},
)


@router.get("/")
def execute_schema_endpoint():
    data = None

    with open("/code/specifications/execute_schema.json") as json_file:
        data = json.load(json_file)
        print(data)

    return data
