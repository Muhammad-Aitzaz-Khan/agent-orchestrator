"""A least-recently-used (LRU) cache."""


class LRUCache:
    """Holds at most `capacity` items.

    - get(key) returns the value, or None if missing, and marks the key as recently used.
    - put(key, value) inserts or updates a key; updating also marks it as recently used
      and never evicts anything.
    - When the cache is full, inserting a NEW key evicts the least recently used key.
    - A capacity of 0 means nothing is ever stored.
    """

    def __init__(self, capacity):
        self.capacity = capacity
        self.data = {}
        self.order = []  # least recently used first

    def get(self, key):
        if key not in self.data:
            return None
        return self.data[key]

    def put(self, key, value):
        if len(self.data) >= self.capacity:
            oldest = self.order.pop(0)
            del self.data[oldest]
        self.data[key] = value
        self.order.append(key)

    def __len__(self):
        return len(self.data)
