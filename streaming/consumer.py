"""Consumer: reads records from assigned partitions and commits offsets per group."""
from __future__ import annotations

from streaming.broker import LogBroker


class Consumer:
    def __init__(self, broker: LogBroker, group: str, topics: list[str]):
        self.broker = broker
        self.group = group
        self.topics = topics
        self._pos: dict[tuple, int] = {}
        for t in topics:
            for p in range(broker.num_partitions(t)):
                self._pos[(t, p)] = broker.committed(group, t, p)

    def poll(self, max_records: int = 200) -> list:
        out = []
        for (t, p), off in self._pos.items():
            recs = self.broker.fetch(t, p, off, max_records)
            if recs:
                self._pos[(t, p)] = off + len(recs)
                for r in recs:
                    r = dict(r)
                    r["topic"] = t
                    out.append(r)
        return out

    def commit(self):
        for (t, p), off in self._pos.items():
            self.broker.commit(self.group, t, p, off)

    def lag(self) -> int:
        return sum(self.broker.high_water(t, p) - off
                   for (t, p), off in self._pos.items())
