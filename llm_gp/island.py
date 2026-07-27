from __future__ import annotations

import random
from dataclasses import dataclass, field

from .models import Individual


@dataclass
class Island:
    """One fixed-size, independently randomized island population."""

    name: str
    random_seed: int
    population: list[Individual] = field(default_factory=list)
    rng: random.Random = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self.rng = random.Random(self.random_seed)
