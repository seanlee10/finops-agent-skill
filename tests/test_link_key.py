from finops.linkkey import integration_input


def test_integration_input_shape():
    i = integration_input("A1", "S1", "finops-readonly", "secret")
    assert i["provider"] == "arize" and i["accountId"] == "A1" and i["spaceId"] == "S1"
    assert i["env"] == [{"envVar": "ARIZE_API_KEY", "value": "secret"}]
