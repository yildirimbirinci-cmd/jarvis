"""Jarvis Constitution katmanina ait hata turleri."""


class ConstitutionError(RuntimeError):
    """Constitution altyapisindaki tum hatalarin temel sinifi."""


class ConstitutionFileNotFoundError(ConstitutionError):
    """Zorunlu Constitution dosyasi bulunamadiginda olusur."""


class ConstitutionLoadError(ConstitutionError):
    """Constitution JSON verisi okunamadiginda olusur."""


class ConstitutionValidationError(ConstitutionError):
    """Constitution semasi veya icerigi gecersiz oldugunda olusur."""


class ConstitutionNotLoadedError(ConstitutionError):
    """Registry yuklenmeden once erisilmeye calisildiginda olusur."""


class ConstitutionRuleNotFoundError(ConstitutionError, KeyError):
    """Istenen kural veya bolum bulunamadiginda olusur."""
