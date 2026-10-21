from __future__ import annotations

import logging

import pytest

from shared.replay_buffer import ReplayBuffer


def test_offer_drops_newest_items_when_capacity_is_reached() -> None:
    buffer: ReplayBuffer[int] = ReplayBuffer(2, name="test")

    assert buffer.offer([1, 2, 3]) == 2
    assert buffer.peek(10) == [1, 2]
    assert buffer.dropped == 1


def test_peek_and_consume_keep_the_oldest_rows_in_fifo_order() -> None:
    buffer: ReplayBuffer[int] = ReplayBuffer(4, name="test")
    buffer.offer([1, 2, 3])

    assert buffer.peek(2) == [1, 2]
    assert len(buffer) == 3
    buffer.consume(2)

    assert buffer.peek(4) == [3]
    assert len(buffer) == 1


def test_overflow_is_logged_once_until_buffer_recovers_below_ninety_percent(
    caplog: pytest.LogCaptureFixture,
) -> None:
    buffer: ReplayBuffer[int] = ReplayBuffer(10, name="test")

    with caplog.at_level(logging.ERROR, logger="shared.replay_buffer"):
        buffer.offer(list(range(10)))
        buffer.offer([10])
        buffer.consume(1)
        buffer.offer([11])

        assert sum("replay buffer test full" in record.message for record in caplog.records) == 1

        buffer.consume(2)
        buffer.offer([12, 13])
        buffer.offer([14])

    assert sum("replay buffer test full" in record.message for record in caplog.records) == 2
