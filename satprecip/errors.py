class SatPrecipError(Exception):
    """Base exception for package errors."""


class ConfigurationError(SatPrecipError):
    """Raised when a project or command configuration is invalid."""


class ProviderError(SatPrecipError):
    """Raised when a remote provider cannot satisfy a request."""


class ValidationError(SatPrecipError):
    """Raised when a downloaded or generated artifact is invalid."""

