"""Domain and application errors. Messages are Uzbek Latin when shown to users."""

from __future__ import annotations


class AppError(Exception):
    """Base error for expected tournament failures."""


class PermissionDenied(AppError):
    def __init__(self, message: str = "Bu amal faqat administrator uchun.") -> None:
        super().__init__(message)


class DatabaseUnavailable(AppError):
    def __init__(
        self,
        message: str = (
            "Ma’lumotlar bazasi vaqtincha ishlamayapti. O‘zgarish saqlanmadi. "
            "Keyinroq urinib ko‘ring."
        ),
    ) -> None:
        super().__init__(message)


class InvalidTransition(AppError):
    pass


class InvalidConfig(AppError):
    pass


class DuplicateRegistration(AppError):
    def __init__(self, message: str = "Siz allaqachon ro‘yxatdan o‘tgansiz.") -> None:
        super().__init__(message)


class NotFound(AppError):
    pass


class InfeasiblePairing(AppError):
    def __init__(
        self,
        message: str = (
            "Takroriy raqibsiz to‘liq juftlash topilmadi. "
            "Turnirni erta yakunlash uchun administrator tasdig‘i kerak."
        ),
    ) -> None:
        super().__init__(message)


class RerollLimitReached(AppError):
    def __init__(self, message: str = "Bu raund uchun qayta juftlash limiti tugadi.") -> None:
        super().__init__(message)


class NoAlternativePairing(AppError):
    def __init__(
        self,
        message: str = "Xuddi shu optimalikdagi boshqa juftlash varianti topilmadi.",
    ) -> None:
        super().__init__(message)


class PairingLocked(AppError):
    def __init__(self, message: str = "Juftliklar tasdiqlangan va o‘zgarmaydi.") -> None:
        super().__init__(message)


class ResultLocked(AppError):
    def __init__(self, message: str = "Oldingi raund natijalari qulflangan.") -> None:
        super().__init__(message)


class MinimumNotMet(AppError):
    pass


class ActiveTournamentExists(AppError):
    def __init__(self, message: str = "Tizimda allaqachon faol turnir bor.") -> None:
        super().__init__(message)


class GroupPermissionError(AppError):
    pass


class UnresolvedPrizes(AppError):
    pass


class AlreadyFinalized(AppError):
    def __init__(self, message: str = "Turnir allaqachon yakunlangan.") -> None:
        super().__init__(message)


class MessageNotFoundError(AppError):
    """Raised when a Telegram message was deleted and must be recreated."""


class ResultsIncomplete(AppError):
    def __init__(self, message: str = "Hali barcha o‘yin natijalari tasdiqlanmagan.") -> None:
        super().__init__(message)
