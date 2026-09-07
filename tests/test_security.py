import pytest

from anki_sync_hub.security import (
    hash_owner_password,
    hash_sync_password,
    validate_password,
    validate_sync_username,
    verify_owner_password,
)


def test_owner_password_round_trip() -> None:
    encoded = hash_owner_password("a sufficiently long password")
    assert verify_owner_password("a sufficiently long password", encoded)
    assert not verify_owner_password("an incorrect long password", encoded)


def test_sync_password_is_pbkdf2_phc() -> None:
    encoded = hash_sync_password("a sufficiently long password")
    assert encoded.startswith("$pbkdf2-sha256$i=600000,l=32$")
    assert len(encoded.split("$")) == 5


@pytest.mark.parametrize("username", ["alice", "alice@example.com", "user.name+anki"])
def test_valid_sync_usernames(username: str) -> None:
    assert validate_sync_username(username) == username


@pytest.mark.parametrize("username", ["../alice", "alice/bob", ".alice", "alice bob"])
def test_invalid_sync_usernames(username: str) -> None:
    with pytest.raises(ValueError):
        validate_sync_username(username)


def test_password_minimum() -> None:
    with pytest.raises(ValueError):
        validate_password("too short")
