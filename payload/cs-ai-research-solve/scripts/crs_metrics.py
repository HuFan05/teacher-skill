"""Invocation-local aggregate measurements; no paths, contents or host telemetry."""
from contextvars import ContextVar
from functools import wraps
from time import perf_counter

_current = ContextVar('crs_metrics', default=None)

def count(key, amount=1):
    active = _current.get()
    if active is not None:
        active.values[key] = active.values.get(key, 0) + amount

def measured(name):
    def decorate(fn):
        @wraps(fn)
        def run(*args, **kwargs):
            if _current.get() is None:
                return fn(*args, **kwargs)
            start = perf_counter()
            count(name + '_calls')
            try:
                return fn(*args, **kwargs)
            finally:
                count(name + '_seconds', perf_counter() - start)
        return run
    return decorate

class Metrics:
    def __init__(self, enabled=True):
        self.enabled = enabled
        self.values = {}
    def __enter__(self):
        self.start = perf_counter()
        self.token = _current.set(self if self.enabled else None)
        return self
    def __exit__(self, *exc):
        self.elapsed = perf_counter() - self.start
        _current.reset(self.token)
    def report(self):
        return {'wall_seconds': self.elapsed, 'counters': dict(self.values),
                'timing_scope': 'Inclusive nested operation times; do not add them to wall time.',
                'byte_scope': 'Instrumented file hashing and staged writes only; not all OS I/O.',
                'peak_memory': None, 'peak_memory_status': 'not_measured',
                'measurement_scope': 'This invocation only; no persistent cache or telemetry.'}
