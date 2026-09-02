"""The Textual adapter for the same warm ink-and-gold world as ``vitruvio-site``."""

from __future__ import annotations

from typing import Any

from textual.theme import Theme

from vitruvio.cli import render
from vitruvio.cli.render import brand

NAME = "vitruvio"

VITRUVIO_THEME = Theme(
    name=NAME,
    primary=brand.GOLD,
    secondary=brand.GOLD_DIM,
    accent=brand.GOLD,
    foreground=brand.IVORY,
    background=brand.INK_000,
    surface=brand.INK_050,
    panel=brand.INK_100,
    boost=brand.INK_BOOST,
    success=brand.SUCCESS,
    warning=brand.WARNING,
    error=brand.ERROR,
    dark=True,
    variables={
        "text-muted": brand.IVORY_MUTED,
        "text-disabled": brand.IVORY_MUTED,
        "border": brand.GOLD_DEEP,
        "border-blurred": brand.GOLD_DIM,
    },
)


def install(app: Any) -> None:
    """Register both presentation adapters before a Textual screen renders shared Rich views."""
    app.register_theme(VITRUVIO_THEME)
    app.theme = NAME
    app.console.push_theme(render.THEME)


__all__ = ["NAME", "VITRUVIO_THEME", "install"]
