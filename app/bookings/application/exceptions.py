class BookingNotFoundError(Exception):
    pass


class BookingDetailNotFoundError(Exception):
    pass


class InvalidBookingItemsError(Exception):
    pass


class DailyBookingLimitExceededError(Exception):
    pass


class StaffConflictError(Exception):
    pass


class InvalidBookingStateError(Exception):
    pass
