import copy
import re
from typing import List, Optional

from pydantic import SecretStr

from config._json_file import JsonObject, JsonValue, load_document, owned_object, write_document
from config.errors import ConfigConflictError, ConfigFileError
from config.models import ConfigWriteResult, ModelSyncResult, OpenCodeConfigPaths, OpenCodeModel

_API_KEY_PATTERN = re.compile(r"^sk-[A-Za-z0-9]{64}$")
_PROVIDER_PATTERN = re.compile(r"^opencode-go([2-9][0-9]*)$")
_BASE_URL = "https://opencode.ai/zen/go/v1"
_NPM_PACKAGE = "@ai-sdk/openai-compatible"


class OpenCodeConfigWriter:
    """
    OpenCode 账号配置安全写入适配器
    """

    def __init__(self, paths: OpenCodeConfigPaths) -> None:
        """
        初始化已验证配置路径

        :param paths (OpenCodeConfigPaths): 三个配置文件的规范路径
        """

        self._paths = paths

    def add_primary_account(self, api_key: SecretStr) -> ConfigWriteResult:
        """
        将第一个账号写入 auth.json

        :param api_key (SecretStr): 已验证 OpenCode API Key

        :return ConfigWriteResult: 配置目标与备份信息

        :raises ConfigConflictError: opencode-go 已存在
        :raises ConfigFileError: 密钥或配置文件无效
        """

        key_value = _validated_api_key(api_key)
        document = load_document(self._paths.auth_path)
        if "opencode-go" in document:
            raise ConfigConflictError("auth.json 已存在 opencode-go")
        document["opencode-go"] = {"type": "api", "key": key_value}
        backup_path = write_document(self._paths.auth_path, document)
        return ConfigWriteResult(
            target_path=self._paths.auth_path,
            backup_path=backup_path,
            provider_name="opencode-go",
        )

    def has_primary_account(self) -> bool:
        """
        判断 auth.json 是否已配置首账号 provider

        :return bool: 已存在有效对象形状的 opencode-go 时返回真

        :raises ConfigFileError: auth.json 或首账号 provider 结构无效
        """

        document = load_document(self._paths.auth_path)
        primary_provider = document.get("opencode-go")
        if primary_provider is None:
            return False
        _validate_primary_provider(primary_provider)
        return True

    def replace_or_remove_primary_account(self, api_key: Optional[SecretStr]) -> ConfigWriteResult:
        """
        删除首账号或使用递补账号 API Key 原子替换首账号

        :param api_key (SecretStr): 递补账号 API Key；空值表示不再保留首账号

        :return ConfigWriteResult: auth.json 写入与备份结果

        :raises ConfigFileError: API Key 或 auth.json 结构无效
        """

        document = load_document(self._paths.auth_path)
        existing = document.get("opencode-go")
        if existing is not None:
            _validate_primary_provider(existing)
        if api_key is None:
            if existing is None:
                return ConfigWriteResult(
                    target_path=self._paths.auth_path,
                    provider_name="opencode-go",
                    changed=False,
                )
            del document["opencode-go"]
        else:
            document["opencode-go"] = {"type": "api", "key": _validated_api_key(api_key)}
        backup_path = write_document(self._paths.auth_path, document)
        return ConfigWriteResult(
            target_path=self._paths.auth_path,
            backup_path=backup_path,
            provider_name="opencode-go",
        )

    def add_secondary_account(
        self,
        api_key: SecretStr,
        models: List[OpenCodeModel],
        expected_provider_name: Optional[str] = None,
    ) -> ConfigWriteResult:
        """
        自动分配序号并使用官方模型列表写入二级账号

        :param api_key (SecretStr): 已验证 OpenCode API Key
        :param models (List): 当前官方 OpenCode Go 模型列表
        :param expected_provider_name (str): 可选的预分配 provider 名称

        :return ConfigWriteResult: 配置目标与备份信息

        :raises ConfigConflictError: 首账号不存在或配置包含禁止的 opencode-go
        :raises ConfigFileError: 参数或配置结构无效
        """

        key_value = _validated_api_key(api_key)
        auth_document = load_document(self._paths.auth_path)
        primary_provider = auth_document.get("opencode-go")
        if primary_provider is None:
            raise ConfigConflictError("auth.json 缺少首账号 opencode-go")
        _validate_primary_provider(primary_provider)
        document = load_document(self._paths.opencode_path)
        providers = owned_object(document, "provider", "provider 配置")
        if "opencode-go" in providers:
            raise ConfigConflictError("opencode.json 不允许包含 opencode-go")
        account_number = _next_secondary_account_number(providers)
        provider_name = f"opencode-go{account_number}"
        if expected_provider_name is not None and provider_name != expected_provider_name:
            raise ConfigConflictError("OpenCode provider 分配与账号库记录不一致")
        _sync_provider_documents(providers, models)
        providers[provider_name] = {
            "name": f"OpenCode Go (Account {account_number})",
            "npm": _NPM_PACKAGE,
            "options": {"apiKey": key_value, "baseURL": _BASE_URL},
            "models": _model_document(models, _existing_models(providers)),
        }
        backup_path = write_document(self._paths.opencode_path, document)
        return ConfigWriteResult(
            target_path=self._paths.opencode_path,
            backup_path=backup_path,
            provider_name=provider_name,
        )

    def next_secondary_provider_name(self, reserved_provider_names: List[str]) -> str:
        """
        根据现有配置与账号库保留名称计算下一个二级 provider

        :param reserved_provider_names (List): 账号库已经分配的 provider 名称

        :return str: 不与配置或账号库冲突的二级 provider 名称

        :raises ConfigFileError: opencode.json 结构或保留名称无效
        """

        document = load_document(self._paths.opencode_path)
        providers = owned_object(document, "provider", "provider 配置")
        if "opencode-go" in providers:
            raise ConfigConflictError("opencode.json 不允许包含 opencode-go")
        reserved = dict(providers)
        for provider_name in reserved_provider_names:
            if provider_name == "opencode-go":
                continue
            if _PROVIDER_PATTERN.fullmatch(provider_name) is None:
                raise ConfigFileError("账号库包含无效 OpenCode provider 名称")
            reserved.setdefault(provider_name, {})
        return f"opencode-go{_next_secondary_account_number(reserved)}"

    def sync_secondary_models(self, models: List[OpenCodeModel]) -> ModelSyncResult:
        """
        将现有二级账号模型集合与官方目录同步

        保留仍然可用模型的已有名称和扩展元数据，添加新模型并删除官方已移除模型

        :param models (List): 当前官方 OpenCode Go 模型列表

        :return ModelSyncResult: 已同步的 provider 和备份信息

        :raises ConfigFileError: 官方列表为空或现有 provider 结构无效
        """

        _validate_models(models)
        document = load_document(self._paths.opencode_path)
        providers = owned_object(document, "provider", "provider 配置")
        updated_providers = _sync_provider_documents(providers, models)
        if not updated_providers:
            return ModelSyncResult(
                target_path=self._paths.opencode_path,
                backup_path=None,
                updated_providers=[],
            )
        backup_path = write_document(self._paths.opencode_path, document)
        return ModelSyncResult(
            target_path=self._paths.opencode_path,
            backup_path=backup_path,
            updated_providers=updated_providers,
        )

    def configured_provider_names(self) -> List[str]:
        """
        返回三个配置文件中实际存在的 OpenCode Go 账号 provider

        :return List: 按账号编号排列的 provider 名称

        :raises ConfigFileError: auth.json 或 opencode.json 结构无效
        """

        provider_names: List[str] = []
        auth_document = load_document(self._paths.auth_path)
        primary_provider = auth_document.get("opencode-go")
        if primary_provider is not None:
            _validate_primary_provider(primary_provider)
            provider_names.append("opencode-go")
        document = load_document(self._paths.opencode_path)
        providers = owned_object(document, "provider", "provider 配置")
        secondary_names = [name for name in providers if _PROVIDER_PATTERN.fullmatch(name) is not None]
        secondary_names.sort(key=lambda name: int(name.removeprefix("opencode-go")))
        provider_names.extend(secondary_names)
        return provider_names

    def remove_secondary_account(self, provider_name: str) -> ConfigWriteResult:
        """
        从 opencode.json 移除指定二级账号 provider

        :param provider_name (str): 二级 OpenCode Go provider 名称

        :return ConfigWriteResult: opencode.json 写入与备份结果

        :raises ConfigFileError: provider 名称或配置结构无效
        """

        if _PROVIDER_PATTERN.fullmatch(provider_name) is None:
            raise ConfigFileError("只能从 opencode.json 移除二级 OpenCode Go provider")
        document = load_document(self._paths.opencode_path)
        providers = owned_object(document, "provider", "provider 配置")
        if provider_name not in providers:
            return ConfigWriteResult(
                target_path=self._paths.opencode_path,
                provider_name=provider_name,
                changed=False,
            )
        del providers[provider_name]
        backup_path = write_document(self._paths.opencode_path, document)
        return ConfigWriteResult(
            target_path=self._paths.opencode_path,
            backup_path=backup_path,
            provider_name=provider_name,
        )


