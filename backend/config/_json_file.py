import json
import os
import shutil
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Dict, List, Optional, Union

from config.errors import ConfigFileError
from config.models import ConfigWriteResult

JsonValue = Union[None, bool, int, float, str, List["JsonValue"], Dict[str, "JsonValue"]]
JsonObject = Dict[str, JsonValue]

_BACKUP_RETENTION = 5


def load_document(path: Path) -> JsonObject:
    if not path.exists():
        return {}
    if not path.is_file() or path.is_symlink():
        raise ConfigFileError("配置目标不是普通文件")
    try:
        with path.open("r", encoding="utf-8") as file:
            parsed = json.load(file)
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ConfigFileError("配置文件无法解析") from error
    return _validate_json_object(parsed)


def owned_object(document: JsonObject, key: str, description: str) -> JsonObject:
    existing = document.get(key)
    if existing is None:
        created: JsonObject = {}
        document[key] = created
        return created
    if not isinstance(existing, dict):
        raise ConfigFileError(f"{description}必须是对象")
    return existing


def write_document(path: Path, document: JsonObject) -> Optional[Path]:
    path.parent.mkdir(parents=True, exist_ok=True)
    backup_path = _create_backup(path)
    temporary_path = _write_temporary(path, document)
    replaced = False
    try:
        os.replace(temporary_path, path)
        replaced = True
        os.chmod(path, 0o600)
        if load_document(path) != document:
            raise ConfigFileError("配置文件写入后验证失败")
        _prune_backups(path)
        return backup_path
    except (OSError, ConfigFileError) as error:
        if replaced:
            _restore_original(path, backup_path)
        raise ConfigFileError("配置文件原子写入失败") from error
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


def rollback_write(result: ConfigWriteResult) -> None:
    rollback_path(result.target_path, result.backup_path)


def rollback_path(target_path: Path, backup_path: Optional[Path]) -> None:
    try:
        if backup_path is None:
            target_path.unlink(missing_ok=True)
            return
        if not backup_path.is_file() or backup_path.is_symlink():
            raise ConfigFileError("配置回滚备份无效")
        shutil.copy2(backup_path, target_path)
        os.chmod(target_path, 0o600)
        load_document(target_path)
    except OSError as error:
        raise ConfigFileError("配置文件跨目标回滚失败") from error


def _validate_json_object(value: object) -> JsonObject:
    if not isinstance(value, dict):
        raise ConfigFileError("配置文件根节点必须是对象")
    validated: JsonObject = {}
    for key, child in value.items():
        if not isinstance(key, str):
            raise ConfigFileError("配置对象键名必须是字符串")
        validated[key] = _validate_json_value(child)
    return validated


def _validate_json_value(value: object) -> JsonValue:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, list):
        return [_validate_json_value(child) for child in value]
    if isinstance(value, dict):
        return _validate_json_object(value)
    raise ConfigFileError("配置文件包含不支持的 JSON 值")


def _create_backup(path: Path) -> Optional[Path]:
    if not path.exists():
        return None
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    backup_path = path.with_name(f"{path.name}.bak.{timestamp}")
    try:
        shutil.copy2(path, backup_path)
        os.chmod(backup_path, 0o600)
    except OSError as error:
        raise ConfigFileError("配置备份创建失败") from error
    return backup_path


def _write_temporary(path: Path, document: JsonObject) -> Path:
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary_path = Path(temporary_name)
    try:
        os.chmod(temporary_path, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as file:
            json.dump(document, file, ensure_ascii=False, indent=2)
            file.write("\n")
            file.flush()
            os.fsync(file.fileno())
    except (OSError, TypeError, ValueError) as error:
        temporary_path.unlink(missing_ok=True)
        raise ConfigFileError("临时配置写入失败") from error
    return temporary_path


def _restore_original(path: Path, backup_path: Optional[Path]) -> None:
    try:
        if backup_path is None:
            path.unlink(missing_ok=True)
            return
        shutil.copy2(backup_path, path)
        os.chmod(path, 0o600)
    except OSError as error:
        raise ConfigFileError("配置文件恢复失败") from error


def _prune_backups(path: Path) -> None:
    backups = sorted(path.parent.glob(f"{path.name}.bak.*"), reverse=True)
    for expired_backup in backups[_BACKUP_RETENTION:]:
        try:
            if expired_backup.is_file() and not expired_backup.is_symlink():
                expired_backup.unlink()
        except OSError as error:
            raise ConfigFileError("过期配置备份清理失败") from error
