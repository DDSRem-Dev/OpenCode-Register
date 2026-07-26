import json
from pathlib import Path
from typing import List

import pytest

from config.errors import ConfigConflictError, ConfigFileError
from config.models import OpenCodeConfigPaths, OpenCodeModel
from config.omo_writer import OmoConfigWriter


def create_paths(tmp_path: Path) -> OpenCodeConfigPaths:
    """
    创建 OMO 写入测试路径

    :param tmp_path (Path): Pytest 临时目录

    :return OpenCodeConfigPaths: 已验证测试路径
    """

    return OpenCodeConfigPaths(
        auth_path=tmp_path / "auth.json",
        opencode_path=tmp_path / "opencode.json",
        omo_path=tmp_path / "oh-my-openagent.json",
    )


def official_models() -> List[OpenCodeModel]:
    """
    创建包含架构默认项的官方模型测试列表

    :return List: 测试模型列表
    """

    return [
        OpenCodeModel(model_id="kimi-k2.7-code", name="kimi-k2.7-code"),
        OpenCodeModel(model_id="glm-5.2", name="glm-5.2"),
    ]


def write_omo(path: Path, document: object) -> None:
    """
    写入临时 OMO 配置

    :param path (Path): OMO 配置路径
    :param document (object): 测试 JSON 值

    :return None: 无返回值
    """

    path.write_text(json.dumps(document), encoding="utf-8")


def read_omo(path: Path) -> object:
    """
    读取临时 OMO 配置

    :param path (Path): OMO 配置路径

    :return object: 解析后的配置
    """

    return json.loads(path.read_text(encoding="utf-8"))


def test_omo_appends_new_account_to_each_agent_chain_end(tmp_path: Path) -> None:
    """
    验证新账号继承 agent 的 Go 模型并追加到链末尾
    """

    paths = create_paths(tmp_path)
    write_omo(
        paths.omo_path,
        {
            "agents": {
                "build": {
                    "model": "anthropic/claude",
                    "fallback_models": ["opencode-go/kimi-k2.7-code", "other/model"],
                },
                "plan": {
                    "model": "opencode-go2/glm-5.2",
                    "fallback_models": [],
                },
            },
            "unrelated": {"preserve": True},
        },
    )

    result = OmoConfigWriter(paths).append_account_fallback("opencode-go3", official_models())

    document = read_omo(paths.omo_path)
    assert isinstance(document, dict)
    assert document["agents"]["build"]["fallback_models"] == [
        "opencode-go/kimi-k2.7-code",
        "other/model",
        "opencode-go3/kimi-k2.7-code",
    ]
    assert document["agents"]["plan"]["fallback_models"] == ["opencode-go3/glm-5.2"]
    assert document["model_fallback"] is True
    assert document["runtime_fallback"] == {
        "enabled": True,
        "max_fallback_attempts": 3,
        "retry_on_errors": [429, 500, 502, 503, 504],
    }
    assert document["unrelated"] == {"preserve": True}
    assert result.backup_path is not None


def test_omo_initializes_minimal_build_agent_when_file_is_missing(tmp_path: Path) -> None:
    """
    验证全新配置创建最小 build agent 并接入首、二账号
    """

    paths = create_paths(tmp_path)

    result = OmoConfigWriter(paths).append_account_fallback("opencode-go2", official_models())

    document = read_omo(paths.omo_path)
    assert isinstance(document, dict)
    assert document["agents"] == {
        "build": {
            "model": "opencode-go/kimi-k2.7-code",
            "fallback_models": ["opencode-go2/kimi-k2.7-code"],
        }
    }
    assert document["model_fallback"] is True
    assert document["runtime_fallback"] == {
        "enabled": True,
        "max_fallback_attempts": 3,
        "retry_on_errors": [429, 500, 502, 503, 504],
    }
    assert result.backup_path is None


def test_omo_rejects_existing_empty_agents_without_modifying_file(tmp_path: Path) -> None:
    """
    验证已有配置显式使用空 agents 时不擅自创建默认 agent
    """

    paths = create_paths(tmp_path)
    original = '{"agents":{}}'
    paths.omo_path.write_text(original, encoding="utf-8")

    with pytest.raises(ConfigFileError, match="没有可更新"):
        OmoConfigWriter(paths).append_account_fallback("opencode-go2", official_models())

    assert paths.omo_path.read_text(encoding="utf-8") == original


def test_omo_write_is_idempotent_and_preserves_runtime_preferences(tmp_path: Path) -> None:
    """
    验证重复写入不重复 fallback 且保留用户运行时偏好
    """

    paths = create_paths(tmp_path)
    write_omo(
        paths.omo_path,
        {
            "agents": {
                "build": {
                    "model": "opencode-go/kimi-k2.7-code",
                    "fallback_models": ["opencode-go2/kimi-k2.7-code"],
                }
            },
            "model_fallback": True,
            "runtime_fallback": {
                "enabled": True,
                "max_fallback_attempts": 8,
                "retry_on_errors": [429],
            },
        },
    )

    result = OmoConfigWriter(paths).append_account_fallback("opencode-go2", official_models())

    document = read_omo(paths.omo_path)
    assert isinstance(document, dict)
    assert document["agents"]["build"]["fallback_models"] == ["opencode-go2/kimi-k2.7-code"]
    assert document["runtime_fallback"]["max_fallback_attempts"] == 8
    assert document["runtime_fallback"]["retry_on_errors"] == [429]
    assert result.backup_path is None


