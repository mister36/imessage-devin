import pytest

from devin_imessage.policy import PolicyError, SendPolicy, normalize, participants_of


def test_normalize_phone_variants_match():
    assert normalize("+1 (555) 010-1234") == normalize("5550101234") == "5550101234"


def test_normalize_email_is_lowercased():
    assert normalize("  Foo@Example.COM ") == "foo@example.com"


def test_sending_disabled_by_default():
    policy = SendPolicy(allow_send=False, allowlist=["+15550101234"])
    with pytest.raises(PolicyError, match="disabled"):
        policy.check(["+15550101234"])


def test_empty_allowlist_permits_any_recipient():
    SendPolicy(allow_send=True, allowlist=[]).check(["+15550109999"])


def test_allowlist_blocks_unlisted_recipient():
    policy = SendPolicy(allow_send=True, allowlist=["+1 555-010-1234"])
    policy.check(["5550101234"])
    with pytest.raises(PolicyError, match="not in IMESSAGE_SEND_ALLOWLIST"):
        policy.check(["5550109999"])


def test_group_chat_is_blocked_when_any_participant_is_unlisted():
    policy = SendPolicy(allow_send=True, allowlist=["5550101234"])
    with pytest.raises(PolicyError):
        policy.check(["5550101234", "5550109999"])


def test_participants_of_skips_addressless_handles():
    chat = {"participants": [{"address": "a@b.com"}, {}, {"address": ""}]}
    assert participants_of(chat) == ["a@b.com"]
