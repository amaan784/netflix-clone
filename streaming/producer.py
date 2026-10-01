"""Producer: publishes keyed records to a topic."""
from __future__ import annotations

from streaming.broker import LogBroker


class Producer:
    def __init__(self, broker: LogBroker):
        self.broker = broker

    def send(self, topic: str, value: dict, key=None) -> dict:
        # keying by profile keeps one viewer's events ordered in a single partition
        return self.broker.produce(topic, value, key=key)
