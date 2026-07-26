import re
from typing import List, Optional, Set, cast

from config._json_file import JsonObject, JsonValue, load_document, owned_object, write_document
from config.errors import ConfigConflictError, ConfigFileError
from config.models import ConfigWriteResult, OmoModelSyncResult, OpenCodeConfigPaths, OpenCodeModel

_GO_PROVIDER_PATTERN = re.compile(r"^opencode-go(?:[2-9][0-9]*)?$")
_SECONDARY_PROVIDER_PATTERN = re.compile(r"^opencode-go[2-9][0-9]*$")
_DEFAULT_FALLBACK_MODEL_ID = "kimi-k2.7-code"
_RETRY_ERRORS: List[JsonValue] = [429, 500, 502, 503, 504]


class OmoConfigWriter:
    """
    Oh My OpenAgent fallback 配置安全写入适配器
    """

    def __init__(self, paths: OpenCodeConfigPaths) -> None:
        """
        初始化已验证配置路径

        :param paths (OpenCodeConfigPaths): 三个配置文件的规范路径
        """

        self._paths = paths

    def append_account_fallback(
        self,
        provider_name: str,
        models: List[OpenCodeModel],
    ) -> ConfigWriteResult:
        """
        将新账号追加到每个 agent 的 fallback_models 链末尾

        每个 agent 优先沿用其现有 Go 模型；不存在时使用仍在官方目录中的架构默认模型

        :param provider_name (str): 新增二级 OpenCode Go provider 名称
        :param models (List): 当前官方 OpenCode Go 模型列表

        :return ConfigWriteResult: OMO 配置目标与备份信息

        :raises ConfigConflictError: 新 provider 已绑定其他模型
        :raises ConfigFileError: provider、模型或 OMO 配置结构无效
        """

        if _SECONDARY_PROVIDER_PATTERN.fullmatch(provider_name) is None:
            raise ConfigFileError("OMO fallback 仅接受二级 OpenCode Go provider")
        official_model_ids = {model.model_id for model in models}
        if not official_model_ids:
            raise ConfigFileError("OpenCode Go 官方模型列表为空")
        omo_exists = self._paths.omo_path.exists()
        document = load_document(self._paths.omo_path)
        agents = owned_object(document, "agents", "agents 配置")
        if not agents:
            if omo_exists:
                raise ConfigFileError("OMO 配置没有可更新的 agent")
            agents["build"] = {
                "model": f"opencode-go/{_DEFAULT_FALLBACK_MODEL_ID}",
                "fallback_models": [],
            }
        changed = False
        for agent_name, agent_value in agents.items():
            if not isinstance(agent_value, dict):
                raise ConfigFileError(f"OMO agent {agent_name} 配置必须是对象")
            fallback_models = _fallback_models(agent_value)
            _validate_primary_model(agent_value, official_model_ids)
            changed = _remove_stale_fallbacks(fallback_models, official_model_ids) or changed
            model_id = _select_agent_model(agent_value, fallback_models, official_model_ids)
            target_model = f"{provider_name}/{model_id}"
            existing_provider_model = _provider_entry(fallback_models, provider_name)
            if existing_provider_model is not None:
                if existing_provider_model != target_model:
                    raise ConfigConflictError("OMO fallback 已存在同名 provider 的其他模型")
                continue
            fallback_models.append(target_model)
            changed = True
        changed = _configure_runtime(document) or changed
        backup_path = write_document(self._paths.omo_path, document) if changed else None
        return ConfigWriteResult(
            target_path=self._paths.omo_path,
            backup_path=backup_path,
            provider_name=provider_name,
        )

    def sync_official_models(self, models: List[OpenCodeModel]) -> OmoModelSyncResult:
        """
        清理 OMO fallback 中已从官方目录下线的 Go 模型

        agent 主模型下线时停止同步，避免擅自改变用户的主模型选择

        :param models (List): 当前官方 OpenCode Go 模型列表

        :return OmoModelSyncResult: 已清理的 agent 与备份信息

        :raises ConfigFileError: 官方列表为空、主模型下线或 OMO 结构无效
        """

        official_model_ids = {model.model_id for model in models}
        if not official_model_ids:
            raise ConfigFileError("OpenCode Go 官方模型列表为空")
        if not self._paths.omo_path.exists():
            return OmoModelSyncResult(
                target_path=self._paths.omo_path,
                backup_path=None,
                updated_agents=[],
            )
        document = load_document(self._paths.omo_path)
        agents = owned_object(document, "agents", "agents 配置")
        updated_agents: List[str] = []
        for agent_name, agent_value in agents.items():
            if not isinstance(agent_value, dict):
                raise ConfigFileError(f"OMO agent {agent_name} 配置必须是对象")
            _validate_primary_model(agent_value, official_model_ids)
            fallback_models = _fallback_models(agent_value)
            if _remove_stale_fallbacks(fallback_models, official_model_ids):
                updated_agents.append(agent_name)
        backup_path = write_document(self._paths.omo_path, document) if updated_agents else None
        return OmoModelSyncResult(
            target_path=self._paths.omo_path,
            backup_path=backup_path,
            updated_agents=updated_agents,
        )

    def remove_account(self, provider_name: str) -> ConfigWriteResult:
        """
        从全部 OMO agent 主模型与 fallback 中移除账号 provider

        :param provider_name (str): 待移除 OpenCode Go provider 名称

        :return ConfigWriteResult: OMO 配置写入与备份结果

        :raises ConfigFileError: provider 名称或 OMO 配置结构无效
        """

        if _GO_PROVIDER_PATTERN.fullmatch(provider_name) is None:
            raise ConfigFileError("OMO 清理仅接受 OpenCode Go provider")
        if not self._paths.omo_path.exists():
            return ConfigWriteResult(
                target_path=self._paths.omo_path,
                provider_name=provider_name,
                changed=False,
            )
        document = load_document(self._paths.omo_path)
        agents = owned_object(document, "agents", "agents 配置")
        changed = False
        for agent_name, agent_value in agents.items():
            if not isinstance(agent_value, dict):
                raise ConfigFileError(f"OMO agent {agent_name} 配置必须是对象")
            primary_model = agent_value.get("model")
            if primary_model is not None and not isinstance(primary_model, str):
                raise ConfigFileError("OMO agent model 必须是字符串")
            if isinstance(primary_model, str) and primary_model.startswith(f"{provider_name}/"):
                del agent_value["model"]
                changed = True
            fallback_models = _fallback_models(agent_value)
            retained_models = [model for model in fallback_models if not model.startswith(f"{provider_name}/")]
            if len(retained_models) != len(fallback_models):
                fallback_models[:] = retained_models
                changed = True
        if not changed:
            return ConfigWriteResult(
                target_path=self._paths.omo_path,
                provider_name=provider_name,
                changed=False,
            )
        backup_path = write_document(self._paths.omo_path, document)
        return ConfigWriteResult(
            target_path=self._paths.omo_path,
            backup_path=backup_path,
            provider_name=provider_name,
        )


