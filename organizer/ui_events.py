"""Coalesce replaceable previews without dropping logs or completion events."""
import queue
from datetime import datetime
from .diagnostics import ZONE


class LogText(str):
    def __new__(cls, value):
        instance = super().__new__(cls, value)
        instance.created = datetime.now(ZONE)
        return instance


class UIEventQueue(queue.Queue):
    def __init__(self):
        super().__init__()
        self.latest = {}

    def put(self, item, block=True, timeout=None):
        kind, value = item
        with self.not_full:
            if kind in ('matches', 'progress'):
                if kind in self.latest:
                    self.latest[kind][1] = value
                    return
            elif kind != 'log':
                # Terminal/control events form boundaries between operations.
                self.latest.clear()
            node = [kind, value]
            if kind in ('matches', 'progress'):
                self.latest[kind] = node
            self._put(node)
            self.unfinished_tasks += 1
            self.not_empty.notify()

    def _get(self):
        node = super()._get()
        if self.latest.get(node[0]) is node:
            del self.latest[node[0]]
        return tuple(node)
