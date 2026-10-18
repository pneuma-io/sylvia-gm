"""Optional first-reply title metadata; never runs a separate provider request."""

TITLE_LIMIT = 56
TITLE_PREFIX = "<!-- sylvia-chat-title:"
TITLE_INSTRUCTION = (
    "\nFor this new chat, begin your first prose response with one metadata comment: "
    "<!-- sylvia-chat-title: A short descriptive title --> followed by a newline. "
    "Choose a recognizable phrase of about 4–8 words, at most 56 characters, for the user's "
    "request. Then answer the request normally. This comment is hidden chat-list metadata; "
    "do not discuss the naming step or make a separate tool call for it."
)


def opening_title(text: str) -> str:
    """Return an immediate single-line label, shortening long openings between words."""
    normalized = " ".join(text.split())
    if len(normalized) <= TITLE_LIMIT:
        return normalized
    shortened = normalized[:TITLE_LIMIT - 1].rsplit(" ", 1)[0]
    return shortened.rstrip(".,;:") + "…"


class FirstReplyTitle:
    """Strip only the optional leading title comment, including split stream chunks."""

    def __init__(self) -> None:
        self.title: str | None = None
        self._prefix = ""
        self._finished = False
        self._trim = False

    def feed(self, text: str, *, final: bool = False) -> str:
        """Release prose promptly; malformed or absent metadata never triggers a retry."""
        if not text and not self._prefix:
            return ""
        if self._finished:
            if self._trim:
                text = text.lstrip("\r\n ")
                self._trim = not bool(text)
            return text
        self._prefix += text
        candidate = self._prefix.lstrip()
        if candidate.startswith(TITLE_PREFIX):
            end = candidate.find("-->", len(TITLE_PREFIX))
            if end >= 0:
                title = " ".join(candidate[len(TITLE_PREFIX):end].split())
                if 0 < len(title) <= TITLE_LIMIT and not any(c in title for c in "<>"):
                    self.title = title
                self._finished = True
                self._trim = True
                self._prefix = ""
                return self.feed(candidate[end + 3:])
            if not final and len(candidate) <= 256:
                return ""
            # Drop only an unfinished leading metadata line; preserve any following answer.
            _, _, remainder = candidate.partition("\n")
            self._finished = True
            self._prefix = ""
            return remainder
        if not final and len(self._prefix) <= 256 and TITLE_PREFIX.startswith(candidate):
            return ""
        self._finished = True
        result, self._prefix = self._prefix, ""
        return result
