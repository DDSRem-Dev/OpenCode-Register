import json
import os
from pathlib import Path
from typing import IO, NoReturn

import pytest
from pydantic import SecretStr, ValidationError

from config.errors import ConfigConflictError, ConfigFileError
from config.models import OpenCodeConfigPaths, OpenCodeModel
from config.opencode_writer import OpenCodeConfigWriter


def create_paths(tmp_path: Path) -> OpenCodeConfigPaths:
    """
    创建全部位于临时目录的配置路径

    :param tmp_path (Path): Pytest 临时目录

    :return OpenCodeConfigPaths: 已验证测试路径
    """

    return OpenCodeConfigPaths(
        auth_path=tmp_path / "data" / "auth.json",
        opencode_path=tmp_path / "config" / "opencode.json",
        omo_path=tmp_path / "config" / "oh-my-openagent.json",
    )


def fake_api_key(character: str = "a") -> SecretStr:
    """
    创建格式有效的虚构 API Key

    :param character (str): 重复填充字符

    :return SecretStr: 虚构 API Key
    """

    return SecretStr("sk-" + character * 64)


def read_json(path: Path) -> object:
    """
    读取测试生成的 JSON 文件

    :param path (Path): JSON 文件路径

    :return object: 解析后的 JSON 值
    """

    with path.open("r", encoding="utf-8") as file:
        return json.load(file)


def write_primary_account(paths: OpenCodeConfigPaths) -> None:
    """
    写入用于二级账号测试的虚构首账号

    :param paths (OpenCodeConfigPaths): 测试配置路径

    :return None: 无返回值
    """

    paths.auth_path.parent.mkdir(parents=True, exist_ok=True)
    paths.auth_path.write_text(
        json.dumps({"opencode-go": {"type": "api", "key": "sk-" + "p" * 64}}),
        encoding="utf-8",
    )


def test_config_paths_reject_unowned_file_names(tmp_path: Path) -> None:
    """
    验证配置路径不能指向任意文件名
    """

    with pytest.raises(ValidationError):
        OpenCodeConfigPaths(
            auth_path=tmp_path / "other.json",
            opencode_path=tmp_path / "opencode.json",
            omo_path=tmp_path / "oh-my-openagent.json",
        )


def test_primary_account_preserves_unrelated_config_and_creates_backup(tmp_path: Path) -> None:
    """
    验证首账号只修改自有键并创建受限权限备份
    """

    paths = create_paths(tmp_path)
    paths.auth_path.parent.mkdir(parents=True)
    paths.auth_path.write_text('{"github": {"type": "oauth", "refresh": "preserve-me"}}\n', encoding="utf-8")
    writer = OpenCodeConfigWriter(paths)

    result = writer.add_primary_account(fake_api_key())

    assert read_json(paths.auth_path) == {
        "github": {"type": "oauth", "refresh": "preserve-me"},
        "opencode-go": {"type": "api", "key": "sk-" + "a" * 64},
    }
    assert result.backup_path is not None
    assert read_json(result.backup_path) == {"github": {"type": "oauth", "refresh": "preserve-me"}}
    assert os.stat(paths.auth_path).st_mode & 0o077 == 0
    assert os.stat(result.backup_path).st_mode & 0o077 == 0


def test_primary_account_never_overwrites_existing_provider(tmp_path: Path) -> None:
    """
    验证已存在的首账号不会被静默覆盖
    """

    paths = create_paths(tmp_path)
    paths.auth_path.parent.mkdir(parents=True)
    original = '{"opencode-go": {"type": "api", "key": "existing-fake-value"}}\n'
    paths.auth_path.write_text(original, encoding="utf-8")

    with pytest.raises(ConfigConflictError):
        OpenCodeConfigWriter(paths).add_primary_account(fake_api_key())

    assert paths.auth_path.read_text(encoding="utf-8") == original
    assert list(paths.auth_path.parent.glob("auth.json.bak.*")) == []


