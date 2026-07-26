import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from pydantic import SecretStr

from config.errors import ConfigFileError
from config.model_catalog import MODELS_DEV_CATALOG_URL, OpenCodeGoModelClient
from config.models import OpenCodeConfigPaths
from config.omo_writer import OmoConfigWriter
from config.opencode_writer import OpenCodeConfigWriter
from config.pool_service import OpenCodePoolConfigService
from storage.models import Account, AccountStatus


def write_primary_auth(tmp_path: Path) -> None:
    """
    写入格式有效的虚构首账号配置

    :param tmp_path (Path): Pytest 临时目录

    :return None: 无返回值
    """

    (tmp_path / "auth.json").write_text(
        json.dumps({"opencode-go": {"type": "api", "key": "sk-" + "p" * 64}}),
        encoding="utf-8",
    )


def create_service(tmp_path: Path) -> OpenCodePoolConfigService:
    """
    创建使用官方契约模拟响应的号池配置服务

    :param tmp_path (Path): Pytest 临时目录

    :return OpenCodePoolConfigService: 测试号池配置服务
    """

    paths = OpenCodeConfigPaths(
        auth_path=tmp_path / "auth.json",
        opencode_path=tmp_path / "opencode.json",
        omo_path=tmp_path / "oh-my-openagent.json",
    )

    def respond(request: httpx.Request) -> httpx.Response:
        """
        返回当前官方模型目录形状

        :param request (Request): 模拟模型请求

        :return Response: 模拟官方响应
        """

        if str(request.url) == MODELS_DEV_CATALOG_URL:
            return httpx.Response(
                200,
                json={
                    "opencode-go": {
                        "id": "opencode-go",
                        "name": "OpenCode Go",
                        "npm": "@ai-sdk/openai-compatible",
                        "api": "https://opencode.ai/zen/go/v1",
                        "models": {
                            "kimi-k2.7-code": {"id": "kimi-k2.7-code", "name": "Kimi K2.7 Code"},
                            "glm-5.2": {"id": "glm-5.2", "name": "GLM-5.2"},
                        },
                    }
                },
            )
        return httpx.Response(
            200,
            json={
                "object": "list",
                "data": [
                    {
                        "id": "kimi-k2.7-code",
                        "object": "model",
                        "created": 1784900000,
                        "owned_by": "opencode",
                    },
                    {
                        "id": "glm-5.2",
                        "object": "model",
                        "created": 1784900000,
                        "owned_by": "opencode",
                    },
                ],
            },
        )

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
    model_client = OpenCodeGoModelClient(http_client)
    return OpenCodePoolConfigService(model_client, OpenCodeConfigWriter(paths), OmoConfigWriter(paths))


def account(account_id: str, provider_name: str, key_character: str) -> Account:
    """
    创建 Phase 7 配置清理使用的虚构账号

    :param account_id (str): 测试账号 UUID
    :param provider_name (str): 测试 provider 名称
    :param key_character (str): 虚构 API Key 填充字符

    :return Account: 完整虚构账号
    """

    timestamp = datetime(2026, 7, 26, tzinfo=UTC)
    return Account(
        uuid=account_id,
        github_username=f"{account_id}-user",
        github_email=f"{account_id}@example.test",
        github_password=SecretStr("Fake-Phase-Seven-GitHub-Password!"),
        github_created_at=timestamp,
        opencode_provider_name=provider_name,
        opencode_workspace_id=f"wrk_{account_id}",
        opencode_api_key=SecretStr("sk-" + key_character * 64),
        email_provider="duckmail",
        temp_email=f"{account_id}@example.test",
        status=AccountStatus.ACTIVE,
        created_at=timestamp,
        updated_at=timestamp,
    )


