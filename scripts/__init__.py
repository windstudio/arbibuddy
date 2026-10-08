"""ArbiBuddy runtime package bootstrap."""

from .cli_encoding import configure_utf8_stdio

# Every ``python -m scripts.*.cli`` import reaches this package before the
# module parser runs, so both success and failure output start with UTF-8.
configure_utf8_stdio()
