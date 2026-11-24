"""Training-only wrapper: seed upstream CrowdNav, record trips, stop at a tick.

Mounted into the unchanged simulator image. No routing or metric formulas are
replaced. The controller still receives exactly the deployment HTTP features.
"""

import json
import os
from pathlib import Path
import random
import sys
import time


class Finished(Exception):
    pass


def main():
    output = Path("/results")
    seed, horizon = int(os.environ["SIMULATION_SEED"]), int(os.environ["SIMULATION_TICKS"])
    initial = json.loads((output / "initial.json").read_text())
    random.seed(seed)
    sys.path.insert(0, "/app")
    from app import Boot, Config
    from app.entity import KafkaProducerMonitor
    from app.entity.Car import Car
    from app.entity.CarRegistry import CarRegistry
    from app.routing.CustomRouter import CustomRouter
    from app.routing.RoutingEdge import RoutingEdge
    from app.simulation.Simulation import Simulation
    from app.streaming import RTXForward
    from kafka import KafkaProducer
    import traci

    producer = KafkaProducer(bootstrap_servers=Config.kafkaHost, api_version=(0, 10, 1))
    try:
        for topic in (Config.kafkaCommandsTopic, Config.kafkaTopicMonitoring,
                      Config.kafkaTopicTrips, Config.kafkaTopicPerformance, Config.kafkaTopicRouting):
            if not producer.partitions_for(topic):
                raise RuntimeError(f"Kafka topic not ready: {topic}")
    finally:
        producer.close()
    # Let the API consumer subscribe before simulation time starts.
    time.sleep(3)
    original_start = traci.start
    traci.start = lambda command, *args, **kwargs: original_start(
        [*command, "--seed", str(seed)], *args, **kwargs)
    CustomRouter.routeRandomSigma = initial["route_random_sigma"]
    RoutingEdge.edgeAverageInfluence = initial["edge_average_influence"]
    Config.totalCarCounter = CarRegistry.totalCarCounter = initial["total_car_counter"]
    configs, trip_count = [], 0
    current_trip = {}
    original_arrival = Car.setArrived
    original_publish = RTXForward.publish
    original_monitor_publish = KafkaProducerMonitor.publish
    original_monitor = Simulation.sendMonitorInfo

    def arrival(car, tick):
        current_trip.update(car_id=car.id, departure_tick=car.currentRouteBeginTick,
                            duration=tick - car.currentRouteBeginTick)
        return original_arrival(car, tick)

    with (output / "trips.jsonl").open("w", buffering=1) as trips:
        def publish(message, topic):
            nonlocal trip_count
            if topic == Config.kafkaTopicTrips:
                trips.write(json.dumps({**message, **current_trip}, allow_nan=False) + "\n")
                trip_count += 1
            return original_publish(message, topic)

        def monitor_publish(topic, message):
            config, step = message["configs"], message["car_stats"]["step"]
            if not configs and config != initial:
                raise RuntimeError(f"Wrong initial configuration: {config}")
            if config["total_car_counter"] != initial["total_car_counter"]:
                raise RuntimeError("Controller changed the workload")
            if not configs or configs[-1]["configuration"] != config:
                configs.append(dict(step=step, configuration=dict(config)))
            if step % 100 == 0:
                temporary = output / "progress.tmp"
                temporary.write_text(json.dumps(message))
                temporary.replace(output / "progress.json")
            if step >= horizon:
                original_monitor_publish(topic, message)
                KafkaProducerMonitor.producer.flush(timeout=10)
                raise Finished()
            return original_monitor_publish(topic, message)

        def monitor(cls, duration, routing_duration):
            return original_monitor(duration, routing_duration)

        Car.setArrived = arrival
        RTXForward.publish = publish
        KafkaProducerMonitor.publish = monitor_publish
        Simulation.sendMonitorInfo = classmethod(monitor)
        try:
            Boot.start(0, False, False)
        except Finished:
            if CarRegistry.totalTrips != trip_count:
                raise RuntimeError("Trip recording disagrees with simulator count")
            result = dict(seed=seed, final_step=Simulation.tick, trip_count=trip_count,
                          configs=configs, warmup_ticks=Config.initialWaitTicks,
                          smart_car_percentage=Config.smartCarPercentage, map=Config.sumoNet,
                          published_overhead=CarRegistry.totalTripOverheadAverage,
                          published_duration=CarRegistry.totalTripAverage)
            temporary = output / "result.tmp"
            temporary.write_text(json.dumps(result, indent=2, allow_nan=False))
            temporary.replace(output / "result.json")
            unfinished = [dict(car_id=car.id, departure_tick=car.currentRouteBeginTick,
                               age=Simulation.tick - car.currentRouteBeginTick, smart=car.smartCar)
                          for car in CarRegistry.cars.values()]
            (output / "unfinished_trips.json").write_text(json.dumps(unfinished))
        finally:
            traci.close()


if __name__ == "__main__":
    main()
