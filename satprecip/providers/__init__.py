from __future__ import annotations

from typing import Type

from ..base import BaseDownloader
from ..errors import ConfigurationError


def provider_class(name: str) -> Type[BaseDownloader]:
    key = name.lower()
    if key == "goes":
        from .goes import GoesDownloader

        return GoesDownloader
    if key == "glm":
        from .glm import GlmDownloader

        return GlmDownloader
    if key == "imerg":
        from .imerg import ImergDownloader

        return ImergDownloader
    if key == "cmorph":
        from .cmorph import CmorphDownloader

        return CmorphDownloader
    if key == "gsmap":
        from .gsmap import GsmapDownloader

        return GsmapDownloader
    raise ConfigurationError(f"Unknown provider: {name}")


__all__ = ["provider_class"]
