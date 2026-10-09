"""Topic names and partition counts."""

WATCH_EVENTS = "watch-events"
RATINGS = "ratings"

# topic -> partition count
TOPICS = {
    WATCH_EVENTS: 3,
    RATINGS: 2,
}

SINK_GROUP = "warehouse-sink"
LAKE_GROUP = "lake-sink"  # the lakehouse reads the same topics at its own pace
