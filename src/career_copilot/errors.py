"""Expected failures. The CLI prints these without a traceback."""


class CareerCopilotError(Exception):
    """A failure the user can correct."""


class ParseError(CareerCopilotError):
    """The file could not be read as a supported document."""


class UnsupportedLayoutError(ParseError):
    """The document is not a single text column."""


class ProfileError(CareerCopilotError):
    """A profile command could not be completed."""
