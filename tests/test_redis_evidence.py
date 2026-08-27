from __future__ import annotations

import pytest
import redis as redis_lib

from reliability_lab.chaos import collect_redis_evidence

REDIS_URL = "redis://localhost:6379/0"
EVIDENCE_PATTERN = "rl:cache:evidence:*"


def _redis_available() -> bool:
    try:
        client = redis_lib.Redis.from_url(REDIS_URL)
        client.ping()
        client.close()
        return True
    except redis_lib.RedisError:
        return False


pytestmark = pytest.mark.skipif(not _redis_available(), reason="Redis is required for evidence test")


def test_collect_redis_evidence_proves_shared_state_and_key_visibility() -> None:
    evidence = collect_redis_evidence(REDIS_URL, ttl_seconds=60, similarity_threshold=0.5)

    assert evidence["available"] is True
    assert evidence["shared_response"] == "shared cache evidence response"
    assert evidence["score"] == 1.0
    keys = evidence["keys"]
    assert isinstance(keys, list)
    assert any(str(key).startswith("rl:cache:evidence:") for key in keys)

    client = redis_lib.Redis.from_url(REDIS_URL, decode_responses=True)
    for key in client.scan_iter(EVIDENCE_PATTERN):
        client.delete(key)
    client.close()