def test_secondary_account_uses_numbered_provider_and_typed_models(tmp_path: Path) -> None:
    """
    验证二级账号命名、固定端点和类型化模型写入
    """

    paths = create_paths(tmp_path)
    write_primary_account(paths)
    paths.opencode_path.parent.mkdir(parents=True)
    paths.opencode_path.write_text('{"theme": "system", "provider": {"custom": {"name": "Keep"}}}\n', encoding="utf-8")
    models = [OpenCodeModel(model_id="kimi-k2.7-code", name="Kimi K2.7 Code")]

    result = OpenCodeConfigWriter(paths).add_secondary_account(fake_api_key("b"), models)

    document = read_json(paths.opencode_path)
    assert isinstance(document, dict)
    assert document["theme"] == "system"
    assert document["provider"]["custom"] == {"name": "Keep"}
    assert document["provider"]["opencode-go2"] == {
        "name": "OpenCode Go (Account 2)",
        "npm": "@ai-sdk/openai-compatible",
        "options": {
            "apiKey": "sk-" + "b" * 64,
            "baseURL": "https://opencode.ai/zen/go/v1",
        },
        "models": {"kimi-k2.7-code": {"name": "Kimi K2.7 Code"}},
    }
    assert "opencode-go" not in document["provider"]
    assert result.provider_name == "opencode-go2"


