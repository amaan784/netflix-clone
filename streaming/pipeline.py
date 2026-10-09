"""
Consumer app: reads the watch/rating topics, keeps a windowed trending tally,
and hands each record to a sink. Demo: python -m streaming.pipeline
"""
from __future__ import annotations

import logging
import threading
import time
from collections import deque

from streaming.topics import WATCH_EVENTS, RATINGS, SINK_GROUP

logger = logging.getLogger("stream")


class LiveTrending:
    """Sliding time-window count of recent activity per title."""

    def __init__(self, window_seconds: int = 1800):
        self.window = window_seconds
        self.events: deque = deque()      # (epoch, title_id, weight)
        self._lock = threading.Lock()

    def add(self, title_id, weight: float = 1.0):
        now = time.time()
        with self._lock:
            self.events.append((now, int(title_id), float(weight)))
            self._prune(now)

    def _prune(self, now):
        cut = now - self.window
        while self.events and self.events[0][0] < cut:
            self.events.popleft()

    def top(self, k: int = 10):
        now = time.time()
        with self._lock:
            self._prune(now)
            agg: dict[int, float] = {}
            for _, tid, w in self.events:
                agg[tid] = agg.get(tid, 0.0) + w
        return sorted(agg.items(), key=lambda x: x[1], reverse=True)[:k]

    def total(self) -> int:
        with self._lock:
            return len(self.events)


class StreamProcessor:
    def __init__(self, consumer, stats_fn,
                 on_watch=None, on_rating=None, window_seconds: int = 1800):
        self.consumer = consumer
        self._stats_fn = stats_fn
        self.on_watch = on_watch
        self.on_rating = on_rating
        self.trending = LiveTrending(window_seconds)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.started_at = time.time()
        self.metrics = {"processed": 0, "watch": 0, "rating": 0, "last_event_ts": None}

    def process_batch(self) -> int:
        recs = self.consumer.poll()
        for r in recs:
            v, topic = r["value"], r["topic"]
            try:
                if topic == WATCH_EVENTS:
                    if self.on_watch:
                        self.on_watch(v)
                    self.trending.add(v["title_id"], 1.0 + float(v.get("completion", 0.0)))
                    self.metrics["watch"] += 1
                elif topic == RATINGS:
                    if self.on_rating:
                        self.on_rating(v)
                    self.trending.add(v["title_id"], max(0.0, float(v.get("rating", 3)) - 3.0))
                    self.metrics["rating"] += 1
                self.metrics["processed"] += 1
                self.metrics["last_event_ts"] = r["ts"]
            except Exception:
                logger.exception("handler failed for topic %s", topic)
        if recs:
            self.consumer.commit()
        return len(recs)

    def run_forever(self, interval: float = 0.4):
        while not self._stop.is_set():
            try:
                if self.process_batch() == 0:
                    self._stop.wait(interval)
            except Exception:
                logger.exception("stream loop error")
                self._stop.wait(interval)

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self.run_forever, name="stream-consumer",
                                        daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)

    def stats(self) -> dict:
        uptime = max(1e-6, time.time() - self.started_at)
        m = self.metrics
        return {
            "consumer_group": SINK_GROUP,
            "lag": self.consumer.lag(),
            "processed": m["processed"],
            "watch_events": m["watch"],
            "rating_events": m["rating"],
            "events_per_min": round(m["processed"] / uptime * 60, 1),
            "last_event_ts": m["last_event_ts"],
            "window_seconds": self.trending.window,
            "live_window_events": self.trending.total(),
            "broker": self._stats_fn(),
        }


def _demo():
    import json
    import random
    import tempfile
    from streaming.broker import LogBroker
    from streaming.consumer import Consumer
    from streaming.producer import Producer

    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(message)s")
    # a throwaway broker: the demo's made-up events never reach the app's own log
    with tempfile.TemporaryDirectory(prefix="netclone-stream-demo-") as tmp:
        broker = LogBroker(tmp)
        producer = Producer(broker)
        consumer = Consumer(broker, SINK_GROUP, [WATCH_EVENTS, RATINGS])

        seen_watch, seen_rating = [], []
        proc = StreamProcessor(consumer, broker.stats,
                               on_watch=lambda v: seen_watch.append(v),
                               on_rating=lambda v: seen_rating.append(v))

        rng = random.Random(7)
        print("Producing 40 sample events...")
        for _ in range(40):
            pid = rng.randint(1, 50)
            tid = rng.randint(1, 60)
            if rng.random() < 0.7:
                ack = producer.send(WATCH_EVENTS,
                                    {"profile_id": pid, "title_id": tid,
                                     "watch_seconds": rng.randint(60, 3600),
                                     "completion": round(rng.random(), 2)},
                                    key=pid)
            else:
                ack = producer.send(RATINGS,
                                    {"profile_id": pid, "title_id": tid,
                                     "rating": rng.randint(1, 5)}, key=pid)
            print(f"  -> {ack['topic']:13} partition={ack['partition']} offset={ack['offset']}")

        print("\nConsuming...")
        n = proc.process_batch()
        print(f"  processed {n} records (watch={len(seen_watch)}, rating={len(seen_rating)})")

        print("\nLive trending (title_id, score):", proc.trending.top(5))
        print("\nStats:")
        print(json.dumps(proc.stats(), indent=2))


if __name__ == "__main__":
    _demo()
