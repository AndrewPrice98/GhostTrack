"""ICAO 24-bit address facts: State allocation blocks and the US N-number mapping.

N-number mapping (our own derivation, see DECISIONS.md, "US N-number to ICAO address mapping"). No public description of the
algorithm was found; this implementation was derived from the structure of US registrations and
validated against published registration/address pairs (tests/test_icao.py).

US civil registrations are ``N`` + a first digit 1-9 + up to four more characters (five in
total after the ``N``). Letters exclude I and O (24 letters). At most two letters, only at the
end: up to two after 1-3 digits, one after 4 digits, none after 5 digits. The US block assigns
addresses A00001 (N1) to ADF7C7 (N99999) in depth-first order: a number itself, then its letter
suffixes (A, AA..AZ, B, BA..BZ, ...), then its children with the next digit 0-9.

Block sizes (addresses taken by a prefix and everything under it):
    suffix block after 1-3 digits: 24 * (1 + 24) = 600; after 4 digits: 24
    5-digit node: 1
    4-digit node: 1 + 24 + 10 * 1     = 35
    3-digit node: 1 + 600 + 10 * 35    = 951
    2-digit node: 1 + 600 + 10 * 951   = 10,111
    1-digit node: 1 + 600 + 10 * 10111 = 101,711
Nine first digits * 101,711 = 915,399 addresses: A00001 + 915,398 = ADF7C7.
"""
from __future__ import annotations

import bisect
import re
from dataclasses import dataclass
from functools import lru_cache

from ghosttrack.facts.icao_table9_1 import ICAO_SPECIAL, TABLE_9_1
from ghosttrack.facts.registration_prefixes import HYPHEN_PREFIXES, UNHYPHENATED

LETTERS = "ABCDEFGHJKLMNPQRSTUVWXYZ"  # no I, no O
US_FIRST = 0xA00001
US_LAST = 0xADF7C7

# _NODE[k] = addresses under a node of k digits (k = 1..5); _SUFFIX[k] = its letter-suffix block.
_SUFFIX = {1: 600, 2: 600, 3: 600, 4: 24, 5: 0}
_NODE = {5: 1}
for _k in (4, 3, 2, 1):
    _NODE[_k] = 1 + _SUFFIX[_k] + 10 * _NODE[_k + 1]

_NNUMBER_RE = re.compile(r"^N[1-9](?:[0-9]{0,4}|[0-9]{0,3}[A-HJ-NP-Z]|[0-9]{0,2}[A-HJ-NP-Z]{2})$")


def is_valid_nnumber(reg: str) -> bool:
    """True for a syntactically valid US civil registration (upper case, with the N)."""
    if not isinstance(reg, str) or not _NNUMBER_RE.match(reg):
        return False
    body = reg[1:]
    digits = len(body.rstrip(LETTERS))
    letters = len(body) - digits
    # Letters only at the end, max 2 after <=3 digits, max 1 after 4 digits, none after 5.
    return body[:digits].isdigit() and (letters == 0 or (digits <= 3 and letters <= 2) or (digits == 4 and letters == 1))


def nnumber_to_icao(reg: str) -> str | None:
    """'N12345' -> 'a061d9'. Returns None for anything that is not a valid N-number."""
    reg = reg.strip().upper() if isinstance(reg, str) else ""
    if not is_valid_nnumber(reg):
        return None
    body = reg[1:]
    offset = (int(body[0]) - 1) * _NODE[1]
    depth = 1
    i = 1
    while i < len(body):
        c = body[i]
        if c.isdigit():
            offset += 1 + _SUFFIX[depth] + int(c) * _NODE[depth + 1]
            depth += 1
            i += 1
            continue
        # Letter suffix: skip the node itself, then index into the suffix block.
        li = LETTERS.index(c)
        if depth == 4:
            offset += 1 + li
        else:
            offset += 1 + li * 25
            if i + 1 < len(body):
                offset += 1 + LETTERS.index(body[i + 1])
        break
    return f"{US_FIRST + offset:06x}"


def icao_to_nnumber(hex_: str) -> str | None:
    """'a061d9' -> 'N12345'. Returns None outside A00001-ADF7C7."""
    try:
        addr = int(hex_, 16)
    except (TypeError, ValueError):
        return None
    if not US_FIRST <= addr <= US_LAST:
        return None
    rem = addr - US_FIRST
    first, rem = divmod(rem, _NODE[1])
    out = "N" + str(first + 1)
    depth = 1
    while rem:
        rem -= 1  # skip the node itself
        if rem < _SUFFIX[depth]:
            if depth == 4:
                return out + LETTERS[rem]
            li, r2 = divmod(rem, 25)
            out += LETTERS[li]
            if r2:
                out += LETTERS[r2 - 1]
            return out
        rem -= _SUFFIX[depth]
        digit, rem = divmod(rem, _NODE[depth + 1])
        out += str(digit)
        depth += 1
    return out


# ---------------------------------------------------------------- allocation blocks


@dataclass(frozen=True)
class Block:
    start: int
    end: int
    state: str
    size: int
    special: bool = False


def _pattern_block(state: str, size: int, pattern: str, special: bool = False) -> Block:
    bits = pattern.replace(" ", "")
    if set(bits) - {"0", "1"}:
        raise ValueError(f"bad pattern for {state}: {pattern!r}")
    free = 24 - len(bits)
    if 1 << free != size:
        raise ValueError(f"pattern length does not match block size for {state}")
    start = int(bits, 2) << free
    return Block(start, start + size - 1, state, size, special)


@lru_cache(maxsize=1)
def allocation_blocks() -> tuple[Block, ...]:
    blocks = [_pattern_block(s, n, p) for s, n, p in TABLE_9_1]
    blocks += [_pattern_block(s, n, p, special=True) for s, n, p in ICAO_SPECIAL]
    return tuple(sorted(blocks, key=lambda b: (b.start, -b.size)))


@lru_cache(maxsize=1)
def _starts() -> list[int]:
    return [b.start for b in allocation_blocks()]


def block_for_address(hex_: str) -> Block | None:
    """The Table 9-1 block containing the address, or None (blocks never overlap; tested)."""
    try:
        addr = int(hex_, 16)
    except (TypeError, ValueError):
        return None
    if not 0 < addr <= 0xFFFFFF:
        return None
    i = bisect.bisect_right(_starts(), addr) - 1
    if i < 0:
        return None
    b = allocation_blocks()[i]
    return b if b.start <= addr <= b.end else None


def state_for_address(hex_: str) -> str | None:
    b = block_for_address(hex_)
    return None if b is None or b.special else b.state


_UNHYPH = tuple((re.compile(p), s) for p, s in UNHYPHENATED)


def states_for_registration(reg: str | None) -> tuple[str, ...] | None:
    """States a registration's nationality mark points to, or None when unknown."""
    if not isinstance(reg, str):
        return None
    reg = reg.strip().upper()
    if not reg:
        return None
    if is_valid_nnumber(reg):
        return ("United States",)
    if "-" in reg:
        prefix = reg.split("-", 1)[0]
        return HYPHEN_PREFIXES.get(prefix)
    for rx, states in _UNHYPH:
        if rx.match(reg):
            return states
    return None
