"""Path guardrail for Builder file operations.

Enforces that all file read/write/search operations stay within
an allowed root directory. Write operations additionally check
for denied patterns (.env*, .key, .pem).
"""

from __future__ import annotations

from pathlib import Path


class GuardrailBlockedError(Exception):
    """Raised when a path is blocked by the guardrail."""

    def __init__(
        self,
        raw_path: str,
        resolved: Path,
        allowed_root: Path,
        reason: str = "",
    ) -> None:
        self.raw_path = raw_path
        self.resolved = resolved
        self.allowed_root = allowed_root
        self.reason = reason
        super().__init__(str(self))

    def __str__(self) -> str:
        msg = (
            f"Access denied: '{self.raw_path}' resolves to '{self.resolved}', "
            f"which is outside the project directory '{self.allowed_root}'. "
            f"The Builder agent can only access files within the Sylvia GM project."
        )
        if self.reason:
            msg += f" Reason: {self.reason}"
        return msg


class PathGuardrail:
    """Validates file paths are within an allowed root directory."""

    # File patterns blocked for write access even within allowed_root
    WRITE_DENIED_PATTERNS = [".env", ".key", ".pem"]
    READ_ONLY_ROOTS = frozenset({".builder-sources"})
    INTERNAL_ROOTS = frozenset({".builder-sources-purge-pending"})

    def __init__(self, allowed_root: Path) -> None:
        self._allowed_root = Path(allowed_root).resolve()

    @property
    def allowed_root(self) -> Path:
        return self._allowed_root

    def validate(self, raw_path: str, for_write: bool = False) -> Path:
        """Validate a path. Returns resolved Path on success.

        Raises GuardrailBlockedError if the path is denied.
        """
        try:
            # Resolve relative to project root if not absolute
            p = Path(raw_path)
            if not p.is_absolute():
                p = self._allowed_root / p

            resolved = p.resolve()

            # Check: is resolved under allowed_root?
            try:
                relative = resolved.relative_to(self._allowed_root)
            except ValueError as error:
                raise GuardrailBlockedError(raw_path, resolved, self._allowed_root) from error

            top_level = relative.parts[0].casefold() if relative.parts else ""
            if top_level in self.INTERNAL_ROOTS:
                raise GuardrailBlockedError(
                    raw_path,
                    resolved,
                    self._allowed_root,
                    reason="This owner-managed lifecycle path is not available to Builder tools.",
                )
            if for_write and top_level in self.READ_ONLY_ROOTS:
                raise GuardrailBlockedError(
                    raw_path,
                    resolved,
                    self._allowed_root,
                    reason="Attached Builder sources are read-only.",
                )

            # Denied-patterns check for write operations (defense-in-depth)
            if for_write:
                fname = resolved.name
                for denied in self.WRITE_DENIED_PATTERNS:
                    # Match .env, .env.local, .env.production, etc.
                    if fname == denied or fname.startswith(denied) or fname.endswith(denied):
                        raise GuardrailBlockedError(
                            raw_path,
                            resolved,
                            self._allowed_root,
                            reason=f"Write access to '{fname}' files is blocked for security.",
                        )
                # Block .key and .pem files
                if resolved.suffix in (".key", ".pem"):
                    raise GuardrailBlockedError(
                        raw_path,
                        resolved,
                        self._allowed_root,
                        reason=(
                            f"Write access to '{resolved.suffix}' files is blocked for security."
                        ),
                    )

            return resolved

        except GuardrailBlockedError:
            raise
        except Exception as error:
            raise GuardrailBlockedError(
                raw_path,
                Path(raw_path),
                self._allowed_root,
                reason=str(error),
            ) from error
