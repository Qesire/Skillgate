from src.parser import parse_escaped


def test_parser_escape():
    assert parse_escaped("a\\nb") == "a\nb"
