import hashlib
import io
import json
import os
import re
import zipfile
from datetime import datetime
from typing import Dict, Final, List, Optional, Tuple

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError, field_validator, model_validator

from storage.models import Account, AccountRecord, AccountStatus, PendingAccount, QuotaInvalidReason, utc_now

_BUNDLE_PREFIX: Final[bytes] = b"OCRB1"
_SALT_LENGTH: Final[int] = 16
_NONCE_LENGTH: Final[int] = 12
_PBKDF2_ITERATIONS: Final[int] = 600_000
_MAX_BUNDLE_SIZE: Final[int] = 10 * 1024 * 1024
_MAX_ARCHIVE_SIZE: Final[int] = 20 * 1024 * 1024
_ALLOWED_ENTRIES: Final[frozenset[str]] = frozenset({"manifest.json", "accounts.json"})


class BundleValidationError(Exception):
    """
    导入包无法认证或不符合受支持格式异常
    """


class BundleManifest(BaseModel):
    """
    加密账号包清单
    """

    model_config = ConfigDict(extra="forbid")

    format_version: int = Field(..., description="导出包格式版本")
    created_at: datetime = Field(..., description="导出包创建时间")
    account_count: int = Field(..., ge=0, description="账号记录数量")
    payload_sha256: str = Field(..., pattern=r"^[a-f0-9]{64}$", description="账号载荷完整性摘要")


class BundleAccount(BaseModel):
    """
    导出包内的完整账号记录
    """

    model_config = ConfigDict(extra="forbid")

    uuid: str = Field(..., min_length=1, description="账号稳定唯一标识")
    github_username: str = Field(..., min_length=1, description="GitHub 用户名")
    github_email: str = Field(..., min_length=3, description="GitHub 注册邮箱")
    github_password: SecretStr = Field(..., description="GitHub 密码")
    github_created_at: datetime = Field(..., description="GitHub 账号创建时间")
    opencode_provider_name: Optional[str] = Field(default=None, min_length=1, description="OpenCode provider 名称")
    opencode_workspace_id: Optional[str] = Field(default=None, min_length=1, description="OpenCode 工作区标识")
    opencode_api_key: Optional[SecretStr] = Field(
        default=None,
        description="OpenCode API Key",
    )
    opencode_user_id: Optional[str] = Field(default=None, description="OpenCode 用户标识")
    email_provider: str = Field(..., min_length=1, description="临时邮箱 provider 名称")
    temp_email: str = Field(..., min_length=3, description="临时邮箱地址")
    status: AccountStatus = Field(..., description="账号状态")
    quota_total: Optional[int] = Field(default=None, ge=0, description="总额度")
    quota_used: Optional[int] = Field(default=None, ge=0, description="已用额度")
    quota_updated_at: Optional[datetime] = Field(default=None, description="额度更新时间")
    quota_checked_at: Optional[datetime] = Field(default=None, description="最近一次确定性额度检查时间")
    quota_invalid_reason: Optional[QuotaInvalidReason] = Field(default=None, description="额度检查确认的失效原因")
    created_at: datetime = Field(..., description="本地记录创建时间")
    updated_at: datetime = Field(..., description="本地记录更新时间")
    notes: Optional[str] = Field(default=None, max_length=2000, description="用户备注")

    @field_validator("github_password")
    @classmethod
    def validate_github_password(cls, value: SecretStr) -> SecretStr:
        """
        拒绝导入空 GitHub 密码

        :param value (SecretStr): 待验证密码

        :return SecretStr: 已验证密码

        :raises ValueError: 密码为空
        """

        if not value.get_secret_value():
            raise ValueError("GitHub 密码不能为空")
        return value

    @field_validator("opencode_api_key")
    @classmethod
    def validate_opencode_api_key(cls, value: Optional[SecretStr]) -> Optional[SecretStr]:
        """
        校验导入包中的 OpenCode API Key 格式

        :param value (SecretStr): 待验证 API Key

        :return SecretStr: 已验证 API Key

        :raises ValueError: API Key 格式无效
        """

        if value is not None and re.fullmatch(r"sk-[A-Za-z0-9]{64}", value.get_secret_value()) is None:
            raise ValueError("OpenCode API Key 格式无效")
        return value

    @model_validator(mode="after")
    def validate_completion_shape(self) -> "BundleAccount":
        """
        校验 OpenCode 字段必须同时存在或同时缺失

        :return BundleAccount: 结构一致的账号记录

        :raises ValueError: 完整与未完成字段混合
        """

        completion_fields = (
            self.opencode_provider_name,
            self.opencode_workspace_id,
            self.opencode_api_key,
        )
        present_count = sum(value is not None for value in completion_fields)
        if present_count not in {0, len(completion_fields)}:
            raise ValueError("OpenCode 完成字段必须同时存在或缺失")
        if present_count == 0 and self.status not in {
            AccountStatus.PENDING_SETUP,
            AccountStatus.PENDING_PAYMENT,
            AccountStatus.CANCELLED,
            AccountStatus.INVALID,
        }:
            raise ValueError("未完成账号状态无效")
        return self

    def to_record(self) -> AccountRecord:
        """
        转换为已验证的完整或未完成持久化模型

        :return AccountRecord: 可进入仓储事务的账号记录
        """

        if self.opencode_provider_name is not None:
            return Account.model_validate(self.model_dump())
        return PendingAccount(
            uuid=self.uuid,
            github_username=self.github_username,
            github_email=self.github_email,
            github_password=self.github_password,
            github_created_at=self.github_created_at,
            email_provider=self.email_provider,
            temp_email=self.temp_email,
            status=self.status,
            created_at=self.created_at,
            updated_at=self.updated_at,
            notes=self.notes,
        )


