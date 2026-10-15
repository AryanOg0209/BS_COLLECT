import os
import stat

from collectos.envfile import load_env_file


def write(tmp_path, text, mode=0o600):
    p = tmp_path / ".env"
    p.write_text(text)
    p.chmod(mode)
    return p


def test_loads_only_allowed_name_value_lines(tmp_path):
    env = {}
    p = write(tmp_path, "# comment\nANTHROPIC_API_KEY=sk-ant-test\nexport COLLECTOS_AI_MODEL = 'claude-haiku-5-5'\nOTHER=nope\n\n")
    assert load_env_file(p, env) == []
    assert env == {"ANTHROPIC_API_KEY": "sk-ant-test", "COLLECTOS_AI_MODEL": "claude-haiku-5-5"}


def test_a_real_environment_variable_always_wins(tmp_path):
    env = {"ANTHROPIC_API_KEY": "from-shell"}
    load_env_file(write(tmp_path, "ANTHROPIC_API_KEY=from-file\n"), env)
    assert env["ANTHROPIC_API_KEY"] == "from-shell"


def test_a_bare_key_with_no_name_is_ignored_with_a_helpful_warning_that_never_contains_the_value(tmp_path):
    env = {}
    warnings = load_env_file(write(tmp_path, "sk-ant-api03-SECRETSECRETSECRET\n"), env)
    assert env == {} and warnings and "ANTHROPIC_API_KEY=" in warnings[0]
    assert "SECRET" not in " ".join(warnings)


def test_world_readable_file_gets_a_warning(tmp_path):
    warnings = load_env_file(write(tmp_path, "ANTHROPIC_API_KEY=k\n", mode=0o644), {})
    assert any("chmod 600" in w for w in warnings)
    assert not load_env_file(write(tmp_path, "ANTHROPIC_API_KEY=k\n", mode=0o600), {})


def test_empty_values_and_missing_files_are_harmless(tmp_path):
    env = {}
    assert load_env_file(tmp_path / "nope.env", env) == [] and env == {}
    load_env_file(write(tmp_path, "ANTHROPIC_API_KEY=\n"), env)
    assert "ANTHROPIC_API_KEY" not in env


def test_values_are_never_returned_in_warnings(tmp_path):
    warnings = load_env_file(write(tmp_path, "ANTHROPIC_API_KEY=topsecretvalue\nnot a setting topsecretvalue\n", mode=0o644), {})
    assert "topsecretvalue" not in " ".join(warnings)
