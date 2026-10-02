"""Picks the streaming backend: real Kafka when KAFKA_BOOTSTRAP is reachable,
otherwise the in-process log broker. Same interface either way."""
from __future__ import annotations

import logging
import os

from streaming.topics import TOPICS, SINK_GROUP

logger = logging.getLogger("stream")


class LogStreamBackend:
    kind = "log"

    def __init__(self):
        from streaming.broker import get_broker
        from streaming.producer import Producer
        self.broker = get_broker()
        self.producer = Producer(self.broker)

    def make_consumer(self, group: str, topics: list[str]):
        from streaming.consumer import Consumer
        return Consumer(self.broker, group, topics)

    def stats(self) -> dict:
        return self.broker.stats()


class KafkaStreamBackend:
    kind = "kafka"

    def __init__(self, bootstrap: str):
        from streaming.kafka_backend import ensure_topics, KafkaProducerAdapter
        self.bootstrap = bootstrap
        ensure_topics(bootstrap, TOPICS)
        self.producer = KafkaProducerAdapter(bootstrap)

    def make_consumer(self, group: str, topics: list[str]):
        from streaming.kafka_backend import KafkaConsumerAdapter
        return KafkaConsumerAdapter(self.bootstrap, group, topics)

    def stats(self) -> dict:
        from streaming.kafka_backend import broker_stats
        return broker_stats(self.bootstrap, TOPICS, SINK_GROUP)


def get_stream_backend():
    bootstrap = os.environ.get("KAFKA_BOOTSTRAP", "").strip()
    if bootstrap:
        try:
            from streaming.kafka_backend import ping
            if ping(bootstrap):
                logger.info("streaming backend: Kafka @ %s", bootstrap)
                return KafkaStreamBackend(bootstrap)
            logger.warning("Kafka not reachable at %s; falling back to log broker", bootstrap)
        except Exception as e:
            logger.warning("Kafka init failed (%s); falling back to log broker", e)
    logger.info("streaming backend: in-process log broker")
    return LogStreamBackend()
