"""Finds the category a quick entry refers to: exact, fuzzy, ambiguous or unknown."""
import difflib
from dataclasses import dataclass, field
from typing import List, Optional, Sequence

FUZZY_CUTOFF = 0.75
MIN_PREFIX = 3
MAX_CATEGORY_WORDS = 3
MAX_CANDIDATES = 4


def _key(text: str) -> str:
    return text.lower().replace('ё', 'е')


@dataclass
class Match:
    kind: str  # 'exact' | 'fuzzy' | 'ambiguous' | 'none' | 'empty'
    categories: List = field(default_factory=list)
    rest: List[str] = field(default_factory=list)
    typed: Optional[str] = None  # what the user wrote for the category

    @property
    def category(self):
        return self.categories[0] if self.categories else None


def match_category(words: Sequence[str], categories: Sequence) -> Match:
    """`categories` are objects with `.name`. The longest exact prefix of words wins."""
    if not words:
        return Match(kind='empty')

    by_key = {_key(c.name): c for c in categories}

    for size in range(min(len(words), MAX_CATEGORY_WORDS), 0, -1):
        typed = ' '.join(words[:size])
        if _key(typed) in by_key:
            return Match(kind='exact', categories=[by_key[_key(typed)]], rest=list(words[size:]), typed=typed)

    found: List = []
    fuzzy_size = 1
    for size in range(min(len(words), MAX_CATEGORY_WORDS), 0, -1):
        typed = _key(' '.join(words[:size]))
        names = difflib.get_close_matches(typed, by_key.keys(), n=MAX_CANDIDATES, cutoff=FUZZY_CUTOFF)
        if len(typed) >= MIN_PREFIX:
            names += [name for name in by_key if name.startswith(typed) and name not in names]
        if names:
            found = [by_key[name] for name in names[:MAX_CANDIDATES]]
            fuzzy_size = size
            break

    rest = list(words[fuzzy_size:])
    typed = ' '.join(words[:fuzzy_size])
    if len(found) == 1:
        return Match(kind='fuzzy', categories=found, rest=rest, typed=typed)
    if found:
        return Match(kind='ambiguous', categories=found, rest=rest, typed=typed)
    return Match(kind='none', rest=list(words[1:]), typed=words[0])
