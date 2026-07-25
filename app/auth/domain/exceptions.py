class UserAlreadyActiveError(Exception):
    pass


class UserNotFoundError(Exception):
    pass


class UserBlockedError(Exception):
    pass


class OtpExpiredError(Exception):
    pass


class OtpInvalidError(Exception):
    pass


class OtpTooManyAttemptsError(Exception):
    pass


class OtpResendTooSoonError(Exception):
    pass
