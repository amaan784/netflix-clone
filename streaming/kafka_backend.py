"""Kafka backend (kafka-python) with the same interface as the log broker."""
from __future__ import annotations

import json
import logging
from datetime import datetime

from kafka import KafkaProducer, KafkaConsumer, TopicPartition
from kafka.admin import KafkaAdminClient, NewTopic
from kafka.errors import TopicAlreadyExistsError

logger = logging.getLogger("stream")


def ping(bootstrap: str, timeout: float = 4.0) -> bool:
    try:
        admin = KafkaAdminClient(
            bootstrap_servers=bootstrap,
            request_timeout_ms=int(timeout * 1000),
            api_version_auto_timeout_ms=int(timeout * 1000),
        )
        admin.list_topics()
        admin.close()
        return True
    except Exception:
        return False


def ensure_topics(bootstrap: str, topics: dict):
    admin = KafkaAdminClient(bootstrap_servers=bootstrap)
    try:
        for name, partitions in topics.items():
            try:
                admin.create_topics([NewTopic(name=name, num_partitions=partitions,
                                              replication_factor=1)])
                logger.info("created topic %s (%d partitions)", name, partitions)
            except TopicAlreadyExistsError:
                pass
            except Exception as e:
                logger.warning("create_topics(%s): %s", name, e)
    finally:
        admin.close()


def _iso(ms):
    try:
        return datetime.fromtimestamp(ms / 1000).isoformat(timespec="seconds")
    except Exception:
        return None


class KafkaProducerAdapter:
    def __init__(self, bootstrap: str):
        self.p = KafkaProducer(
            bootstrap_servers=bootstrap,
            value_serializer=lambda v: json.dumps(v).encode("utf-8"),
            key_serializer=lambda k: None if k is None else str(k).encode("utf-8"),
            acks=1, linger_ms=5,
        )

    def send(self, topic: str, value: dict, key=None) -> dict:
        md = self.p.send(topic, value=value, key=key).get(timeout=10)
        return {"topic": topic, "partition": md.partition, "offset": md.offset}


class KafkaConsumerAdapter:
    def __init__(self, bootstrap: str, group: str, topics: list[str]):
        self.group = group
        self.topics = topics
        self.c = KafkaConsumer(
            *topics,
            bootstrap_servers=bootstrap,
            group_id=group,
            enable_auto_commit=False,
            auto_offset_reset="earliest",
            value_deserializer=lambda b: json.loads(b.decode("utf-8")),
            key_deserializer=lambda b: None if b is None else b.decode("utf-8"),
        )

    def poll(self, max_records: int = 200) -> list:
        out = []
        batch = self.c.poll(timeout_ms=400, max_records=max_records)
        for tp, recs in batch.items():
            for r in recs:
                out.append({"topic": tp.topic, "partition": r.partition,
                            "offset": r.offset, "key": r.key, "value": r.value,
                            "ts": _iso(r.timestamp)})
        return out

    def commit(self):
        self.c.commit()

    def lag(self) -> int:
        total = 0
        try:
            assigned = self.c.assignment()
            if not assigned:
                return 0
            ends = self.c.end_offsets(list(assigned))
            for tp in assigned:
                pos = self.c.position(tp)
                total += max(0, ends.get(tp, 0) - pos)
        except Exception:
            return 0
        return total


def broker_stats(bootstrap: str, topics: dict, group: str) -> dict:
    """Topic/partition message counts and committed consumer-group offsets."""
    consumer = KafkaConsumer(bootstrap_servers=bootstrap)
    topics_out = []
    try:
        for name, _ in topics.items():
            parts = consumer.partitions_for_topic(name) or set()
            detail, total = [], 0
            for p in sorted(parts):
                tp = TopicPartition(name, p)
                end = consumer.end_offsets([tp])[tp]
                detail.append({"partition": p, "messages": int(end)})
                total += int(end)
            topics_out.append({"topic": name, "partitions": len(parts),
                               "total_messages": total, "detail": detail})
    finally:
        consumer.close()

    offsets = {group: {}}
    try:
        admin = KafkaAdminClient(bootstrap_servers=bootstrap)
        committed = admin.list_consumer_group_offsets(group)
        for tp, meta in committed.items():
            offsets[group].setdefault(tp.topic, {})[str(tp.partition)] = meta.offset
        admin.close()
    except Exception:
        pass

    return {"topics": topics_out, "consumer_offsets": offsets}
