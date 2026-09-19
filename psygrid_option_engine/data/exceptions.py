"""Exception hierarchy for the upstream data layer.

`fetch_endpoint`/`fetch_snapshot` in client.py deliberately do NOT raise
these for ordinary transport/HTTP failures — those are captured into
`EndpointFetchResult.error` so one bad endpoint can't crash a snapshot
fetch (section 5 of the brief). These exceptions are for programmer
errors (unregistered endpoint, malformed config) that should fail loud.
"""


class PsygridError(Exception):
    """Base class for all engine-raised (not upstream-caused) errors."""


class UnregisteredEndpointError(PsygridError):
    """Raised when code references a logical endpoint name that was never
    registered in data/endpoints.py."""


class ConfigurationError(PsygridError):
    """Raised for invalid engine configuration."""
