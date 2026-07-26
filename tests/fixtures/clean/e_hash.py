# A real sha256 constant — short hex must not read as obfuscation.
EXPECTED = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
def verify(digest: str) -> bool:
    return digest == EXPECTED