def test_omo_rejects_removed_default_model_without_modifying_file(tmp_path: Path) -> None:
    """
    验证架构默认模型从官网下线时停止写入
    """

    paths = create_paths(tmp_path)
    original = '{"agents":{"build":{"model":"other/model"}}}'
    paths.omo_path.write_text(original, encoding="utf-8")

    with pytest.raises(ConfigFileError, match="默认 fallback 模型"):
        OmoConfigWriter(paths).append_account_fallback(
            "opencode-go2",
            [OpenCodeModel(model_id="glm-5.2", name="glm-5.2")],
        )

    assert paths.omo_path.read_text(encoding="utf-8") == original


def test_omo_rejects_same_provider_with_conflicting_model(tmp_path: Path) -> None:
    """
    验证同名 provider 的不同模型不会被静默保留或覆盖
    """

    paths = create_paths(tmp_path)
    original = {
        "agents": {
            "build": {
                "model": "opencode-go/kimi-k2.7-code",
                "fallback_models": ["opencode-go2/glm-5.2"],
            }
        }
    }
    write_omo(paths.omo_path, original)

    with pytest.raises(ConfigConflictError):
        OmoConfigWriter(paths).append_account_fallback("opencode-go2", official_models())

    assert read_omo(paths.omo_path) == original


def test_omo_rejects_invalid_existing_runtime_structure(tmp_path: Path) -> None:
    """
    验证已有运行时 fallback 类型错误时不写入配置
    """

    paths = create_paths(tmp_path)
    original = {
        "agents": {"build": {"model": "opencode-go/kimi-k2.7-code"}},
        "runtime_fallback": {"enabled": "yes"},
    }
    write_omo(paths.omo_path, original)

    with pytest.raises(ConfigFileError, match="enabled"):
        OmoConfigWriter(paths).append_account_fallback("opencode-go2", official_models())

    assert read_omo(paths.omo_path) == original


def test_omo_remove_account_cleans_primary_and_fallback_references(tmp_path: Path) -> None:
    """
    验证账号清理移除全部目标 provider 引用并保留顺序
    """

    paths = create_paths(tmp_path)
    write_omo(
        paths.omo_path,
        {
            "agents": {
                "build": {
                    "model": "opencode-go2/kimi-k2.7-code",
                    "fallback_models": [
                        "other/model",
                        "opencode-go2/glm-5.2",
                        "opencode-go3/kimi-k2.7-code",
                    ],
                },
                "plan": {
                    "model": "other/primary",
                    "fallback_models": ["opencode-go2/kimi-k2.7-code", "other/fallback"],
                },
            }
        },
    )

    result = OmoConfigWriter(paths).remove_account("opencode-go2")

    document = read_omo(paths.omo_path)
    assert isinstance(document, dict)
    assert "model" not in document["agents"]["build"]
    assert document["agents"]["build"]["fallback_models"] == [
        "other/model",
        "opencode-go3/kimi-k2.7-code",
    ]
    assert document["agents"]["plan"]["fallback_models"] == ["other/fallback"]
    assert result.changed is True


def test_omo_model_sync_removes_stale_fallback_but_preserves_order(tmp_path: Path) -> None:
    """
    验证模型同步移除已下线 Go fallback 且不重排其余项目
    """

    paths = create_paths(tmp_path)
    write_omo(
        paths.omo_path,
        {
            "agents": {
                "build": {
                    "model": "other/model",
                    "fallback_models": [
                        "opencode-go/removed-model",
                        "other/fallback",
                        "opencode-go2/kimi-k2.7-code",
                    ],
                }
            }
        },
    )

    result = OmoConfigWriter(paths).sync_official_models(official_models())

    document = read_omo(paths.omo_path)
    assert isinstance(document, dict)
    assert document["agents"]["build"]["fallback_models"] == [
        "other/fallback",
        "opencode-go2/kimi-k2.7-code",
    ]
    assert result.updated_agents == ["build"]


def test_omo_model_sync_rejects_removed_primary_model(tmp_path: Path) -> None:
    """
    验证 agent 主模型下线时不擅自选择替代项
    """

    paths = create_paths(tmp_path)
    original = {"agents": {"build": {"model": "opencode-go/removed-model"}}}
    write_omo(paths.omo_path, original)

    with pytest.raises(ConfigFileError, match="主模型已从"):
        OmoConfigWriter(paths).sync_official_models(official_models())

    assert read_omo(paths.omo_path) == original
