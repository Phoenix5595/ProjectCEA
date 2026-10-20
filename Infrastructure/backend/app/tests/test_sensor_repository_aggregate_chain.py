from app.repositories.sensor_repository import _pick_aggregate_tier, _tier_source_sql


def test_five_minute_tier_gates_values_on_parent_frontier_and_invalidations() -> None:
    tier = _pick_aggregate_tier(24.0)
    sql = _tier_source_sql(tier)

    assert tier.name == "5min"
    assert "user_view_name IN ('monitoring_measurement_5min','monitoring_measurement_1min')" in sql
    assert "MIN(_timescaledb_functions.to_timestamp" in sql
    assert "r.bucket + INTERVAL '5 minutes' > coverage.through" in sql
    assert "FROM measurement raw" in sql


def test_hourly_tier_checks_all_hierarchical_parents() -> None:
    tier = _pick_aggregate_tier(7.0 * 24)
    sql = _tier_source_sql(tier)

    assert tier.name == "hourly"
    assert "user_view_name IN ('monitoring_measurement_1hour','monitoring_measurement_1min')" in sql


def test_daily_tier_checks_hourly_and_one_minute_ancestors() -> None:
    tier = _pick_aggregate_tier(30.0 * 24)
    sql = _tier_source_sql(tier)

    assert tier.name == "daily"
    assert (
        "user_view_name IN ('monitoring_measurement_1day','monitoring_measurement_1hour',"
        "'monitoring_measurement_1min')"
    ) in sql
