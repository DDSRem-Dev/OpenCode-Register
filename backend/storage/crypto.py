import os
from typing import Final

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from pydantic import SecretStr

_FORMAT_PREFIX: Final[bytes] = b"OCR1"
_NONCE_LENGTH: Final[int] = 12
_SALT_LENGTH: Final[int] = 16
_PBKDF2_ITERATIONS: Final[int] = 600_000
_VERIFIER_VALUE: Final[str] = "opencode-register-vault-verifier-v1"


class DecryptionError(Exception):
    """
    加密字段无法认证或解密异常
    """


class FieldCipher:
    """
    使用主密码派生密钥的 AES-GCM 字段加密器
    """

    def __init__(self, master_password: SecretStr, salt: bytes) -> None:
        """
        初始化字段加密器

        :param master_password (SecretStr): 仅驻留内存的用户主密码
        :param salt (bytes): 数据库持久化的随机派生盐

        :raises ValueError: 主密码为空或盐长度无效
        """

        password = master_password.get_secret_value()
        if not password:
            raise ValueError("主密码不能为空")
        if len(salt) != _SALT_LENGTH:
            raise ValueError("加密盐长度无效")
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            iterations=_PBKDF2_ITERATIONS,
        )
        self._key = kdf.derive(password.encode("utf-8"))

    @staticmethod
    def generate_salt() -> bytes:
        """
        生成新的密码派生盐

        :return bytes: 适用于当前加密格式的随机盐
        """

        return os.urandom(_SALT_LENGTH)

    def encrypt(self, value: SecretStr) -> bytes:
        """
        使用唯一随机数加密敏感字符串

        :param value (SecretStr): 待加密敏感值

        :return bytes: 带格式版本和认证标签的密文
        """

        nonce = os.urandom(_NONCE_LENGTH)
        ciphertext = AESGCM(self._key).encrypt(nonce, value.get_secret_value().encode("utf-8"), _FORMAT_PREFIX)
        return _FORMAT_PREFIX + nonce + ciphertext

    def decrypt(self, value: bytes) -> SecretStr:
        """
        验证并解密版本化敏感字段

        :param value (bytes): 数据库中的版本化密文

        :return SecretStr: 禁止直接展示的解密值

        :raises DecryptionError: 密文格式、认证标签或文本编码无效
        """

        if not value.startswith(_FORMAT_PREFIX) or len(value) <= len(_FORMAT_PREFIX) + _NONCE_LENGTH:
            raise DecryptionError("加密字段格式无效")
        nonce_start = len(_FORMAT_PREFIX)
        nonce_end = nonce_start + _NONCE_LENGTH
        try:
            plaintext = AESGCM(self._key).decrypt(value[nonce_start:nonce_end], value[nonce_end:], _FORMAT_PREFIX)
            return SecretStr(plaintext.decode("utf-8"))
        except (InvalidTag, UnicodeDecodeError) as error:
            raise DecryptionError("加密字段认证失败") from error

    def create_verifier(self) -> bytes:
        """
        创建用于空账号库主密码认证的版本化密文

        :return bytes: 不包含主密码的 AES-GCM 认证密文
        """

        return self.encrypt(SecretStr(_VERIFIER_VALUE))

    def verify(self, verifier: bytes) -> None:
        """
        使用当前派生密钥认证账号库验证密文

        :param verifier (bytes): 已持久化的版本化认证密文

        :return None: 验证成功无返回值

        :raises DecryptionError: 密钥错误、密文损坏或验证值不匹配
        """

        value = self.decrypt(verifier).get_secret_value()
        if value != _VERIFIER_VALUE:
            raise DecryptionError("账号库验证值无效")
