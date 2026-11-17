"""Pair package dice across setups without changing gameplay state or action identities."""

from __future__ import annotations

import threading
from collections import Counter
from contextlib import ExitStack
from functools import wraps
from typing import Any
from unittest.mock import patch

from sylvia_gm.domain.authority import thaw_json_value

from .contracts import digest

POLICY = "package-tool-occurrence-v1"
_exclusive = threading.Lock()


class PairedDice:
    """Benchmark-only override: same tool occurrence and player turn get the same RNG stream.

    Pairing excludes setup, model call IDs and parameters. Different actions or parameters can
    consume that stream differently; this does not force models to take identical actions.
    The owner is isolated and runs serially while the process-local patches are installed.
    """

    def __init__(self, fixture: str, scenario: str, repetition: int) -> None:
        self.basis = [POLICY, fixture, scenario, repetition]
        self.turn = 0
        self.trace: list[dict[str, Any]] = []
        self.counts: Counter[tuple[Any, ...]] = Counter()
        self.local = threading.local()
        self.stack = ExitStack()

    def __enter__(self) -> PairedDice:
        from sylvia_gm import dice
        from sylvia_gm.application import authority

        if not _exclusive.acquire(blocking=False):
            raise RuntimeError("Paired dice requires one isolated benchmark owner at a time")
        original_seed = dice.set_seed
        original_materialize = authority.materialize_package_mechanic
        rng_state, current_seed = dice._rng.getstate(), dice._current_seed

        def set_seed(seed: int) -> None:
            controlled = getattr(self.local, "seed", None)
            original_seed(seed if controlled is None else controlled)

        def materialize(**kwargs: Any) -> Any:
            mechanic = original_materialize(**kwargs)

            @wraps(mechanic)
            def paired(context: Any) -> Any:
                key = (
                    self.turn,
                    kwargs["package_id"],
                    kwargs["package_version"],
                    kwargs["package_sha256"],
                    kwargs["declaration"].name,
                )
                occurrence = self.counts[key]
                self.counts[key] += 1
                seed = int(digest([*self.basis, *key, occurrence])[:16], 16)
                self.trace.append(
                    {
                        "turn": self.turn,
                        "package": key[1],
                        "tool": key[-1],
                        "occurrence": occurrence,
                        "seed": seed,
                        "parameters_sha256": digest(thaw_json_value(context.parameters)),
                    }
                )
                previous = getattr(self.local, "seed", None)
                self.local.seed = seed
                try:
                    return mechanic(context)
                finally:
                    self.local.seed = previous

            return paired

        def restore() -> None:
            dice._rng.setstate(rng_state)
            dice._current_seed = current_seed
            _exclusive.release()

        self.stack.callback(restore)
        self.stack.enter_context(patch.object(dice, "set_seed", set_seed))
        self.stack.enter_context(
            patch.object(authority, "materialize_package_mechanic", materialize)
        )
        return self

    def __exit__(self, *args: Any) -> None:
        self.stack.__exit__(*args)
