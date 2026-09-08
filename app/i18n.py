"""
Idioma del analisis en curso (ES/EN), compartido por todo el backend via
contextvar. Se fija en main._run_job segun lo que pidio el cliente, y lo leen
analyzer, geo_ai y report_pdf para producir textos en el idioma correcto.
"""

from __future__ import annotations

import contextvars

_lang: "contextvars.ContextVar[str]" = contextvars.ContextVar("cl_lang", default="es")


def set_lang(lang: str) -> None:
    _lang.set("en" if str(lang or "es").strip().lower().startswith("en") else "es")


def get_lang() -> str:
    return _lang.get()


def is_en() -> bool:
    return _lang.get() == "en"


def L(es: str, en: str) -> str:
    """Devuelve el texto en el idioma activo."""
    return en if _lang.get() == "en" else es
