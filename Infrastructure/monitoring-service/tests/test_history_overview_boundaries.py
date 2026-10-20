"""Consumer-visible display budgets, clipped edges and nullable summary invariants."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from monitoring_service.history_buckets import choose_history_bucket, history_bucket_bounds
from monitoring_service.history_overview_models import (
    HistoryOverviewPoint,
    MonitoringValidationError,
)
from monitoring_service.history_overview_repository import _control_series_groups
from monitoring_service.history_overview_sql import SENSOR_OVERVIEW_STATEMENT


def test_sensor_overview_freshness_covers_measurement_ancestors():
    statement = " ".join(SENSOR_OVERVIEW_STATEMENT.split())
    hourly_tiers = (
        "cagg_name IN ('monitoring_measurement_1hour', 'monitoring_measurement_1min')"
    )
    daily_tiers = (
        "cagg_name IN ('monitoring_measurement_1day', "
        "'monitoring_measurement_1hour', 'monitoring_measurement_1min')"
    )
    assert statement.count(hourly_tiers) == 2
    assert statement.count(daily_tiers) == 2


@pytest.mark.parametrize(
    "budget,seconds,slots", [(1000, 18000, 880), (600, 28800, 550), (400, 43200, 367)]
)
def test_toronto_six_month_range_fits_actual_intersecting_slot_budget(budget, seconds, slots):
    # June 1 through December 1 local midnight spans the fall DST transition.
    start = datetime(2026, 6, 1, 4, tzinfo=UTC)
    end = datetime(2026, 12, 1, 5, tzinfo=UTC)
    bucket = choose_history_bucket(start, end, budget)
    bounds = history_bucket_bounds(start, end, bucket)
    assert bucket.seconds == seconds
    assert len(bounds) == slots <= budget
    assert bounds[0][0] == start
    assert bounds[-1][1] == end
    assert all(left[1] == right[0] for left, right in zip(bounds, bounds[1:]))
    assert sum((hi - lo for lo, hi, _ in bounds), timedelta()) == end - start


def test_budget_failure_does_not_widen_past_twelve_hours():
    start = datetime(2026, 6, 1, 4, tzinfo=UTC)
    end = datetime(2026, 12, 1, 5, tzinfo=UTC)
    with pytest.raises(MonitoringValidationError):
        choose_history_bucket(start, end, 300)


def test_old_ranges_have_the_same_pixel_policy_without_an_age_cutoff():
    start = datetime(2019, 3, 1, tzinfo=UTC)
    end = datetime(2019, 9, 1, tzinfo=UTC)
    bucket = choose_history_bucket(start, end, 1000)
    assert bucket.seconds == 18000
    assert len(history_bucket_bounds(start, end, bucket)) <= 1000


def test_microsecond_edges_are_clipped_not_rounded_into_other_samples():
    start = datetime(2026, 1, 1, 0, 0, 0, 1, tzinfo=UTC)
    end = datetime(2026, 1, 1, 1, 0, 0, 1, tzinfo=UTC)
    bounds = history_bucket_bounds(start, end, choose_history_bucket(start, end, 10))
    assert bounds == (
        (start, datetime(2026, 1, 1, 1, tzinfo=UTC), True),
        (datetime(2026, 1, 1, 1, tzinfo=UTC), end, True),
    )


@pytest.mark.parametrize("average,minimum,maximum", [(1.0, None, None), (None, 0.0, 1.0)])
def test_zero_sample_summary_cannot_fabricate_statistics(average, minimum, maximum):
    with pytest.raises(MonitoringValidationError):
        HistoryOverviewPoint(
            start=datetime(2026, 1, 1, tzinfo=UTC),
            end=datetime(2026, 1, 1, 1, tzinfo=UTC),
            sample_count=0,
            partial=False,
            average=average,
            minimum=minimum,
            maximum=maximum,
        )


def test_requested_pid_spans_merge_by_covered_seconds_not_sample_count():
    start = datetime(2026, 1, 1, tzinfo=UTC)
    end = start + timedelta(minutes=1)
    identity = {
        "family": "pid",
        "idx": 0,
        "location": "Flower Room",
        "cluster": "main",
        "device_name": "flower_heater",
        "value_sum": None,
        "value_sum_squares": None,
        "present": True,
    }
    rows = [
        {
            **identity,
            "sample_count": 1,
            "min_value": Decimal("20"),
            "max_value": Decimal("20"),
            "weighted_value": Decimal("200"),
            "covered_seconds": Decimal("10"),
        },
        {
            **identity,
            "sample_count": 1,
            "min_value": Decimal("80"),
            "max_value": Decimal("80"),
            "weighted_value": Decimal("4000"),
            "covered_seconds": Decimal("50"),
        },
    ]

    lights, requested_pid = _control_series_groups(rows, ((start, end, False),))

    assert lights == ()
    assert requested_pid[0].points[0].sample_count == 2
    assert requested_pid[0].points[0].average == 70.0
    assert requested_pid[0].points[0].minimum == 20.0
    assert requested_pid[0].points[0].maximum == 80.0


def test_single_source_row_can_have_a_predecessor_weighted_average():
    point = HistoryOverviewPoint(
        start=datetime(2026, 1, 1, tzinfo=UTC),
        end=datetime(2026, 1, 1, 0, 1, tzinfo=UTC),
        sample_count=1,
        partial=False,
        average=70,
        minimum=20,
        maximum=80,
    )

    assert point.sample_count == 1