class AccountBundleCodec:
    """
    版本化账号 ZIP 的 AES-GCM 编解码器
    """

    def export(self, accounts: List[AccountRecord], password: SecretStr) -> bytes:
        """
        生成整包认证加密的账号导出数据

        :param accounts (List): 待导出的完整账号记录
        :param password (SecretStr): 导出包密码

        :return bytes: 版本化 AES-GCM 加密包
        """

        accounts_payload = _serialize_accounts(accounts)
        manifest = BundleManifest(
            format_version=2,
            created_at=utc_now(),
            account_count=len(accounts),
            payload_sha256=hashlib.sha256(accounts_payload).hexdigest(),
        )
        archive = _create_archive(manifest.model_dump_json().encode("utf-8"), accounts_payload)
        salt = os.urandom(_SALT_LENGTH)
        nonce = os.urandom(_NONCE_LENGTH)
        key = _derive_key(password, salt)
        ciphertext = AESGCM(key).encrypt(nonce, archive, _BUNDLE_PREFIX)
        return _BUNDLE_PREFIX + salt + nonce + ciphertext

    def load(self, bundle: bytes, password: SecretStr) -> List[AccountRecord]:
        """
        认证、解密并完整校验账号导入包

        :param bundle (bytes): 待导入的加密包
        :param password (SecretStr): 导出包密码

        :return List: 全部通过校验的账号记录

        :raises BundleValidationError: 包过大、认证失败或结构无效
        """

        archive = _decrypt_bundle(bundle, password)
        manifest_payload, accounts_payload = _read_archive(archive)
        try:
            manifest = BundleManifest.model_validate_json(manifest_payload)
            account_items = _parse_accounts(accounts_payload)
        except ValidationError as error:
            raise BundleValidationError("导入包结构无效") from error
        if manifest.format_version not in {1, 2}:
            raise BundleValidationError("不支持的导入包版本")
        if manifest.account_count != len(account_items):
            raise BundleValidationError("导入包账号数量不一致")
        if manifest.payload_sha256 != hashlib.sha256(accounts_payload).hexdigest():
            raise BundleValidationError("导入包完整性校验失败")
        _validate_unique_accounts(account_items)
        return [account.to_record() for account in account_items]


def _derive_key(password: SecretStr, salt: bytes) -> bytes:
    if not password.get_secret_value():
        raise BundleValidationError("导出包密码不能为空")
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=_PBKDF2_ITERATIONS,
    )
    return kdf.derive(password.get_secret_value().encode("utf-8"))


def _decrypt_bundle(bundle: bytes, password: SecretStr) -> bytes:
    minimum_size = len(_BUNDLE_PREFIX) + _SALT_LENGTH + _NONCE_LENGTH + 16
    if len(bundle) < minimum_size or len(bundle) > _MAX_BUNDLE_SIZE or not bundle.startswith(_BUNDLE_PREFIX):
        raise BundleValidationError("导入包大小或格式无效")
    salt_start = len(_BUNDLE_PREFIX)
    nonce_start = salt_start + _SALT_LENGTH
    ciphertext_start = nonce_start + _NONCE_LENGTH
    try:
        archive = AESGCM(_derive_key(password, bundle[salt_start:nonce_start])).decrypt(
            bundle[nonce_start:ciphertext_start],
            bundle[ciphertext_start:],
            _BUNDLE_PREFIX,
        )
    except InvalidTag as error:
        raise BundleValidationError("导入包密码错误或内容已损坏") from error
    if len(archive) > _MAX_ARCHIVE_SIZE:
        raise BundleValidationError("导入包解压内容过大")
    return archive


