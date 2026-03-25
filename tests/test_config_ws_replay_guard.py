from app.config import Settings


def test_memory_ws_replay_rejects_multi_instance_topology():
    try:
        Settings(
            **{
                "SECURITY_STRICT_MODE": False,
                "API_REPLICA_COUNT": 2,
                "WS_REPLAY_BACKEND": "memory",
            }
        )
        raise AssertionError("Expected ValueError for unsafe multi-instance memory replay")
    except ValueError as exc:
        assert "API_REPLICA_COUNT=1" in str(exc)


def test_memory_ws_replay_allows_single_instance_topology():
    settings = Settings(
        **{
            "SECURITY_STRICT_MODE": False,
            "API_REPLICA_COUNT": 1,
            "WS_REPLAY_BACKEND": "memory",
        }
    )
    assert settings.api_replica_count == 1


def test_db_ws_replay_allows_multi_instance_topology():
    settings = Settings(
        **{
            "SECURITY_STRICT_MODE": False,
            "API_REPLICA_COUNT": 3,
            "WS_REPLAY_BACKEND": "db",
        }
    )
    assert settings.api_replica_count == 3
    assert settings.ws_replay_backend == "db"
