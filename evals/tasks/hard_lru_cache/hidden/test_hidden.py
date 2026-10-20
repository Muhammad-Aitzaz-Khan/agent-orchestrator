from lru import LRUCache


def test_update_does_not_evict():
    cache = LRUCache(2)
    cache.put("a", 1)
    cache.put("b", 2)
    cache.put("a", 10)
    assert len(cache) == 2
    assert cache.get("a") == 10
    assert cache.get("b") == 2


def test_update_marks_recently_used():
    cache = LRUCache(2)
    cache.put("a", 1)
    cache.put("b", 2)
    cache.put("a", 10)
    cache.put("c", 3)
    assert cache.get("b") is None
    assert cache.get("a") == 10


def test_capacity_zero():
    cache = LRUCache(0)
    cache.put("a", 1)
    assert cache.get("a") is None
    assert len(cache) == 0


def test_many_operations():
    cache = LRUCache(3)
    for i in range(10):
        cache.put(i, i * i)
    assert len(cache) == 3
    assert [cache.get(i) for i in (7, 8, 9)] == [49, 64, 81]