def _create_archive(manifest_payload: bytes, accounts_payload: bytes) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", manifest_payload)
        archive.writestr("accounts.json", accounts_payload)
    return buffer.getvalue()


def _read_archive(archive_payload: bytes) -> Tuple[bytes, bytes]:
    try:
        with zipfile.ZipFile(io.BytesIO(archive_payload), "r") as archive:
            entries = archive.infolist()
            names = [entry.filename for entry in entries]
            if len(names) != len(set(names)) or set(names) != _ALLOWED_ENTRIES:
                raise BundleValidationError("导入包文件清单无效")
            if any(_is_unsafe_entry(entry) for entry in entries):
                raise BundleValidationError("导入包包含不安全文件条目")
            if sum(entry.file_size for entry in entries) > _MAX_ARCHIVE_SIZE:
                raise BundleValidationError("导入包解压内容过大")
            return archive.read("manifest.json"), archive.read("accounts.json")
    except (zipfile.BadZipFile, RuntimeError) as error:
        raise BundleValidationError("导入包 ZIP 内容无效") from error


def _is_unsafe_entry(entry: zipfile.ZipInfo) -> bool:
    path_parts = entry.filename.replace("\\", "/").split("/")
    file_type = (entry.external_attr >> 16) & 0o170000
    return entry.is_dir() or ".." in path_parts or path_parts[0] == "" or file_type == 0o120000


def _parse_accounts(payload: bytes) -> List[BundleAccount]:
    try:
        raw_accounts = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise BundleValidationError("导入包账号数据不是有效 JSON") from error
    if not isinstance(raw_accounts, list):
        raise BundleValidationError("导入包账号数据必须是列表")
    return [BundleAccount.model_validate(item) for item in raw_accounts]


def _validate_unique_accounts(accounts: List[BundleAccount]) -> None:
    account_ids = [account.uuid for account in accounts]
    github_usernames = [account.github_username for account in accounts]
    provider_names = [
        account.opencode_provider_name for account in accounts if account.opencode_provider_name is not None
    ]
    if (
        len(account_ids) != len(set(account_ids))
        or len(github_usernames) != len(set(github_usernames))
        or len(provider_names) != len(set(provider_names))
    ):
        raise BundleValidationError("导入包包含重复账号或 provider")


def _serialize_accounts(accounts: List[AccountRecord]) -> bytes:
    payload = [_account_payload(account) for account in accounts]
    return json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode("utf-8")


def _account_payload(account: AccountRecord) -> Dict[str, object]:
    payload: Dict[str, object] = {
        "uuid": account.uuid,
        "github_username": account.github_username,
        "github_email": account.github_email,
        "github_password": account.github_password.get_secret_value(),
        "github_created_at": account.github_created_at.isoformat(),
        "opencode_provider_name": None,
        "opencode_workspace_id": None,
        "opencode_api_key": None,
        "opencode_user_id": None,
        "email_provider": account.email_provider,
        "temp_email": account.temp_email,
        "status": account.status.value,
        "quota_total": None,
        "quota_used": None,
        "quota_updated_at": None,
        "quota_checked_at": None,
        "quota_invalid_reason": None,
        "created_at": account.created_at.isoformat(),
        "updated_at": account.updated_at.isoformat(),
        "notes": account.notes,
    }
    if isinstance(account, Account):
        payload.update(
            {
                "opencode_provider_name": account.opencode_provider_name,
                "opencode_workspace_id": account.opencode_workspace_id,
                "opencode_api_key": account.opencode_api_key.get_secret_value(),
                "opencode_user_id": account.opencode_user_id,
                "quota_total": account.quota_total,
                "quota_used": account.quota_used,
                "quota_updated_at": account.quota_updated_at.isoformat() if account.quota_updated_at else None,
                "quota_checked_at": account.quota_checked_at.isoformat() if account.quota_checked_at else None,
                "quota_invalid_reason": (
                    account.quota_invalid_reason.value if account.quota_invalid_reason is not None else None
                ),
            }
        )
    return payload
