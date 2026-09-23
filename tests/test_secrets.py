"""Tests for the secret scanner.

Push-safety: every secret-shaped fixture below is assembled from concatenated
string parts at call time. No contiguous, real-looking token is ever written to
disk, so this test file cannot itself trip a secret scanner (that is the whole
lesson the scanner teaches).
"""

from __future__ import annotations

from pre_commit_hooks.secrets import scan_text

THRESHOLD = 3.2


# --------------------------------------------------------------------------- #
# Fixture builders (assembled at runtime, never a literal secret on disk)
# --------------------------------------------------------------------------- #
def aws_access_key() -> str:
    return "AKIA" + "J7QR2K9WL4TZ8XY3"  # AKIA + 16 chars, high variety


def github_pat() -> str:
    return "ghp_" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8"  # ghp_ + 36 chars


def google_api_key() -> str:
    return "AIza" + "9x8Y7" + "w6V5u" + "4T3s2" + "R1q0P" + "9o8N7" + "m6L5k" + "4J3h2"


def stripe_key() -> str:
    return "sk" + "_" + "live" + "_" + "4eC8gH2iJ6kL0mN4oP8qR2sT"


def slack_webhook() -> str:
    # Assemble the Slack-webhook shape at runtime. This is exactly the string
    # that GitHub push protection (GH013) rejects on push -- the scanner must
    # catch it at commit time instead.
    host = "hooks." + "slack" + ".com"
    workspace = "T" + "A1B2C3D4E5"
    channel = "B" + "F6G7H8I9J0"
    token = "K3l4M5n6O7p8Q9r0S1t2U3v4W"
    return "https://" + host + "/services/" + workspace + "/" + channel + "/" + token


def discord_webhook() -> str:
    host = "discord" + ".com"
    webhook_id = "1730928475610283"
    token = "aZ9bY8cX7dW6eV5fU4gT3hS2"
    return "https://" + host + "/api/webhooks/" + webhook_id + "/" + token


def nvidia_key() -> str:
    return "nvapi-" + "Ab3Cd6Ef9Gh2Ij5Kl8Mn1"


def private_key_header() -> str:
    return "-----BEGIN RSA PRIVATE KEY-----"


# --------------------------------------------------------------------------- #
# Positive cases: planted secrets must be caught
# --------------------------------------------------------------------------- #
def _find_rule(text, rule_id):
    return [f for f in scan_text("f.py", text, THRESHOLD) if f.rule_id == rule_id]


def test_catches_aws_access_key():
    assert _find_rule(f'key = "{aws_access_key()}"', "aws-access-key-id")


def test_catches_github_pat():
    assert _find_rule(f"token: {github_pat()}", "github-token")


def test_catches_google_api_key():
    assert _find_rule(f'GOOGLE = "{google_api_key()}"', "gcp-api-key")


def test_catches_stripe_key():
    assert _find_rule(f"stripe={stripe_key()}", "stripe-secret-key")


def test_catches_slack_webhook():
    # The headline case: the shape GitHub push protection blocks.
    findings = _find_rule(f'url = "{slack_webhook()}"', "slack-webhook")
    assert findings, "the Slack-webhook-shaped string must be flagged"


def test_catches_discord_webhook():
    assert _find_rule(f'hook = "{discord_webhook()}"', "discord-webhook")


def test_catches_nvidia_key():
    assert _find_rule(f"NVIDIA_API_KEY={nvidia_key()}", "nvidia-nim-key")


def test_catches_private_key_block():
    assert _find_rule(private_key_header(), "private-key")


def test_catches_generic_high_entropy_assignment():
    text = 'password = "hunter2Zx9Qw8Lp3Ba"'
    assert scan_text("app.py", text, THRESHOLD)


def test_reports_correct_line_number():
    text = "clean = 1\nalso_clean = 2\nsecret = " + f'"{aws_access_key()}"'
    findings = scan_text("multi.py", text, THRESHOLD)
    assert findings
    assert findings[0].line == 3


# --------------------------------------------------------------------------- #
# Negative cases: obvious placeholders must NOT be flagged
# --------------------------------------------------------------------------- #
def test_ignores_nvidia_placeholder():
    text = "NVIDIA_API_KEY=nvapi-" + "X" * 24
    assert scan_text(".env.example", text, THRESHOLD) == []


def test_ignores_github_placeholder():
    text = "token = ghp_" + "X" * 36
    assert scan_text("README.md", text, THRESHOLD) == []


def test_ignores_aws_placeholder():
    text = "aws_key = AKIA" + "X" * 16
    assert scan_text("docs.md", text, THRESHOLD) == []


