import math

import pytest

from ghosttrack import geo, icao
from ghosttrack.facts.icao_table9_1 import TABLE_9_1

# Source A: the 9 pairs published in the README of guillaumemichel/icao-nnumber_converter.
README_PAIRS = [
    ("N1", "a00001"), ("N1A", "a00002"), ("N10", "a0025a"), ("N1000", "a0070c"), ("N2", "a18d50"),
    ("N9", "ac6a79"), ("N99999", "adf7c7"), ("N12345", "a061d9"), ("N86QU", "abcdef"),
]
# Source B: registration/address pairs from the adsb.lol sample of 2026-11-10 (DECISIONS.md, "US N-number to ICAO address mapping").
RECORDED_PAIRS = [
    ("N738TJ", "a9ea75"), ("N383HA", "a4671f"), ("N8806Q", "ac210e"), ("N523DE", "a69463"),
    ("N8738K", "ac04f5"), ("N8779Q", "ac13f9"), ("N652FR", "a894b5"), ("N664FR", "a8c3a2"),
    ("N893XB", "ac5288"), ("N438AN", "a540e3"), ("N415AN", "a4e6c0"), ("N990AU", "add2b6"),
    ("N191UW", "a16db9"), ("N467AL", "a5b3a7"), ("N574DZ", "a75da8"), ("N983JT", "adb723"),
    ("N987JT", "adc5ff"), ("N951WN", "ad3a5f"), ("N8650F", "abe3c8"), ("N824NW", "ab40f8"),
    ("N400XB", "a4aeb0"), ("N916US", "acaec8"), ("N306XR", "a337b9"), ("N519DN", "a68336"),
    ("N73259", "a9d5a2"), ("N542FX", "a6dfed"), ("N223LA", "a1ed21"), ("N420MP", "a4fcc0"),
    ("N956LA", "ad4bec"),
]


@pytest.mark.parametrize("reg,hex_", README_PAIRS + RECORDED_PAIRS)
def test_nnumber_known_pairs(reg, hex_):
    assert icao.nnumber_to_icao(reg) == hex_
    assert icao.icao_to_nnumber(hex_) == reg


def h(reg):
    return int(icao.nnumber_to_icao(reg), 16)


def test_nnumber_edge_cases_follow_from_published_pairs():
    # Suffixes come right after the number: N1 (a00001), N1A (a00002), N1AA next.
    assert h("N1AA") == h("N1A") + 1
    assert h("N1B") == h("N1A") + 25  # A, then AA..AZ (24), then B
    # The last suffix of N1 (N1ZZ) is immediately followed by N10 (published: a0025a).
    assert h("N1ZZ") + 1 == h("N10") == 0xA0025A
    assert h("N10AA") == h("N10") + 2
    # A 4-digit number has single-letter suffixes only, then its 5-digit children.
    assert h("N1000A") == h("N1000") + 1
    assert h("N1000Z") == h("N1000") + 24
    assert h("N10000") == h("N1000Z") + 1
    # N9999Z is followed by N99990..N99999, and N99999 is the published end of the block.
    assert h("N9999Z") + 10 == h("N99999") == 0xADF7C7
    # N9ZZ is the last suffix of N9, immediately before N90; N99 sits under N9.
    assert h("N9ZZ") + 1 == h("N90")
    assert h("N9") < h("N99") < h("N99999")
    assert icao.icao_to_nnumber(icao.nnumber_to_icao("N99")) == "N99"


def test_nnumber_full_block_is_a_bijection():
    seen = 0
    for addr in range(icao.US_FIRST, icao.US_LAST + 1, 7):
        n = icao.icao_to_nnumber(f"{addr:06x}")
        assert icao.is_valid_nnumber(n)
        assert icao.nnumber_to_icao(n) == f"{addr:06x}"
        seen += 1
    assert seen > 100_000
    assert icao.icao_to_nnumber(f"{icao.US_LAST:06x}") == "N99999"


@pytest.mark.parametrize("bad", ["N0", "N01", "N1I", "N1O", "N123456", "N12345A", "N1AAA", "N1A1",
                                 "N1234AB", "", None, "D-ALFB", "N", "n 1"])
