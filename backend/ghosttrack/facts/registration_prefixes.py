"""Aircraft nationality marks (registration prefixes) mapped to the State names of Table 9-1.

Nationality marks are assigned under ICAO Annex 7 (Aircraft Nationality and Registration
Marks), drawn from the radio call-sign series the ITU allocates to each State. The marks below
were cross-checked on 2026-11-10 against the list at
https://en.wikipedia.org/wiki/List_of_aircraft_registration_prefixes (facts only, written in
our own form). Marks that list footnotes as "not in the ICAO document" (for example Thailand
"U", Russia "RF", Spain "EM", UAE "DU") are deliberately left out.

Coverage is intentionally partial: the States most likely in Southern California traffic plus a
broad international set. A prefix that is not listed is "unknown", and GT-006 then does not
evaluate the aircraft (it never guesses).

The marks are written with a hyphen in registrations ("D-ALFB", "XA-VLE", "B-2422"), except
the US ("N12345"), Japan ("JA822A") and the Republic of Korea ("HL7xxx"), which are handled by
pattern in ``ghosttrack.icao``.
"""
from __future__ import annotations

# Prefix before the hyphen -> State names (Table 9-1 spelling). A tuple with more than one State
# means the mark is shared or ambiguous; GT-006 only flags when the address State is in none.
HYPHEN_PREFIXES: dict[str, tuple[str, ...]] = {
    # North and Central America, Caribbean
    "C": ("Canada",),
    "CF": ("Canada",),
    "XA": ("Mexico",),
    "XB": ("Mexico",),
    "XC": ("Mexico",),
    "HP": ("Panama",),
    "TI": ("Costa Rica",),
    "YS": ("El Salvador",),
    "TG": ("Guatemala",),
    # South America
    "PP": ("Brazil",),
    "PR": ("Brazil",),
    "PS": ("Brazil",),
    "PT": ("Brazil",),
    "PU": ("Brazil",),
    "LV": ("Argentina",),
    "LQ": ("Argentina",),
    "CC": ("Chile",),
    "HK": ("Colombia",),
    "HJ": ("Colombia",),
    "OB": ("Peru",),
    # Europe
    "G": ("United Kingdom",),
    "D": ("Germany",),
    "F": ("France",),
    "I": ("Italy",),
    "EC": ("Spain",),
    "CS": ("Portugal",),
    "EI": ("Ireland",),
    "EJ": ("Ireland",),
    "PH": ("Netherlands, Kingdom of the",),
    "OO": ("Belgium",),
    "HB": ("Switzerland",),  # also used by Liechtenstein, which has no Table 9-1 block
    "OE": ("Austria",),
    "OY": ("Denmark",),
    "SE": ("Sweden",),
    "LN": ("Norway",),
    "OH": ("Finland",),
    "TF": ("Iceland",),
    "SP": ("Poland",),
    "LX": ("Luxembourg",),
    "RA": ("Russian Federation",),
    "TC": ("Turkey",),
    # Middle East and Africa
    "A6": ("United Arab Emirates",),
    "A7": ("Qatar",),
    "HZ": ("Saudi Arabia",),
    "4X": ("Israel",),
    "ET": ("Ethiopia",),
    "SU": ("Egypt",),
    # Asia and Oceania. "B" is used by China (including Hong Kong and Macau) and by Taiwan,
    # which has no Table 9-1 block; an address outside every block stays unknown.
    "B": ("China",),
    "VH": ("Australia",),
    "ZK": ("New Zealand",),
    "9V": ("Singapore",),
    "RP": ("Philippines",),
    "HS": ("Thailand",),
    "VN": ("Viet Nam",),
    "9M": ("Malaysia",),
    "PK": ("Indonesia",),
    "VT": ("India",),
    "DQ": ("Fiji",),
}

# Registrations written without a hyphen: regex -> State names.
UNHYPHENATED: tuple[tuple[str, tuple[str, ...]], ...] = (
    (r"^JA[0-9]{4}$|^JA[0-9]{3}[A-Z]$|^JA[0-9]{2}[A-Z]{2}$", ("Japan",)),
    (r"^HL[0-9]{4}$", ("Republic of Korea",)),
)