def test_ignores_named_placeholder_values():
    for value in ("your_api_key_here", "changeme", "example-placeholder", "REDACTED"):
        text = f'api_key = "{value}"'
        assert scan_text("cfg.py", text, THRESHOLD) == [], value


def test_ignores_example_com_webhook():
    # example.com host is not the real Slack host -> not a secret.
    text = 'url = "https://hooks.example.com/services/T00000000/B00000000/token"'
    assert scan_text("cfg.py", text, THRESHOLD) == []


def test_inline_allow_pragma_suppresses():
    text = f"token = {github_pat()}  # pragma: allowlist secret"
    assert scan_text("f.py", text, THRESHOLD) == []


def test_allow_regex_suppresses():
    import re

    text = f'token = "{github_pat()}"'
    allow = [re.compile(re.escape(github_pat()))]
    assert scan_text("f.py", text, THRESHOLD, allow) == []


def test_low_entropy_generic_value_ignored():
    # A short, repetitive value in a generic assignment is not treated as secret.
    text = 'password = "aaaaaaaaaaaa"'
    assert scan_text("f.py", text, THRESHOLD) == []


# --------------------------------------------------------------------------- #
# Placeholder heuristics run on the token body and need filler to dominate
# --------------------------------------------------------------------------- #
def _rule_ids(text):
    return [f.rule_id for f in scan_text("f.py", text, THRESHOLD)]


def test_stripe_test_key_is_reported():
    # Regression: "test_" was a placeholder substring, so sk_test_ keys were
    # silently dropped even though the README listed them.
    key = "sk" + "_" + "test" + "_" + "4eC8gH2iJ6kL0mN4oP8qR2sT"
    assert _rule_ids(f'STRIPE = "{key}"') == ["stripe-secret-key"]


def test_real_token_containing_a_placeholder_word_is_reported():
    token = "ghp_" + "a1B2c3fakeD4e5F6g7H8i9J0k1L2m3N4o5P6"  # "fake" inside
    assert _rule_ids(f'token = "{token}"') == ["github-token"]


def test_openai_key_containing_todo_is_reported():
    key = "sk-" + "proj-" + "Todo8Qm2Lx7Vb4Nc1Zr9Tk3Wp6Ys5Hd0Gf"
    assert _rule_ids(f'OPENAI = "{key}"') == ["openai-key"]


def test_placeholder_dominated_values_are_ignored():
    for value in (
        "your_api_key_here",
        "test_password_123",
        "changeme123456",
        "<your-token-goes-here>",
        "${API_TOKEN_FROM_ENV}",
    ):
        assert scan_text("cfg.py", f'api_key = "{value}"', THRESHOLD) == [], value


def test_placeholder_word_in_stripe_prefix_does_not_hide_body():
    fake_body = "sk" + "_" + "test" + "_" + "X" * 24
    assert _rule_ids(f'k = "{fake_body}"') == []


# --------------------------------------------------------------------------- #
# One finding per secret
# --------------------------------------------------------------------------- #
def anthropic_key() -> str:
    return "sk-" + "ant-" + "api03-" + "Zq8Lm2Xv7Bn4Cr1Tk9Wp3Ys6Hd0Gf5Jq8Lm2Xv7Bn4Cr1Tk9W"


def test_anthropic_key_is_not_reported_as_openai():
    # Regression: sk-ant- keys matched the OpenAI rule and the .env rule.
    assert _rule_ids(f"ANTHROPIC_API_KEY={anthropic_key()}") == ["anthropic-key"]


def test_structured_token_in_assignment_reported_once():
    # Regression: github-token + generic-assignment for the same characters.
    assert _rule_ids(f'token = "{github_pat()}"') == ["github-token"]


def test_two_different_secrets_on_one_line_both_reported():
    text = f'a = "{github_pat()}"; b = "{aws_access_key()}"'
    assert sorted(_rule_ids(text)) == ["aws-access-key-id", "github-token"]


# --------------------------------------------------------------------------- #
# Exclude globs
# --------------------------------------------------------------------------- #
def test_exclude_matches_nested_basename():
    from pre_commit_hooks.secrets import _excluded

    # Regression: only the full path was matched, so web/package-lock.json
    # was scanned despite "package-lock.json" being excluded.
    assert _excluded("web/package-lock.json", ["package-lock.json"])
    assert _excluded("deep/dir/app.min.js", ["*.min.js"])
    assert _excluded("vendor/x.py", ["vendor/*"])
    assert not _excluded("src/app.py", ["*.lock", "package-lock.json"])