def test_invalid_nnumbers(bad):
    assert icao.nnumber_to_icao(bad) is None


@pytest.mark.parametrize("hex_", ["a00000", "adf7c8", "ae1234", "3c70c2", "zzzzzz", ""])
def test_icao_outside_us_nnumber_block(hex_):
    assert icao.icao_to_nnumber(hex_) is None


def test_table_9_1_blocks_are_consistent():
    blocks = icao.allocation_blocks()
    assert len(blocks) == len(TABLE_9_1) + 3
    for a, b in zip(blocks, blocks[1:]):
        assert a.end < b.start, (a, b)
    for b in blocks:
        assert b.end - b.start + 1 == b.size
        assert b.start % b.size == 0


@pytest.mark.parametrize("state,lo,hi", [
    ("United States", 0xA00000, 0xAFFFFF), ("Canada", 0xC00000, 0xC3FFFF),
    ("Germany", 0x3C0000, 0x3FFFFF), ("France", 0x380000, 0x3BFFFF), ("Japan", 0x840000, 0x87FFFF),
    ("Australia", 0x7C0000, 0x7FFFFF), ("Mexico", 0x0D0000, 0x0D7FFF), ("United Kingdom", 0x400000, 0x43FFFF),
])
def test_table_9_1_cross_check(state, lo, hi):
    b = icao.block_for_address(f"{lo:06x}")
    assert (b.state, b.start, b.end) == (state, lo, hi)
    assert icao.state_for_address(f"{hi:06x}") == state


# Address / registration pairs seen in the committed recordings.
@pytest.mark.parametrize("hex_,reg,state", [
    ("06a1c9", "A7-CGD", "Qatar"), ("0c20f5", "HP-9909CMP", "Panama"), ("0d0866", "XA-VLE", "Mexico"),
    ("3c458d", "D-AALM", "Germany"), ("408143", "G-DHMD", "United Kingdom"), ("7802fd", "B-2422", "China"),
    ("86d5b6", "JA822A", "Japan"), ("a16db9", "N191UW", "United States"),
])
def test_recorded_addresses_match_registration_state(hex_, reg, state):
    assert icao.state_for_address(hex_) == state
    assert state in icao.states_for_registration(reg)


def test_unknowns_stay_unknown():
    assert icao.state_for_address("899100") is None  # used by Taiwan; ICAO-administered block
    assert icao.block_for_address("899100").special
    assert icao.block_for_address("c40000") is None  # unallocated
    assert icao.state_for_address("f00010") is None  # ICAO temporary block: special, no State
    assert icao.block_for_address("f00010").special
    assert icao.state_for_address("000000") is None
    assert icao.states_for_registration("ZZ-ABC") is None
    assert icao.states_for_registration("JA8") is None
    assert icao.states_for_registration(None) is None


def test_haversine_and_bearing():
    assert geo.haversine_nm(0, 0, 1, 0) == pytest.approx(60.04, abs=0.01)
    # Antimeridian: 0.2 degrees of longitude at the equator, not 359.8.
    assert geo.haversine_nm(0, 179.9, 0, -179.9) == pytest.approx(12.0, abs=0.1)
    # Across the pole.
    assert geo.haversine_nm(89.9, 0, 89.9, 180) == pytest.approx(12.0, abs=0.1)
    assert geo.bearing_deg(0, 0, 1, 0) == pytest.approx(0.0)
    assert geo.bearing_deg(0, 0, 0, 1) == pytest.approx(90.0)
    assert geo.bearing_deg(0, 179.9, 0, -179.9) == pytest.approx(90.0)
    assert geo.angle_diff_deg(350, 10) == pytest.approx(20)
    assert geo.angle_diff_deg(10, 350) == pytest.approx(-20)
    lat, lon = geo.destination(0, 179.95, 90, 6.0)
    assert lon < 0 and geo.haversine_nm(0, 179.95, lat, lon) == pytest.approx(6.0, abs=1e-6)
    assert not math.isnan(geo.haversine_nm(10, 10, 10, 10))
