"""In-process crawl job registry."""
import threading


_jobs = {}
_lock = threading.Lock()


def register(crawler):
    """Register a crawler by its persisted crawl_id."""
    if not crawler or not crawler.crawl_id:
        return None

    crawl_id = int(crawler.crawl_id)
    with _lock:
        _jobs[crawl_id] = crawler
    return crawl_id


def get(crawl_id):
    """Return the active crawler for crawl_id, if this process owns it."""
    with _lock:
        return _jobs.get(int(crawl_id))


def unregister(crawl_id):
    """Remove a crawler from the active registry."""
    with _lock:
        return _jobs.pop(int(crawl_id), None)


def is_active(crawl_id):
    """True when crawl_id is still attached to an in-process crawler."""
    with _lock:
        return int(crawl_id) in _jobs


def list_active_ids():
    """Return active crawl ids for diagnostics/tests."""
    with _lock:
        return sorted(_jobs)
