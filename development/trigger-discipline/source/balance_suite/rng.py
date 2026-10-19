from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import random
from typing import Any


def derive_seed(master_seed: int, stream_id: str, index: int = 0) -> int:
    material = f"trigger-discipline|{master_seed}|{stream_id}|{index}".encode("utf-8")
    return int.from_bytes(hashlib.sha256(material).digest()[:8], "big", signed=False)


@dataclass(slots=True)
class RandomStream:
    master_seed: int
    stream_id: str
    index: int = 0
    draws: list[dict[str, Any]] = field(default_factory=list)
    _random: random.Random = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._random = random.Random(derive_seed(self.master_seed, self.stream_id, self.index))

    @property
    def position(self) -> int:
        return len(self.draws)

    def d20(self, purpose: str) -> int:
        value = self._random.randint(1, 20)
        self.draws.append({"position": self.position, "kind": "d20", "purpose": purpose, "value": value})
        return value

    def random(self, purpose: str) -> float:
        value = self._random.random()
        self.draws.append({"position": self.position, "kind": "random", "purpose": purpose, "value": value})
        return value

    def choice(self, values: list[Any] | tuple[Any, ...], purpose: str) -> Any:
        chosen_index = self._random.randrange(len(values))
        value = values[chosen_index]
        self.draws.append(
            {"position": self.position, "kind": "choice", "purpose": purpose, "index": chosen_index, "value": value}
        )
        return value

    def drain_draws(self, start: int = 0) -> list[dict[str, Any]]:
        return [dict(draw) for draw in self.draws[start:]]


@dataclass(slots=True)
class RNGManager:
    master_seed: int
    _streams: dict[tuple[str, int], RandomStream] = field(default_factory=dict)

    def stream(self, stream_id: str, index: int = 0) -> RandomStream:
        key = (stream_id, index)
        if key not in self._streams:
            self._streams[key] = RandomStream(self.master_seed, stream_id, index)
        return self._streams[key]

    def provenance(self) -> dict[str, Any]:
        return {
            "master_seed": self.master_seed,
            "derived_streams": [
                {
                    "stream_id": stream_id,
                    "index": index,
                    "derived_seed": derive_seed(self.master_seed, stream_id, index),
                    "draw_count": stream.position,
                }
                for (stream_id, index), stream in sorted(self._streams.items())
            ],
        }

