"""The in-process log broker backend: producer, consumers and stats."""
from __future__ import annotations

import logging

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


def get_stream_backend():
    logger.info("streaming backend: in-process log broker")
    return LogStreamBackend()
