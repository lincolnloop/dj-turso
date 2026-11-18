import importlib.metadata


def test_distribution_metadata_declares_name_and_serverless_extra() -> None:
    metadata = importlib.metadata.metadata("dj-turso")

    assert metadata["Name"] == "dj-turso"
    assert metadata.get_all("Provides-Extra") == ["serverless"]
    assert metadata["Requires-Python"] == ">=3.12"
    assert metadata.get_all("Requires-Dist") == [
        "django<6.2,>=6.1",
        "pyturso<0.9,>=0.8.3",
        "turso-serverless<0.2,>=0.1.0; extra == 'serverless'",
    ]
    assert metadata.get_all("Project-URL") == [
        "Homepage, https://github.com/lincolnloop/dj-turso",
        "Issues, https://github.com/lincolnloop/dj-turso/issues",
        "Repository, https://github.com/lincolnloop/dj-turso",
    ]
