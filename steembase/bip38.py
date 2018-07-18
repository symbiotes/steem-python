import hashlib
import logging
import os
import sys
from binascii import hexlify, unhexlify

from cryptography.hazmat.primitives.ciphers import Cipher
from cryptography.hazmat.primitives.ciphers import algorithms
from cryptography.hazmat.primitives.ciphers import modes
from cryptography.hazmat.backends import default_backend

from .account import PrivateKey
from .base58 import Base58, base58decode
from steem.utils import compat_bytes


log = logging.getLogger(__name__)





SCRYPT_MODULE = os.environ.get('SCRYPT_MODULE', None)
if not SCRYPT_MODULE:
    try:
        import scrypt

        SCRYPT_MODULE = "scrypt"
    except ImportError:
        try:
            import pylibscrypt as scrypt

            SCRYPT_MODULE = "pylibscrypt"
        except ImportError:
            raise ImportError("Missing dependency: scrypt or pylibscrypt")
elif 'pylibscrypt' in SCRYPT_MODULE:
    try:
        import pylibscrypt as scrypt
    except ImportError:
        raise ImportError("Missing dependency: pylibscrypt explicitly set but missing")
elif 'scrypt' in SCRYPT_MODULE:
    try:
        import scrypt
    except ImportError:
            raise ImportError("Missing dependency: scrypt explicitly set but missing")


log.debug("Using scrypt module: %s" % SCRYPT_MODULE)


class SaltException(Exception):
    pass


def _encrypt_xor(a, b, cipher):
    """ Returns encrypt(a ^ b). """
    a = unhexlify('%0.32x' % (int((a), 16) ^ int(hexlify(b), 16)))
    encryptor = cipher.encryptor()
    return encryptor.update(a) + encryptor.finalize()


def encrypt(privkey, passphrase):
    """ BIP0038 non-ec-multiply encryption. Returns BIP0038 encrypted privkey.

    :param privkey: Private key
    :type privkey: Base58
    :param str passphrase: UTF-8 encoded passphrase for encryption
    :return: BIP0038 non-ec-multiply encrypted wif key
    :rtype: Base58

    """
    privkeyhex = repr(privkey)  # hex
    addr = format(privkey.uncompressed.address, "BTC")
    a = compat_bytes(addr, 'ascii')
    salt = hashlib.sha256(hashlib.sha256(a).digest()).digest()[0:4]
    if SCRYPT_MODULE == "scrypt":
        if sys.version >= '3.0.0':
            key = scrypt.hash(passphrase, salt, 16384, 8, 8)
        else:
            key = scrypt.hash(str(passphrase), str(salt), 16384, 8, 8)
    elif SCRYPT_MODULE == "pylibscrypt":
        key = scrypt.scrypt(compat_bytes(passphrase, "utf-8"), salt, 16384, 8, 8)
    else:
        raise ValueError("No scrypt module loaded")
    (derived_half1, derived_half2) = (key[:32], key[32:])
    backend = default_backend()
    cipher = Cipher(algorithms.AES(derived_half2), modes.ECB(), backend=backend)
    encrypted_half1 = _encrypt_xor(privkeyhex[:32], derived_half1[:16], cipher)

    encrypted_half2 = _encrypt_xor(privkeyhex[32:], derived_half1[16:], cipher)
    " flag byte is forced 0xc0 because Graphene only uses compressed keys "
    payload = (
            b'\x01' + b'\x42' + b'\xc0' + salt + encrypted_half1 + encrypted_half2)
    " Checksum "
    checksum = hashlib.sha256(hashlib.sha256(payload).digest()).digest()[:4]
    privatekey = hexlify(payload + checksum).decode('ascii')
    return Base58(privatekey)


def decrypt(encrypted_privkey, passphrase):
    """BIP0038 non-ec-multiply decryption. Returns WIF privkey.

    :param Base58 encrypted_privkey: Private key
    :param str passphrase: UTF-8 encoded passphrase for decryption
    :return: BIP0038 non-ec-multiply decrypted key
    :rtype: Base58
    :raises SaltException: if checksum verification failed (e.g. wrong
    password)

    """

    d = unhexlify(base58decode(encrypted_privkey))
    d = d[2:]  # remove trailing 0x01 and 0x42
    flagbyte = d[0:1]  # get flag byte
    d = d[1:]  # get payload
    assert flagbyte == b'\xc0', "Flagbyte has to be 0xc0"
    salt = d[0:4]
    d = d[4:-4]
    if SCRYPT_MODULE == "scrypt":
        if sys.version >= '3.0.0':
            key = scrypt.hash(passphrase, salt, 16384, 8, 8)
        else:
            key = scrypt.hash(str(passphrase), str(salt), 16384, 8, 8)
    elif SCRYPT_MODULE == "pylibscrypt":
        key = scrypt.scrypt(compat_bytes(passphrase, "utf-8"), salt, 16384, 8, 8)
    else:
        raise ValueError("No scrypt module loaded")
    derivedhalf1 = key[0:32]
    derivedhalf2 = key[32:64]
    encryptedhalf1 = d[0:16]
    encryptedhalf2 = d[16:32]

    backend = default_backend()
    cipher = Cipher(algorithms.AES(derivedhalf2), modes.ECB(), backend=backend)
    decryptor = cipher.decryptor()
    decryptedhalf2 = decryptor.update(encryptedhalf2) + decryptor.finalize()

    decryptor = cipher.decryptor()
    decryptedhalf1 = decryptor.update(encryptedhalf1) + decryptor.finalize()

    privraw = decryptedhalf1 + decryptedhalf2
    privraw = ('%064x' %
               (int(hexlify(privraw), 16) ^ int(hexlify(derivedhalf1), 16)))
    wif = Base58(privraw)
    """ Verify Salt """
    privkey = PrivateKey(format(wif, "wif"))
    addr = format(privkey.uncompressed.address, "BTC")
    a = compat_bytes(addr, 'ascii')
    saltverify = hashlib.sha256(hashlib.sha256(a).digest()).digest()[0:4]
    if saltverify != salt:
        raise SaltException(
            'checksum verification failed! Password may be incorrect.')
    return wif
