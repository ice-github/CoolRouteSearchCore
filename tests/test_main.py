from main import gportal_username_and_password_from_env


def test_gportal_username_and_password_from_env_reads_expected_variables(monkeypatch) -> None:
    monkeypatch.setenv("GPORTAL_USER", "demo-user")
    monkeypatch.setenv("GPORTAL_PASS", "demo-pass")

    assert gportal_username_and_password_from_env() == ("demo-user", "demo-pass")