def _validated_api_key(api_key: SecretStr) -> str:
    value = api_key.get_secret_value()
    if _API_KEY_PATTERN.fullmatch(value) is None:
        raise ConfigFileError("OpenCode API Key 格式无效")
    return value


def _validate_primary_provider(primary_provider: JsonValue) -> None:
    if not isinstance(primary_provider, dict):
        raise ConfigFileError("auth.json 中的 opencode-go 配置必须是对象")
    if primary_provider.get("type") != "api":
        raise ConfigFileError("auth.json 中的 opencode-go 类型必须为 api")
    key = primary_provider.get("key")
    if not isinstance(key, str) or _API_KEY_PATTERN.fullmatch(key) is None:
        raise ConfigFileError("auth.json 中的 opencode-go API Key 格式无效")


def _next_secondary_account_number(providers: JsonObject) -> int:
    account_numbers = [
        int(provider_match.group(1))
        for provider_name in providers
        if (provider_match := _PROVIDER_PATTERN.fullmatch(provider_name)) is not None
    ]
    return max(account_numbers, default=1) + 1


def _existing_models(providers: JsonObject) -> JsonObject:
    template = providers.get("opencode-go2")
    if template is None:
        return {}
    if not isinstance(template, dict):
        raise ConfigFileError("opencode-go2 配置必须是对象")
    models = template.get("models")
    if not isinstance(models, dict):
        raise ConfigFileError("opencode-go2 模型配置无效")
    return models


