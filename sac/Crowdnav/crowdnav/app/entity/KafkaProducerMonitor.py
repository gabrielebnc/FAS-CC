from kafka import KafkaProducer
import app.Config as Config
from colorama import Fore
import json

producer = None

def connect():
    """Try to connect to Kafka, else exits the process."""
    if Config.kafkaUpdates:
        try:
            global producer
            producer = KafkaProducer(
                bootstrap_servers=Config.kafkaHost,
                api_version=(0, 10, 1),
                value_serializer=lambda v: json.dumps(v).encode('utf-8'),
                request_timeout_ms=5000)
            
            print(Fore.GREEN + '# KafkaProducerMonitor Connected!' + Fore.RESET)
        except Exception as e:
            print(Fore.RED + "Connection to Kafka failed! Error:", e, Fore.RESET)  

def publish(topic, message):
    if Config.kafkaUpdates:
        try:
            producer.send(topic, message)
        except:
            print("Error sending kafka status")
    else:
        # we ignore this in json mode
        pass