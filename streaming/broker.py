"""
In-process append-only log with Kafka-style topics, partitions and offsets.
Records persist to per-partition .log files; consumer groups commit offsets.
"""
from __future__ import annotations

import hashlib
import json
import threading
from datetime import datetime
from pathlib import Path

from paths import STREAM_DIR
from streaming.topics import TOPICS


class LogBroker:
    def __init__(self, base_dir: Path | str = STREAM_DIR):
        self.base = Path(base_dir)
        self.base.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._log: dict[str, dict[int, list]] = {}      # topic -> partition -> records
        self._partitions: dict[str, int] = {}           # topic -> count
        self._offsets: dict = {}                         # group -> topic -> partition -> offset
        self._offsets_path = self.base / "offsets.json"
        self._load_offsets()
        for topic, n in TOPICS.items():
            self.create_topic(topic, n)

    def create_topic(self, topic: str, partitions: int = 3):
        with self._lock:
            if topic in self._partitions:
                return
            self._partitions[topic] = partitions
            self._log[topic] = {p: [] for p in range(partitions)}
            (self.base / topic).mkdir(parents=True, exist_ok=True)
            for p in range(partitions):
                seg = self.base / topic / f"p{p}.log"
                if seg.exists():
                    recs = []
                    for line in seg.read_text(encoding="utf-8").splitlines():
                        if line.strip():
                            recs.append(json.loads(line))
                    self._log[topic][p] = recs

    def num_partitions(self, topic: str) -> int:
        return self._partitions.get(topic, 0)

    def _partition_for(self, topic: str, key) -> int:
        n = self._partitions[topic]
        if key is None:
            return 0
        h = hashlib.md5(str(key).encode()).hexdigest()
        return int(h, 16) % n

    def produce(self, topic: str, value: dict, key=None) -> dict:
        with self._lock:
            if topic not in self._partitions:
                self.create_topic(topic)
            p = self._partition_for(topic, key)
            offset = len(self._log[topic][p])
            rec = {
                "offset": offset,
                "partition": p,
                "key": None if key is None else str(key),
                "value": value,
                "ts": datetime.now().isoformat(timespec="seconds"),
            }
            self._log[topic][p].append(rec)
            with open(self.base / topic / f"p{p}.log", "a", encoding="utf-8") as f:
                f.write(json.dumps(rec) + "\n")
            return {"topic": topic, "partition": p, "offset": offset}

    def fetch(self, topic: str, partition: int, offset: int, max_records: int = 200) -> list:
        with self._lock:
            log = self._log[topic][partition]
            return list(log[offset: offset + max_records])

    def high_water(self, topic: str, partition: int) -> int:
        with self._lock:
            return len(self._log[topic][partition])

    def commit(self, group: str, topic: str, partition: int, offset: int):
        with self._lock:
            self._offsets.setdefault(group, {}).setdefault(topic, {})[str(partition)] = offset
            self._save_offsets()

    def committed(self, group: str, topic: str, partition: int) -> int:
        return self._offsets.get(group, {}).get(topic, {}).get(str(partition), 0)

    def _load_offsets(self):
        if self._offsets_path.exists():
            try:
                self._offsets = json.loads(self._offsets_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                self._offsets = {}

    def _save_offsets(self):
        # Another process (python -m lakehouse.build) may commit to this file too.
        # Offsets only ever move forward, so merge and keep the furthest of each.
        merged = {}
        if self._offsets_path.exists():
            try:
                merged = json.loads(self._offsets_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                merged = {}
        for group, topics in self._offsets.items():
            for topic, parts in topics.items():
                saved = merged.setdefault(group, {}).setdefault(topic, {})
                for p, offset in parts.items():
                    saved[p] = max(saved.get(p, 0), offset)
        self._offsets = merged
        self._offsets_path.write_text(json.dumps(merged, indent=2), encoding="utf-8")

    def stats(self) -> dict:
        with self._lock:
            topics = []
            for topic, n in self._partitions.items():
                parts = [{"partition": p, "messages": len(self._log[topic][p])}
                         for p in range(n)]
                topics.append({
                    "topic": topic,
                    "partitions": n,
                    "total_messages": sum(x["messages"] for x in parts),
                    "detail": parts,
                })
            return {"topics": topics, "consumer_offsets": self._offsets}


_broker: LogBroker | None = None


def get_broker() -> LogBroker:
    global _broker
    if _broker is None:
        _broker = LogBroker()
    return _broker
