import importlib


def _keypair():
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.hazmat.primitives import serialization
    k = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    priv = k.private_bytes(serialization.Encoding.PEM,
                           serialization.PrivateFormat.PKCS8,
                           serialization.NoEncryption()).decode()
    pub = k.public_key().public_bytes(serialization.Encoding.PEM,
                                       serialization.PublicFormat.SubjectPublicKeyInfo).decode()
    return priv, pub


def test_jwt_verify_valid_and_wrong_audience(monkeypatch):
    import jwt
    priv, pub = _keypair()
    monkeypatch.setenv("PFLEGE_JWT_PUBLIC_KEY", pub)
    monkeypatch.setenv("PFLEGE_JWT_ISSUER", "https://app.linn.games")
    monkeypatch.setenv("PFLEGE_JWT_AUDIENCE", "pflege-marktradar")
    from marktradar import auth
    importlib.reload(auth)
    try:
        good = jwt.encode({"aud": "pflege-marktradar", "iss": "https://app.linn.games"},
                          priv, algorithm="RS256")
        assert auth.verify(good) is not None
        bad = jwt.encode({"aud": "someone-else", "iss": "https://app.linn.games"},
                         priv, algorithm="RS256")
        assert auth.verify(bad) is None
        assert auth.verify("garbage") is None
    finally:
        monkeypatch.delenv("PFLEGE_JWT_PUBLIC_KEY", raising=False)
        importlib.reload(auth)  # zurück auf "kein Key" für andere Tests


def test_jwt_disabled_without_key(monkeypatch):
    monkeypatch.delenv("PFLEGE_JWT_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("PFLEGE_JWT_PUBLIC_KEY_PATH", raising=False)
    from marktradar import auth
    importlib.reload(auth)
    assert auth.PUBLIC_KEY is None
    assert auth.verify("whatever") == {}
