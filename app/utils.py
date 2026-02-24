"""Utility functions — emission designator parsing, etc."""

import re
from functools import lru_cache

# Emission designator format: BBBBMST
# BBBB = Bandwidth (e.g., 16K0, 6K00, 200K)
# M = Modulation type
# S = Signal type
# T = Information type

MODULATION_TYPES = {
    "N": "Unmodulated carrier",
    "A": "Double sideband AM",
    "H": "Single sideband, full carrier",
    "R": "Single sideband, reduced carrier",
    "J": "Single sideband, suppressed carrier",
    "B": "Independent sideband",
    "C": "Vestigial sideband",
    "F": "Frequency modulation",
    "G": "Phase modulation",
    "D": "AM + FM/PM simultaneously",
    "P": "Unmodulated pulse sequence",
    "K": "Pulse AM",
    "L": "Pulse width modulation",
    "M": "Pulse position modulation",
    "Q": "Pulse, phase/frequency modulation during pulse",
    "V": "Combination of pulse modulation",
    "W": "Combination of modulation, none dominant",
    "X": "Other",
}

SIGNAL_TYPES = {
    "0": "No modulating signal",
    "1": "Single channel, digital, no subcarrier",
    "2": "Single channel, digital, with subcarrier",
    "3": "Single channel, analog",
    "7": "Two or more channels, digital",
    "8": "Two or more channels, analog",
    "9": "Composite (analog + digital)",
    "X": "Other",
}

INFORMATION_TYPES = {
    "N": "No information",
    "A": "Aural telegraphy (Morse)",
    "B": "Electronic telegraphy",
    "C": "Facsimile",
    "D": "Data, telemetry, telecommand",
    "E": "Telephony (voice)",
    "F": "Television (video)",
    "W": "Combination",
    "X": "Other",
}


@lru_cache(maxsize=8192)
def parse_emission_designator(emission: str) -> dict:
    """Parse an emission designator string into components.

    Example: '16K0F3E' → bandwidth=16kHz, modulation=FM, signal=analog, info=telephony

    Returns a dict with keys: bandwidth, modulation, signal_type, info_type, raw
    """
    if not emission or not emission.strip():
        return {}

    emission = emission.strip().upper()

    result = {
        "raw": emission,
        "bandwidth": None,
        "modulation": None,
        "signal_type": None,
        "info_type": None,
    }

    # Match: bandwidth (up to last letter before MST), then M, S, T
    # Standard format: digits/letters for bandwidth, then 3 classification chars
    # e.g. 16K0F3E, 6K00F3E, 200KF3E, 11K0F3E
    match = re.match(
        r"^(\d+[HKMGT]\d*)\s*([A-Z])\s*([0-9X])\s*([A-Z])$",
        emission,
    )
    if not match:
        # Simpler match: just extract last 3 characters as classification if length >= 4
        if len(emission) >= 4:
            bw = emission[:-3]
            mod_char = emission[-3]
            sig_char = emission[-2]
            info_char = emission[-1]
            result["bandwidth"] = _parse_bandwidth(bw)
            result["modulation"] = MODULATION_TYPES.get(mod_char, mod_char)
            result["signal_type"] = SIGNAL_TYPES.get(sig_char, sig_char)
            result["info_type"] = INFORMATION_TYPES.get(info_char, info_char)
        return result

    bw_str, mod_char, sig_char, info_char = match.groups()

    result["bandwidth"] = _parse_bandwidth(bw_str)
    result["modulation"] = MODULATION_TYPES.get(mod_char, mod_char)
    result["signal_type"] = SIGNAL_TYPES.get(sig_char, sig_char)
    result["info_type"] = INFORMATION_TYPES.get(info_char, info_char)

    return result


def _parse_bandwidth(bw_str: str) -> str:
    """Parse bandwidth string like '16K0' into human-readable format."""
    if not bw_str:
        return ""

    # Multipliers: H=Hz, K=kHz, M=MHz, G=GHz
    multipliers = {"H": "Hz", "K": "kHz", "M": "MHz", "G": "GHz", "T": "THz"}

    for code, unit in multipliers.items():
        if code in bw_str:
            parts = bw_str.split(code)
            try:
                whole = parts[0] if parts[0] else "0"
                frac = parts[1] if len(parts) > 1 and parts[1] else "0"
                val = f"{whole}.{frac}".rstrip("0").rstrip(".")
                return f"{val} {unit}"
            except (ValueError, IndexError):
                return bw_str

    return bw_str


# US State FIPS codes mapping
US_STATES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas",
    "CA": "California", "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware",
    "FL": "Florida", "GA": "Georgia", "HI": "Hawaii", "ID": "Idaho",
    "IL": "Illinois", "IN": "Indiana", "IA": "Iowa", "KS": "Kansas",
    "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine", "MD": "Maryland",
    "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota", "MS": "Mississippi",
    "MO": "Missouri", "MT": "Montana", "NE": "Nebraska", "NV": "Nevada",
    "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York",
    "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma",
    "OR": "Oregon", "PA": "Pennsylvania", "RI": "Rhode Island", "SC": "South Carolina",
    "SD": "South Dakota", "TN": "Tennessee", "TX": "Texas", "UT": "Utah",
    "VT": "Vermont", "VA": "Virginia", "WA": "Washington", "WV": "West Virginia",
    "WI": "Wisconsin", "WY": "Wyoming", "DC": "District of Columbia",
    "PR": "Puerto Rico", "VI": "Virgin Islands", "GU": "Guam",
    "AS": "American Samoa", "MP": "Northern Mariana Islands",
}
