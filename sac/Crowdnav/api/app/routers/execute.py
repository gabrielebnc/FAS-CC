import asyncio
import logging

from aiokafka import AIOKafkaProducer
from aiokafka.errors import KafkaTimeoutError, KafkaError, KafkaConnectionError

from fastapi import APIRouter
from pydantic import BaseModel
import json


router = APIRouter(
    prefix="/execute",
    tags=["execute"],
    responses={404: {"description": "Not found"}},)
class AdaptationOptions(BaseModel):
    route_random_sigma: float | None = None
    exploration_percentage: float | None = None
    max_speed_and_length_factor: float | None = None
    average_edge_duration_factor: float | None = None
    freshness_update_factor: float | None = None
    freshness_cut_off_value: float | None = None
    re_route_every_ticks: int | None = None
    total_car_counter: int | None = None
    edge_average_influence: float | None = None


loop = asyncio.get_event_loop()

KAFKA_INSTANCE = "kafka:9092"
# Detect the broker version instead of forcing a potentially incompatible format.
kafkaproducer = AIOKafkaProducer(loop=loop, bootstrap_servers=KAFKA_INSTANCE)


@router.put("/")
async def execute(options: AdaptationOptions):

    message = ""
    status = "error"
    try:
        await kafkaproducer.start()
        await kafkaproducer.send_and_wait("crowd-nav-commands", json.dumps(options.dict(exclude_none=True)).encode("ascii"))
        status = "success"
        message = "The message was successfully sent to the kafka broker, the message is: " + str(options)
    except KafkaConnectionError:
        logging.exception("Failed to connect to Kafka")
        message = "Something went wrong with the connection to Kafka"
    except KafkaTimeoutError:
        logging.exception("Timed out sending the command to Kafka")
        message = "While sending the message to the kafka broker a timeout occurred"
    except KafkaError:
        logging.exception("Failed to send the command to Kafka")
        message = "While sending the message to the kafka broker a KafkaError occurred"

    return {"status": status, "message": message}
