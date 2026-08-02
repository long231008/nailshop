class BookingNotFoundError(Exception):
    pass


class BookingDetailNotFoundError(Exception):
    pass


class InvalidBookingItemsError(Exception):
    pass


class DailyBookingLimitExceededError(Exception):
    pass


class NotLegOwnerError(Exception):
    """A staff member tried to complete a leg that is not theirs."""


class StaffConflictError(Exception):
    """The salon closed this time: a branch-wide slot lock covers the visit."""

    pass


class InvalidBookingStateError(Exception):
    pass


class NotBookingOwnerError(Exception):
    pass
