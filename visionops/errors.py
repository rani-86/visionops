"""Error taxonomy used to decide whether a job is retried or failed immediately."""


class PermanentJobError(Exception):
    """The input itself is bad (corrupt, unsupported, empty). Retrying cannot help.

    The worker records the failure under ``failed/`` and deletes the message.
    """

    def __init__(self, reason: str, detail: str = ""):
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason = reason
        self.detail = detail


class ModelIntegrityError(Exception):
    """The downloaded model artifact does not match the checksum in its metadata."""