def test_later_account_syncs_official_models_and_preserves_existing_metadata(tmp_path: Path) -> None:
    """
    验证后续账号完整复制现有二号账号模型配置
    """

    paths = create_paths(tmp_path)
    write_primary_account(paths)
    paths.opencode_path.parent.mkdir(parents=True)
    paths.opencode_path.write_text(
        json.dumps(
            {
                "provider": {
                    "opencode-go2": {
                        "models": {
                            "model-one": {"name": "Model One", "limit": {"context": 128000}},
                        }
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    OpenCodeConfigWriter(paths).add_secondary_account(
        fake_api_key("c"),
        [
            OpenCodeModel(model_id="model-one", name="model-one"),
            OpenCodeModel(model_id="model-two", name="model-two"),
        ],
    )

    document = read_json(paths.opencode_path)
    assert isinstance(document, dict)
    assert document["provider"]["opencode-go3"]["models"] == {
        "model-one": {"name": "Model One", "limit": {"context": 128000}},
        "model-two": {"name": "model-two"},
    }
    assert document["provider"]["opencode-go2"]["models"] == {
        "model-one": {"name": "Model One", "limit": {"context": 128000}},
        "model-two": {"name": "model-two"},
    }


def test_model_sync_applies_and_removes_provider_overrides(tmp_path: Path) -> None:
    """
    验证模型同步按结构化目录纠正模型级 SDK override
    """

    paths = create_paths(tmp_path)
    write_primary_account(paths)
    paths.opencode_path.parent.mkdir(parents=True)
    paths.opencode_path.write_text(
        json.dumps(
            {
                "provider": {
                    "opencode-go2": {
                        "models": {
                            "openai-model": {
                                "name": "Preserved Name",
                                "provider": {"npm": "@ai-sdk/anthropic"},
                                "limit": {"context": 128000},
                            },
                            "anthropic-model": {"name": "Anthropic Model"},
                        }
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    OpenCodeConfigWriter(paths).sync_secondary_models(
        [
            OpenCodeModel(model_id="openai-model", name="OpenAI Model"),
            OpenCodeModel(
                model_id="anthropic-model",
                name="Anthropic Model",
                provider_npm="@ai-sdk/anthropic",
            ),
        ]
    )

    document = read_json(paths.opencode_path)
    assert isinstance(document, dict)
    assert document["provider"]["opencode-go2"]["models"] == {
        "openai-model": {"name": "Preserved Name", "limit": {"context": 128000}},
        "anthropic-model": {
            "name": "Anthropic Model",
            "provider": {"npm": "@ai-sdk/anthropic"},
        },
    }


def test_secondary_account_does_not_reuse_numbering_gap(tmp_path: Path) -> None:
    """
    验证自动编号使用最大序号加一而不复用历史缺口
    """

    paths = create_paths(tmp_path)
    write_primary_account(paths)
    paths.opencode_path.parent.mkdir(parents=True)
    paths.opencode_path.write_text(
        json.dumps(
            {
                "provider": {
                    "opencode-go2": {"models": {"model-one": {"name": "Model One"}}},
                    "opencode-go4": {"models": {"model-one": {"name": "Model One"}}},
                }
            }
        ),
        encoding="utf-8",
    )

    result = OpenCodeConfigWriter(paths).add_secondary_account(
        fake_api_key("d"),
        [OpenCodeModel(model_id="model-one", name="Model One")],
    )

    document = read_json(paths.opencode_path)
    assert isinstance(document, dict)
    assert result.provider_name == "opencode-go5"
    assert set(document["provider"]) == {"opencode-go2", "opencode-go4", "opencode-go5"}


def test_secondary_account_rejects_forbidden_primary_provider(tmp_path: Path) -> None:
    """
    验证 opencode.json 中出现首账号名称时停止写入
    """

    paths = create_paths(tmp_path)
    write_primary_account(paths)
    paths.opencode_path.parent.mkdir(parents=True)
    original = '{"provider": {"opencode-go": {"name": "conflict"}}}\n'
    paths.opencode_path.write_text(original, encoding="utf-8")

    with pytest.raises(ConfigConflictError):
        OpenCodeConfigWriter(paths).add_secondary_account(
            fake_api_key(),
            [OpenCodeModel(model_id="model-one", name="Model One")],
        )

    assert paths.opencode_path.read_text(encoding="utf-8") == original


def test_secondary_account_requires_primary_account(tmp_path: Path) -> None:
    """
    验证缺少首账号时不会创建二级 provider
    """

    paths = create_paths(tmp_path)

    with pytest.raises(ConfigConflictError, match="缺少首账号"):
        OpenCodeConfigWriter(paths).add_secondary_account(
            fake_api_key(),
            [OpenCodeModel(model_id="model-one", name="Model One")],
        )

    assert not paths.opencode_path.exists()


def test_remove_secondary_account_preserves_unrelated_providers(tmp_path: Path) -> None:
    """
    验证二级账号清理只移除目标 provider
    """

    paths = create_paths(tmp_path)
    paths.opencode_path.parent.mkdir(parents=True)
    paths.opencode_path.write_text(
        json.dumps(
            {
                "theme": "system",
                "provider": {
                    "custom": {"name": "Keep"},
                    "opencode-go2": {"models": {"model-one": {"name": "Model One"}}},
                    "opencode-go3": {"models": {"model-one": {"name": "Model One"}}},
                },
            }
        ),
        encoding="utf-8",
    )

    result = OpenCodeConfigWriter(paths).remove_secondary_account("opencode-go2")

    document = read_json(paths.opencode_path)
    assert isinstance(document, dict)
    assert set(document["provider"]) == {"custom", "opencode-go3"}
    assert document["theme"] == "system"
    assert result.changed is True
    assert result.backup_path is not None


def test_replace_or_remove_primary_account_preserves_other_auth(tmp_path: Path) -> None:
    """
    验证首账号递补和末账号移除均保留其他认证配置
    """

    paths = create_paths(tmp_path)
    paths.auth_path.parent.mkdir(parents=True)
    paths.auth_path.write_text(
        json.dumps(
            {
                "github": {"type": "oauth", "refresh": "preserve"},
                "opencode-go": {"type": "api", "key": "sk-" + "a" * 64},
            }
        ),
        encoding="utf-8",
    )
    writer = OpenCodeConfigWriter(paths)

    writer.replace_or_remove_primary_account(fake_api_key("b"))
    replaced = read_json(paths.auth_path)
    assert isinstance(replaced, dict)
    assert replaced["opencode-go"]["key"] == "sk-" + "b" * 64

    writer.replace_or_remove_primary_account(None)
    removed = read_json(paths.auth_path)
    assert removed == {"github": {"type": "oauth", "refresh": "preserve"}}


@pytest.mark.parametrize(
    ("primary_provider", "message"),
    [
        ({"type": "oauth", "key": "sk-" + "p" * 64}, "类型必须为 api"),
        ({"type": "api"}, "API Key 格式无效"),
        ({"type": "api", "key": "invalid"}, "API Key 格式无效"),
    ],
)
def test_secondary_account_rejects_invalid_primary_provider(
    tmp_path: Path,
    primary_provider: object,
    message: str,
) -> None:
    """
    验证畸形首账号配置不会扩散到二级 provider
    """

    paths = create_paths(tmp_path)
    paths.auth_path.parent.mkdir(parents=True)
    paths.auth_path.write_text(json.dumps({"opencode-go": primary_provider}), encoding="utf-8")
    paths.opencode_path.parent.mkdir(parents=True)
    original = '{"theme":"system","provider":{}}\n'
    paths.opencode_path.write_text(original, encoding="utf-8")

    with pytest.raises(ConfigFileError, match=message):
        OpenCodeConfigWriter(paths).add_secondary_account(
            fake_api_key(),
            [OpenCodeModel(model_id="model-one", name="Model One")],
        )

    assert paths.opencode_path.read_text(encoding="utf-8") == original


def test_atomic_replace_failure_keeps_original_and_removes_temporary_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    验证原子替换失败时原配置保持不变且临时文件被清理
    """

    paths = create_paths(tmp_path)
    paths.auth_path.parent.mkdir(parents=True)
    original = '{"preserved": true}\n'
    paths.auth_path.write_text(original, encoding="utf-8")

    def fail_replace(source: Path, target: Path) -> NoReturn:
        raise OSError("simulated replace failure")

    monkeypatch.setattr("config._json_file.os.replace", fail_replace)

    with pytest.raises(ConfigFileError):
        OpenCodeConfigWriter(paths).add_primary_account(fake_api_key())

    assert paths.auth_path.read_text(encoding="utf-8") == original
    assert list(paths.auth_path.parent.glob(".auth.json.*.tmp")) == []


def test_post_write_validation_failure_restores_backup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    验证替换后回读内容不一致时自动恢复原配置
    """

    paths = create_paths(tmp_path)
    paths.auth_path.parent.mkdir(parents=True)
    original = '{"preserved": true}\n'
    paths.auth_path.write_text(original, encoding="utf-8")
    original_json_load = json.load
    load_count = 0

    def inconsistent_load(file: IO[str]) -> object:
        nonlocal load_count
        load_count += 1
        if load_count == 2:
            return {}
        return original_json_load(file)

    monkeypatch.setattr("config._json_file.json.load", inconsistent_load)

    with pytest.raises(ConfigFileError):
        OpenCodeConfigWriter(paths).add_primary_account(fake_api_key())

    assert paths.auth_path.read_text(encoding="utf-8") == original


def test_successful_write_prunes_owned_backups_to_retention_limit(tmp_path: Path) -> None:
    """
    验证成功写入后仅保留最近五份自有备份
    """

    paths = create_paths(tmp_path)
    paths.auth_path.parent.mkdir(parents=True)
    paths.auth_path.write_text('{"preserved": true}\n', encoding="utf-8")
    for index in range(7):
        (paths.auth_path.parent / f"auth.json.bak.20260101T00000000000{index}Z").write_text("{}\n", encoding="utf-8")

    OpenCodeConfigWriter(paths).add_primary_account(fake_api_key())

    backups = list(paths.auth_path.parent.glob("auth.json.bak.*"))
    assert len(backups) == 5
