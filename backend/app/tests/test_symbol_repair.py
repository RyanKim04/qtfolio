"""Description → ticker matching for Questrade trade rows that arrive with no
symbol/symbolId — real description strings from the API."""

from app.services.history import _desc_key, _keys_match


def test_issuer_name_extraction_stops_at_double_space():
    assert _desc_key("JPMORGAN CHASE & CO  WE ACTED AS AGENT") == "JPMORGANCHASE"
    assert _desc_key("ADVANCED MICRO DEVICES INC  COM  WE ACTED AS AGENT") == "ADVANCEDMICRODEVICES"
    assert _desc_key("INVESCO QQQ TR  UNIT SER 1  WE ACTED AS AGENT") == "INVESCOQQQ"


def test_punctuation_variants_normalize_identically():
    # API row says AMAZON.COM; Questrade's symbol registry says AMAZON COM.
    assert _keys_match(_desc_key("AMAZON.COM INC  WE ACTED AS AGENT"), _desc_key("AMAZON COM INC"))
    # Row says MCDONALDS; registry says MCDONALD'S.
    assert _keys_match(_desc_key("MCDONALDS CORP  WE ACTED AS AGENT"), _desc_key("MCDONALD'S CORP"))


def test_prefix_matching_across_naming_variants():
    assert _keys_match(
        _desc_key("INVESCO QQQ TR  UNIT SER 1  WE ACTED AS AGENT"),
        _desc_key("INVESCO QQQ TRUST, SERIES 1"),
    )


def test_no_match_on_different_issuers_or_short_keys():
    assert not _keys_match(_desc_key("JPMORGAN CHASE & CO  X"), _desc_key("NVIDIA CORP"))
    assert not _keys_match("", "NVIDIA")
    assert not _keys_match("AMD", "AMD")  # too short to trust prefix logic
