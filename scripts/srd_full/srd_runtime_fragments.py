"""Locate package tool fragments once while preserving all surrounding source bytes."""

import re

if __package__:
    from .srd_generator_yaml import seed_generator_tool_fragments
else:
    from srd_generator_yaml import seed_generator_tool_fragments


class _ToolFragments:
    """Collect ordered replacements using the two established header policies."""

    def __init__(self, text, *, any_suffix=False):
        self._text = text
        self._blocks = {}
        self._replacements = []
        self._insertions = []
        self._insertion = text.find("procedures: {}\n")
        self._d20_alias_count = text.count("*d20_result_card")
        boundaries = [
            match.start()
            for match in re.finditer(
                r"(?m)^  [A-Za-z_][^\n]*:|^procedures:|^definition_row_schemas:", text
            )
        ]
        for start, end in zip(boundaries, boundaries[1:], strict=False):
            if not text.startswith("  ", start):
                continue
            newline = text.find("\n", start, end)
            if newline < 0:
                continue
            name, _, suffix = text[start + 2 : newline].partition(":")
            if any_suffix or suffix == "" or (suffix.startswith(" &") and len(suffix) > 2):
                self._blocks.setdefault(name, (start, end))

    @property
    def has_d20_alias(self):
        return self._d20_alias_count > 0

    def prior(self, name):
        bounds = self._blocks.get(name)
        return self._text[slice(*bounds)] if bounds is not None else ""

    def replace(self, name, fragment, *, insertion_separator=""):
        bounds = self._blocks.get(name)
        if bounds is not None:
            self._replacements.append((*bounds, fragment))
            self._d20_alias_count += fragment.count("*d20_result_card") - self.prior(name).count(
                "*d20_result_card"
            )
        elif self._insertion >= 0:
            inserted = fragment + insertion_separator
            self._insertions.append(inserted)
            self._d20_alias_count += inserted.count("*d20_result_card")

    def render(self):
        replacements = self._replacements[:]
        if self._insertions:
            replacements.append((self._insertion, self._insertion, "".join(self._insertions)))
        pieces = []
        cursor = 0
        for start, end, fragment in sorted(replacements):
            pieces.extend((self._text[cursor:start], fragment))
            cursor = end
        pieces.append(self._text[cursor:])
        rendered = "".join(pieces)
        seed_generator_tool_fragments(self, rendered)
        return rendered