def _model_document(models: List[OpenCodeModel], existing_models: JsonObject) -> JsonObject:
    _validate_models(models)
    model_document: JsonObject = {}
    for model in models:
        existing = existing_models.get(model.model_id)
        if isinstance(existing, dict):
            preserved = copy.deepcopy(existing)
            preserved.setdefault("name", model.name)
        else:
            preserved = {"name": model.name}
        if model.provider_npm is None:
            preserved.pop("provider", None)
        else:
            preserved["provider"] = {"npm": model.provider_npm}
        model_document[model.model_id] = preserved
    return model_document


def _sync_provider_documents(providers: JsonObject, models: List[OpenCodeModel]) -> List[str]:
    updated_providers: List[str] = []
    for provider_name, provider_value in providers.items():
        if _PROVIDER_PATTERN.fullmatch(provider_name) is None:
            continue
        if not isinstance(provider_value, dict):
            raise ConfigFileError("OpenCode Go provider 配置必须是对象")
        current_models = provider_value.get("models")
        if not isinstance(current_models, dict):
            raise ConfigFileError("OpenCode Go provider 模型配置必须是对象")
        provider_value["models"] = _model_document(models, current_models)
        updated_providers.append(provider_name)
    return updated_providers


def _validate_models(models: List[OpenCodeModel]) -> None:
    if not models:
        raise ConfigFileError("OpenCode Go 官方模型列表为空")
    model_ids = [model.model_id for model in models]
    if len(model_ids) != len(set(model_ids)):
        raise ConfigFileError("OpenCode Go 官方模型标识重复")