@pytest.mark.anyio
async def test_pool_service_fetches_models_and_updates_both_configs(tmp_path: Path) -> None:
    """
    验证新增账号自动获取官网模型并更新 OpenCode 与 OMO
    """

    (tmp_path / "oh-my-openagent.json").write_text(
        '{"agents":{"build":{"model":"other/model","fallback_models":[]}}}',
        encoding="utf-8",
    )
    write_primary_auth(tmp_path)
    service = create_service(tmp_path)

    result = await service.add_secondary_account(SecretStr("sk-" + "a" * 64))

    opencode_document = json.loads((tmp_path / "opencode.json").read_text(encoding="utf-8"))
    omo_document = json.loads((tmp_path / "oh-my-openagent.json").read_text(encoding="utf-8"))
    assert result.model_count == 2
    assert set(opencode_document["provider"]["opencode-go2"]["models"]) == {"kimi-k2.7-code", "glm-5.2"}
    assert omo_document["agents"]["build"]["fallback_models"] == ["opencode-go2/kimi-k2.7-code"]


@pytest.mark.anyio
async def test_pool_service_restores_opencode_when_omo_update_fails(tmp_path: Path) -> None:
    """
    验证 OMO 失败不会留下孤立的 OpenCode provider
    """

    original_opencode = '{"theme":"system","provider":{}}\n'
    (tmp_path / "opencode.json").write_text(original_opencode, encoding="utf-8")
    (tmp_path / "oh-my-openagent.json").write_text('{"agents":{}}\n', encoding="utf-8")
    write_primary_auth(tmp_path)
    service = create_service(tmp_path)

    with pytest.raises(ConfigFileError, match="没有可更新的 agent"):
        await service.add_secondary_account(SecretStr("sk-" + "b" * 64))

    assert json.loads((tmp_path / "opencode.json").read_text(encoding="utf-8")) == {
        "theme": "system",
        "provider": {},
    }


