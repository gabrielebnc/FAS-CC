from fastapi import APIRouter
from ..connectors import KafkaConsumerMonitor

router = APIRouter(
    prefix="/monitor",
    tags=["monitor"],
    responses={404: {"description": "Not found"}},
)

@router.get("/")
def monitor_endpoint():
    return KafkaConsumerMonitor.data_store["latest_message"]