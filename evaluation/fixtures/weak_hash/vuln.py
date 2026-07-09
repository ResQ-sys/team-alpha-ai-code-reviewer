"""Weak cryptographic hash fixture (CWE-327).

MD5 is used to hash a password. MD5 is broken for security purposes; a salted
password hash (bcrypt/scrypt/argon2) should be used instead.
"""
import hashlib


def hash_password(password):
    # BAD: MD5 is a weak, fast hash unsuitable for passwords.
    return hashlib.md5(password.encode()).hexdigest()
