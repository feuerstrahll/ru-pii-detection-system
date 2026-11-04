"""Local loader for the frozen hivetrace/pii-bench views."""

from .loader import PII_BENCH_LABEL_MAPPING, load_pii_bench_views

__all__ = ["PII_BENCH_LABEL_MAPPING", "load_pii_bench_views"]
