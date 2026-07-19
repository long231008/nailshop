class UserAlreadyActiveError(Exception):
    pass


class UserNotFoundError(Exception):
    pass


class OtpExpiredError(Exception):
    pass


class OtpInvalidError(Exception):
    pass
