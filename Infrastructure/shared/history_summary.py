"""Exact additive NUMERIC summary state shared by history readers."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal, localcontext


def decimal(value: object) -> Decimal:
    """Convert a PostgreSQL NUMERIC result to its exact decimal value."""
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def exact_add(left: Decimal, right: Decimal) -> Decimal:
    """Add numeric sufficient statistics without context-rounding their digits."""
    with localcontext() as context:
        context.prec = max(
            28,
            max(left.adjusted(), right.adjusted())
            - min(int(left.as_tuple().exponent), int(right.as_tuple().exponent))
            + 2,
        )
        return left + right


def float_or_none(value: Decimal | None) -> float | None:
    """Convert only a finished wire statistic, never a source sample, to float."""
    return None if value is None else float(value)


@dataclass(slots=True)
class SummaryState:
    """Count, sums, squared sums and extrema for disjoint source contributions."""

    count: int = 0
    value_sum: Decimal = Decimal(0)
    value_sum_squares: Decimal = Decimal(0)
    minimum: Decimal | None = None
    maximum: Decimal | None = None
    present: bool = False

    def apply(
        self,
        record: Mapping[str, object],
        *,
        minimum_key: str = "min_value",
        maximum_key: str = "max_value",
    ) -> None:
        count = record.get("sample_count")
        if count is not None:
            if type(count) is not int:
                raise TypeError("summary sample_count must be an integer")
            self.count += count
        value_sum = record.get("value_sum")
        if value_sum is not None:
            self.value_sum = exact_add(self.value_sum, decimal(value_sum))
        squares = record.get("value_sum_squares")
        if squares is not None:
            self.value_sum_squares = exact_add(self.value_sum_squares, decimal(squares))
        minimum = record.get(minimum_key)
        if minimum is not None:
            value = decimal(minimum)
            self.minimum = value if self.minimum is None else min(self.minimum, value)
        maximum = record.get(maximum_key)
        if maximum is not None:
            value = decimal(maximum)
            self.maximum = value if self.maximum is None else max(self.maximum, value)
        self.present = self.present or record.get("present") is True

    def absorb(self, state: SummaryState) -> None:
        """Fold one disjoint sufficient-statistic state into this state."""
        self.count += state.count
        self.value_sum = exact_add(self.value_sum, state.value_sum)
        self.value_sum_squares = exact_add(self.value_sum_squares, state.value_sum_squares)
        if state.minimum is not None:
            self.minimum = (
                state.minimum if self.minimum is None else min(self.minimum, state.minimum)
            )
        if state.maximum is not None:
            self.maximum = (
                state.maximum if self.maximum is None else max(self.maximum, state.maximum)
            )
        self.present = self.present or state.present

    @property
    def average_decimal(self) -> Decimal | None:
        if not self.count:
            return None
        with localcontext() as context:
            context.prec = max(
                28, len(self.value_sum.as_tuple().digits) + len(str(self.count)) + 16
            )
            return self.value_sum / Decimal(self.count)

    def sample_stddev_decimal(self) -> Decimal | None:
        if self.count < 2:
            return Decimal(0) if self.count == 1 else None
        count = Decimal(self.count)
        with localcontext() as context:
            context.prec = max(
                28,
                2 * len(self.value_sum.as_tuple().digits) + len(str(self.count)) + 16,
                len(self.value_sum_squares.as_tuple().digits) + len(str(self.count)) + 16,
            )
            variance = (self.value_sum_squares - self.value_sum * self.value_sum / count) / (
                count - 1
            )
            return max(variance, Decimal(0)).sqrt()