def _fallback_models(agent: JsonObject) -> List[str]:
    value = agent.get("fallback_models")
    if value is None:
        created: List[JsonValue] = []
        agent["fallback_models"] = created
        return cast(List[str], created)
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ConfigFileError("OMO fallback_models 必须是字符串列表")
    return cast(List[str], value)


def _select_agent_model(agent: JsonObject, fallback_models: List[str], official_model_ids: Set[str]) -> str:
    candidates: List[str] = []
    primary_model = agent.get("model")
    if primary_model is not None and not isinstance(primary_model, str):
        raise ConfigFileError("OMO agent model 必须是字符串")
    if isinstance(primary_model, str):
        candidates.append(primary_model)
    candidates.extend(fallback_models)
    for candidate in candidates:
        provider_name, separator, model_id = candidate.partition("/")
        if separator and _GO_PROVIDER_PATTERN.fullmatch(provider_name) is not None and model_id in official_model_ids:
            return model_id
    if _DEFAULT_FALLBACK_MODEL_ID not in official_model_ids:
        raise ConfigFileError("架构默认 fallback 模型已不在 OpenCode Go 官方目录")
    return _DEFAULT_FALLBACK_MODEL_ID


def _validate_primary_model(agent: JsonObject, official_model_ids: Set[str]) -> None:
    primary_model = agent.get("model")
    if primary_model is None:
        return
    if not isinstance(primary_model, str):
        raise ConfigFileError("OMO agent model 必须是字符串")
    go_model_id = _go_model_id(primary_model)
    if go_model_id is not None and go_model_id not in official_model_ids:
        raise ConfigFileError("OMO agent 主模型已从 OpenCode Go 官方目录下线")


def _remove_stale_fallbacks(fallback_models: List[str], official_model_ids: Set[str]) -> bool:
    valid_models = [
        model
        for model in fallback_models
        if (go_model_id := _go_model_id(model)) is None or go_model_id in official_model_ids
    ]
    if len(valid_models) == len(fallback_models):
        return False
    fallback_models[:] = valid_models
    return True


def _go_model_id(model: str) -> Optional[str]:
    provider_name, separator, model_id = model.partition("/")
    if not separator or _GO_PROVIDER_PATTERN.fullmatch(provider_name) is None:
        return None
    return model_id


def _provider_entry(fallback_models: List[str], provider_name: str) -> Optional[str]:
    matches = [model for model in fallback_models if model.startswith(f"{provider_name}/")]
    if len(matches) > 1:
        raise ConfigConflictError("OMO fallback 包含重复 provider")
    if not matches:
        return None
    return matches[0]


def _configure_runtime(document: JsonObject) -> bool:
    changed = False
    if document.get("model_fallback") is not True:
        document["model_fallback"] = True
        changed = True
    runtime = owned_object(document, "runtime_fallback", "runtime_fallback 配置")
    _validate_runtime(runtime)
    defaults: JsonObject = {
        "enabled": True,
        "max_fallback_attempts": 3,
        "retry_on_errors": _RETRY_ERRORS.copy(),
    }
    for key, value in defaults.items():
        if key not in runtime:
            runtime[key] = value
            changed = True
    return changed


def _validate_runtime(runtime: JsonObject) -> None:
    enabled = runtime.get("enabled")
    if enabled is not None and not isinstance(enabled, bool):
        raise ConfigFileError("runtime_fallback.enabled 必须是布尔值")
    attempts = runtime.get("max_fallback_attempts")
    if attempts is not None and (not isinstance(attempts, int) or isinstance(attempts, bool) or attempts < 1):
        raise ConfigFileError("runtime_fallback.max_fallback_attempts 必须是正整数")
    retry_errors = runtime.get("retry_on_errors")
    if retry_errors is not None and (
        not isinstance(retry_errors, list)
        or not all(isinstance(error_code, int) and not isinstance(error_code, bool) for error_code in retry_errors)
    ):
        raise ConfigFileError("runtime_fallback.retry_on_errors 必须是整数列表")
