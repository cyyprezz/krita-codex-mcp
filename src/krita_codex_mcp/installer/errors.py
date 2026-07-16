class InstallerError(RuntimeError):
    """Raised when a safe installation operation cannot be completed."""


class BridgeRequestError(InstallerError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        details: dict[str, object] | None = None,
    ) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.details = details or {}
