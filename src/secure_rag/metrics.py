"""Low-cardinality request and RAG stage metrics."""

from prometheus_client import Counter, Histogram

REQUESTS = Counter("rag_http_requests_total", "HTTP requests", ["method", "route", "status"])
REQUEST_SECONDS = Histogram(
    "rag_http_request_duration_seconds", "HTTP request duration", ["method", "route"],
    buckets=(.01, .025, .05, .1, .25, .5, 1, 2.5, 5, 10, 30, 60, 120),
)
STAGE_SECONDS = Histogram(
    "rag_stage_duration_seconds", "RAG operation duration", ["stage"],
    buckets=(.01, .025, .05, .1, .25, .5, 1, 2.5, 5, 10, 30, 60, 120),
)
ANSWER_OUTCOMES = Counter(
    "rag_answer_outcomes_total", "Answer outcomes", ["outcome"],
)
