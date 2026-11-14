import time
from concurrent.futures import ThreadPoolExecutor

from tagger.__main__ import _throttled


def test_throttled_spaces_calls_across_threads():
    starts = []
    call = _throttled(lambda i: starts.append(time.monotonic()), 0.1)
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(call, range(4)))
    starts.sort()
    assert all(b - a >= 0.09 for a, b in zip(starts, starts[1:]))