@pytest.mark.anyio
async def test_pool_service_appends_after_existing_secondary_account(tmp_path: Path) -> None:
    """
    验证已有二号账号时自动创建三号账号并追加 OMO 链尾
    """

    write_primary_auth(tmp_path)
    (tmp_path / "opencode.json").write_text(
        json.dumps(
            {
                "provider": {
                    "opencode-go2": {
                        "models": {"glm-5.2": {"name": "GLM 5.2"}},
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "oh-my-openagent.json").write_text(
        '{"agents":{"build":{"model":"other/model","fallback_models":["opencode-go2/glm-5.2"]}}}',
        encoding="utf-8",
    )
    service = create_service(tmp_path)

    result = await service.add_secondary_account(SecretStr("sk-" + "c" * 64))

    opencode_document = json.loads((tmp_path / "opencode.json").read_text(encoding="utf-8"))
    omo_document = json.loads((tmp_path / "oh-my-openagent.json").read_text(encoding="utf-8"))
    assert result.provider_name == "opencode-go3"
    assert set(opencode_document["provider"]) == {"opencode-go2", "opencode-go3"}
    assert omo_document["agents"]["build"]["fallback_models"] == [
        "opencode-go2/glm-5.2",
        "opencode-go3/glm-5.2",
    ]


@pytest.mark.anyio
async def test_pool_service_refreshes_existing_provider_models(tmp_path: Path) -> None:
    """
    验证显式刷新从官网同步新增和已移除模型
    """

    (tmp_path / "opencode.json").write_text(
        json.dumps(
            {
                "provider": {
                    "opencode-go2": {
                        "models": {
                            "kimi-k2.7-code": {"name": "Kimi K2.7 Code"},
                            "removed-model": {"name": "Removed"},
                        }
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    service = create_service(tmp_path)

    result = await service.refresh_models()

    document = json.loads((tmp_path / "opencode.json").read_text(encoding="utf-8"))
    assert result.opencode_result.updated_providers == ["opencode-go2"]
    assert document["provider"]["opencode-go2"]["models"] == {
        "kimi-k2.7-code": {"name": "Kimi K2.7 Code"},
        "glm-5.2": {"name": "GLM-5.2"},
    }


@pytest.mark.anyio
async def test_pool_refresh_rolls_back_when_omo_primary_model_was_removed(tmp_path: Path) -> None:
    """
    验证 OMO 主模型冲突会回滚同次 OpenCode 模型同步
    """

    original_opencode = {
        "provider": {
            "opencode-go2": {
                "models": {
                    "kimi-k2.7-code": {"name": "Kimi K2.7 Code"},
                    "legacy-model": {"name": "Legacy"},
                }
            }
        }
    }
    (tmp_path / "opencode.json").write_text(json.dumps(original_opencode), encoding="utf-8")
    (tmp_path / "oh-my-openagent.json").write_text(
        '{"agents":{"build":{"model":"opencode-go/removed-model"}}}',
        encoding="utf-8",
    )
    service = create_service(tmp_path)

    with pytest.raises(ConfigFileError, match="主模型已从"):
        await service.refresh_models()

    assert json.loads((tmp_path / "opencode.json").read_text(encoding="utf-8")) == original_opencode


@pytest.mark.anyio
async def test_remove_primary_promotes_lowest_secondary_account(tmp_path: Path) -> None:
    """
    验证删除首账号时最低编号二级账号递补并清除重复 fallback
    """

    write_primary_auth(tmp_path)
    (tmp_path / "opencode.json").write_text(
        json.dumps(
            {
                "provider": {
                    "opencode-go2": {"models": {"kimi-k2.7-code": {"name": "Kimi"}}},
                    "opencode-go3": {"models": {"kimi-k2.7-code": {"name": "Kimi"}}},
                }
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "oh-my-openagent.json").write_text(
        json.dumps(
            {
                "agents": {
                    "build": {
                        "model": "opencode-go/kimi-k2.7-code",
                        "fallback_models": [
                            "opencode-go2/kimi-k2.7-code",
                            "opencode-go3/kimi-k2.7-code",
                        ],
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    service = create_service(tmp_path)
    primary = account("primary", "opencode-go", "p")
    secondary_two = account("secondary-two", "opencode-go2", "s")
    secondary_three = account("secondary-three", "opencode-go3", "t")

    result = await service.remove_account(primary, [secondary_three, secondary_two])

    auth_document = json.loads((tmp_path / "auth.json").read_text(encoding="utf-8"))
    opencode_document = json.loads((tmp_path / "opencode.json").read_text(encoding="utf-8"))
    omo_document = json.loads((tmp_path / "oh-my-openagent.json").read_text(encoding="utf-8"))
    assert auth_document["opencode-go"]["key"] == "sk-" + "s" * 64
    assert set(opencode_document["provider"]) == {"opencode-go3"}
    assert omo_document["agents"]["build"]["fallback_models"] == ["opencode-go3/kimi-k2.7-code"]
    assert result.promoted_account_id == "secondary-two"
    assert result.promoted_provider_name == "opencode-go2"


@pytest.mark.anyio
async def test_remove_primary_rolls_back_auth_and_provider_when_omo_fails(tmp_path: Path) -> None:
    """
    验证首账号递补过程中 OMO 结构错误会恢复已修改配置
    """

    write_primary_auth(tmp_path)
    original_auth = (tmp_path / "auth.json").read_text(encoding="utf-8")
    original_opencode = {"provider": {"opencode-go2": {"models": {"kimi-k2.7-code": {"name": "Kimi"}}}}}
    (tmp_path / "opencode.json").write_text(json.dumps(original_opencode), encoding="utf-8")
    (tmp_path / "oh-my-openagent.json").write_text('{"agents":{"build":"invalid"}}', encoding="utf-8")
    service = create_service(tmp_path)

    with pytest.raises(ConfigFileError):
        await service.remove_account(
            account("primary", "opencode-go", "p"),
            [account("secondary-two", "opencode-go2", "s")],
        )

    assert (tmp_path / "auth.json").read_text(encoding="utf-8") == original_auth
    assert json.loads((tmp_path / "opencode.json").read_text(encoding="utf-8")) == original_opencode


@pytest.mark.anyio
async def test_remove_unconfigured_account_does_not_create_configuration_files(tmp_path: Path) -> None:
    """
    验证清理未配置账号时不会创建或修改本地配置文件
    """

    service = create_service(tmp_path)
    unconfigured = account("unconfigured", "opencode-go", "u").model_copy(
        update={"opencode_configured": False, "omo_configured": False}
    )

    result = await service.remove_account(unconfigured, [])

    assert result.writes == []
    assert not (tmp_path / "auth.json").exists()
    assert not (tmp_path / "opencode.json").exists()
    assert not (tmp_path / "oh-my-openagent.json").exists()
