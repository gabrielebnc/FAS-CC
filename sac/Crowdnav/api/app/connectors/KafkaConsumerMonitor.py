from aiokafka import AIOKafkaConsumer
import asyncio
import logging
import json

data_store = {"latest_message": None}

KAFKA_INSTANCE = "kafka:9092"
KAFKA_TOPIC = 'crowd-nav-monitored-stats'

async def connect():
    while True:
        consumer = AIOKafkaConsumer(
            KAFKA_TOPIC,
            bootstrap_servers=KAFKA_INSTANCE,
            value_deserializer=lambda v: json.loads(v.decode('utf-8')),
            auto_offset_reset='earliest',
            group_id=None)
        try:
            # Kafka may still be starting when the HTTP server becomes ready.
            await consumer.start()
            print('# KafkaConsumerMonitor Connected!', flush=True)
            async for message in consumer:
                data_store["latest_message"] = message.value
        except asyncio.CancelledError:
            raise
        except Exception:
            logging.exception('Monitoring consumer disconnected; retrying in 5 seconds')
        finally:
            await consumer.stop()
        await asyncio.sleep(5)
