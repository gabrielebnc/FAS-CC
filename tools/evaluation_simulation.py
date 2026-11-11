"""Local assessment instrumentation, run inside the CrowdNav image by evaluate.py.

Keep normal simulation, routing, HTTP and Kafka behavior. Only seed randomness,
record the existing trip events, and stop after the final tick has completed.
"""

import json
import os
from pathlib import Path
import random
import sys
import time
from urllib.error import URLError
from urllib.request import urlopen


BASELINE = {
    "route_random_sigma": 0.2,
    "exploration_percentage": 0.5,
    "max_speed_and_length_factor": 1,
    "average_edge_duration_factor": 1,
    "freshness_update_factor": 10,
    "freshness_cut_off_value": 90,
    "re_route_every_ticks": 600,
    "total_car_counter": 750,
    "edge_average_influence": 140,
}
HORIZON = 10_000


class Finished(Exception):
    pass


def main():
    seed = int(os.environ["EVALUATION_SEED"])
    random.seed(seed)
    sys.path.insert(0, "/app")
    # Boot adds SUMO's Python modules to the import path.
    from app import Boot, Config
    from app.entity import KafkaProducerMonitor
    from app.entity.CarRegistry import CarRegistry
    from app.routing.CustomRouter import CustomRouter
    from app.routing.RoutingEdge import RoutingEdge
    from app.simulation.Simulation import Simulation
    from app.streaming import RTXForward
    from kafka import KafkaProducer
    import traci

    # Precreate topics before the simulator's command consumer subscribes.
    producer = KafkaProducer(bootstrap_servers=Config.kafkaHost, api_version=(0, 10, 1))
    try:
        for topic in (Config.kafkaCommandsTopic, Config.kafkaTopicMonitoring,
                      Config.kafkaTopicTrips, Config.kafkaTopicPerformance, Config.kafkaTopicRouting):
            # Producer metadata requests trigger topic auto-creation on this
            # broker; KafkaConsumer.partitions_for_topic() does not.
            if not producer.partitions_for(topic):
                raise RuntimeError(f"Kafka topic not ready: {topic}")
    finally:
        producer.close()

    # The API must be running before simulation time starts. Its consumer uses
    # a fresh broker, so no snapshots or commands from other runs can leak in.
    deadline = time.monotonic() + 120
    while True:
        try:
            with urlopen("http://http-server:8080/", timeout=3) as response:
                if response.status == 200:
                    break
        except (URLError, TimeoutError):
            pass
        if time.monotonic() >= deadline:
            raise TimeoutError("Evaluation API did not start")
        time.sleep(1)
    time.sleep(3)

    original_start = traci.start

    def seeded_start(command, *args, **kwargs):
        return original_start([*command, "--seed", str(seed)], *args, **kwargs)

    traci.start = seeded_start
    CustomRouter.routeRandomSigma = BASELINE["route_random_sigma"]
    RoutingEdge.edgeAverageInfluence = BASELINE["edge_average_influence"]
    Config.totalCarCounter = CarRegistry.totalCarCounter = 750

    output = Path("/results")
    trip_count = 0
    configs = []
    last_config = None
    original_publish = RTXForward.publish
    original_monitor = Simulation.sendMonitorInfo
    original_monitor_publish = KafkaProducerMonitor.publish

    with (output / "trips.jsonl").open("w", buffering=1) as trips:
        def record_trip(message, topic):
            nonlocal trip_count
            if topic == Config.kafkaTopicTrips:
                if not (0 < message["tick"] <= HORIZON):
                    raise RuntimeError("Trip outside evaluation horizon")
                trips.write(json.dumps(message, allow_nan=False) + "\n")
                trip_count += 1
            return original_publish(message, topic)

        def record_monitor(topic, message):
            nonlocal last_config
            config = message["configs"]
            if config["total_car_counter"] != 750:
                raise RuntimeError("Strategy changed the workload")
            if last_config is None and config != BASELINE:
                raise RuntimeError(f"Incorrect initial baseline: {config}")
            if config != last_config:
                configs.append({"step": message["car_stats"]["step"], "configs": config.copy()})
                last_config = config.copy()
            return original_monitor_publish(topic, message)

        def bounded_monitor(cls, duration, routing_duration):
            original_monitor(duration, routing_duration)
            if cls.tick % 100 == 0:
                progress = output / "progress.tmp"
                progress.write_text(json.dumps({"step": cls.tick}))
                progress.replace(output / "progress.json")
            if cls.tick == HORIZON:
                raise Finished()

        RTXForward.publish = record_trip
        KafkaProducerMonitor.publish = record_monitor
        Simulation.sendMonitorInfo = classmethod(bounded_monitor)
        try:
            Boot.start(0, False, False)
        except Finished:
            if trip_count != CarRegistry.totalTrips:
                raise RuntimeError("Recorded trip count differs from simulation counter")
        else:
            raise RuntimeError("Simulation ended before the evaluation horizon")
        finally:
            for producer in (RTXForward.producer, KafkaProducerMonitor.producer):
                if producer is not None:
                    producer.flush(timeout=10)
            if traci.isLoaded():
                traci.close()

    result = {"seed": seed, "final_step": Simulation.tick,
              "trip_count": trip_count, "configs": configs,
              "warmup_ticks": Config.initialWaitTicks,
              "map": Config.sumoConfig, "smart_car_percentage": Config.smartCarPercentage}
    temporary = output / "result.tmp"
    temporary.write_text(json.dumps(result, indent=2))
    temporary.replace(output / "result.json")


if __name__ == "__main__":
    main()
