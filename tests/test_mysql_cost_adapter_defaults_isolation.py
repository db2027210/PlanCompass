from mysql_cost_adapter import MySQLCLIClient


def test_defaults_file_only_is_first_and_excludes_home_defaults(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    (home / ".my.cnf").write_text("[client]\npassword=must-not-load\n", encoding="utf-8")
    isolated = tmp_path / "isolated.cnf"
    isolated.write_text("[client]\nuser=root\nprotocol=tcp\n", encoding="utf-8")
    monkeypatch.setenv("HOME", str(home))

    args = MySQLCLIClient(
        {"defaults_extra_file": str(isolated), "defaults_file_only": True}
    )._args("SELECT 1")

    assert args[0] == "mysql"
    assert args[1] == f"--defaults-file={isolated}"
    assert not any("defaults-extra-file" in arg for arg in args)
    assert not any(str(home) in arg or "must-not-load" in arg for arg in args)


def test_default_mode_remains_defaults_extra_file_compatible(tmp_path):
    defaults = tmp_path / "extra.cnf"
    args = MySQLCLIClient({"defaults_extra_file": str(defaults)})._args("SELECT 1")
    assert args[1] == f"--defaults-extra-file={defaults}"


def test_password_is_never_placed_in_process_arguments():
    secret = "unit-test-secret-not-for-argv"
    client = MySQLCLIClient({"password": secret, "defaults_file_only": True})
    assert all(secret not in arg for arg in client._args("SELECT 1"))
