from src.stages.analyse import classify_token, tokenize_path


def test_classify_6digit_date():
    pc, st = classify_token("160929")
    assert pc == "6digit_numeric"
    assert st == "date"


def test_classify_6digit_time():
    pc, st = classify_token("094814")
    assert pc == "6digit_numeric"
    assert st == "time"


def test_classify_6digit_ambiguous():
    # 999999: month=99 (invalid) and hour=99 (invalid) → unclassified
    pc, st = classify_token("999999")
    assert pc == "6digit_numeric"
    assert st == "unclassified"


def test_classify_8digit_date():
    pc, st = classify_token("20160929")
    assert pc == "8digit_numeric"
    assert st == "date"


def test_classify_sequential():
    pc, st = classify_token("001")
    assert pc == "sequential"
    assert st == "sequential"


def test_classify_camelcase():
    pc, st = classify_token("TuckInleted")
    assert pc == "camelcase"
    assert st == "compound"


def test_classify_route_code():
    pc, st = classify_token("BC-5")
    assert pc == "route_code"
    assert st == "code"


def test_tokenize_path_splits_delimiters():
    tokens = tokenize_path("BC-Hwy_97C")
    # Should split CamelCase 97C → 97, c? Actually "97C" is alphanumeric, no interior uppercase
    # "BC-Hwy_97C": BC, Hwy, 97C → lower: bc, hwy, 97c
    assert "bc" in tokens
    assert "hwy" in tokens
