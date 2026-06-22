from __future__ import annotations

from urllib.parse import urlparse, urlunparse

from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes


def encode_vpn_host(
    host: str,
    vpn_key: str = "wrdvpnisthebest!",
    vpn_iv: str = "wrdvpnisthebest!",
) -> str:
    """Encode a host for BIT WebVPN URLs.

    Adapted from BIT-Login's `utils.encode_vpn_host`.
    """

    key_bytes = vpn_key.encode("utf-8")
    iv_bytes = vpn_iv.encode("utf-8")
    text_len = len(host)
    pad_len = (16 - text_len % 16) % 16
    plaintext = (host + "0" * pad_len).encode("utf-8")

    ciphertext = bytearray(len(plaintext))
    feedback = bytearray(iv_bytes)
    for offset in range(0, len(plaintext), 16):
        encryptor = Cipher(
            algorithms.AES(key_bytes),
            modes.ECB(),
            backend=default_backend(),
        ).encryptor()
        keystream = encryptor.update(bytes(feedback)) + encryptor.finalize()
        block = bytearray(16)
        for index in range(16):
            pos = offset + index
            if pos < len(plaintext):
                block[index] = plaintext[pos] ^ keystream[index]
                ciphertext[pos] = block[index]
        feedback = block

    return iv_bytes.hex() + ciphertext.hex()[: text_len * 2]


def to_webvpn_url(url: str) -> str:
    """Convert a normal URL into BIT WebVPN URL format."""

    parsed = urlparse(url)
    if not parsed.hostname:
        return url

    encoded_host = encode_vpn_host(parsed.hostname)
    path = f"/{parsed.scheme}/{encoded_host}{parsed.path}"
    return urlunparse(
        (
            "https",
            "webvpn.bit.edu.cn",
            path,
            parsed.params,
            parsed.query,
            parsed.fragment,
        )
    )
