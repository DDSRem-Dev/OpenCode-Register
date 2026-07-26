import json
from pathlib import Path
from typing import List, Tuple

import httpx
import pytest
from pydantic import SecretStr

from config.errors import ConfigFileError
from config.model_catalog import MODELS_DEV_CATALOG_URL, OpenCodeGoModelClient
from config.models import ConfigWriteResult, OpenCodeConfigPaths
from config.omo_writer import OmoConfigWriter
from config.opencode_writer import OpenCodeConfigWriter
from config.pool_service import OpenCodePoolConfigService
from engine.completion import AccountCompletionError, AccountCompletionService
from engine.models import AccountCompletionData, PendingAccountData
from storage.models import AccountCreate, AutomaticConfigurationSettings
from storage.repositories import AccountAlreadyExistsError
from storage.service import AccountVaultService


def _paths(tmp_path: Path) -> OpenCodeConfigPaths:
    return OpenCodeConfigPaths(
        auth_path=tmp_path / "auth.json",
        opencode_path=tmp_path / "opencode.json",
        omo_path=tmp_path / "oh-my-openagent.json",
    )


def _write_existing_auth(paths: OpenCodeConfigPaths) -> None:
    paths.auth_path.write_text(
        json.dumps({"opencode-go": {"type": "api", "key": "sk-" + "p" * 64}}) + "\n",
        encoding="utf-8",
    )


def _model_response(request: httpx.Request) -> httpx.Response:
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
                        "kimi-k2.7-code": {
                            "id": "kimi-k2.7-code",
                            "name": "Kimi K2.7 Code",
                        }
                    },
                }
            },
            request=request,
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
                }
            ],
        },
        request=request,
    )


def _completion_service(
    tmp_path: Path,
    vault: AccountVaultService,
) -> Tuple[AccountCompletionService, httpx.AsyncClient]:
    paths = _paths(tmp_path)
    client = httpx.AsyncClient(transport=httpx.MockTransport(_model_response))
    writer = OpenCodeConfigWriter(paths)
    pool_service = OpenCodePoolConfigService(
        OpenCodeGoModelClient(client),
        writer,
        OmoConfigWriter(paths),
    )
    return AccountCompletionService(vault, writer, pool_service), client


def _completion_data(suffix: str = "a") -> AccountCompletionData:
    return AccountCompletionData(
        github_username=f"completion-{suffix}",
        github_email=f"completion-{suffix}@example.test",
        github_password=SecretStr(f"GitHub-Completion-{suffix}-Password!"),
        opencode_workspace_id=f"wrk_completion_{suffix}",
        opencode_api_key=SecretStr("sk-" + suffix * 64),
        email_provider="fake",
        temp_email=f"completion-{suffix}@example.test",
    )


def _unlock(vault: AccountVaultService) -> None:
    password = SecretStr("completion service master password")
    vault.unlock(password, password)


@pytest.mark.anyio
async def test_first_account_writes_auth_and_encrypted_vault(tmp_path: Path) -> None:
    """
    验证首账号同时写入 auth.json 与加密账号库
    """

    vault = AccountVaultService(tmp_path / "accounts.db")
    _unlock(vault)
    service, client = _completion_service(tmp_path, vault)

    provider_name = await service.complete(_completion_data())
    await client.aclose()

    auth_document = json.loads(_paths(tmp_path).auth_path.read_text(encoding="utf-8"))
    assert provider_name == "opencode-go"
    assert auth_document["opencode-go"]["type"] == "api"
    assert auth_document["opencode-go"]["key"].startswith("sk-")
    assert vault.account_count() == 1
    assert vault.list_accounts()[0].opencode_provider_name == "opencode-go"


@pytest.mark.anyio
async def test_pending_account_is_promoted_without_changing_stable_identity(tmp_path: Path) -> None:
    """
    验证 GitHub 完成记录在配置成功后提升为同一稳定账号
    """

    vault = AccountVaultService(tmp_path / "accounts.db")
    _unlock(vault)
    service, client = _completion_service(tmp_path, vault)
    data = _completion_data("p")
    account_id = await service.persist_pending(
        PendingAccountData(
            github_username=data.github_username,
            github_email=data.github_email,
            github_password=data.github_password,
            email_provider=data.email_provider,
            temp_email=data.temp_email,
        )
    )

    provider_name = await service.complete(data.model_copy(update={"account_id": account_id}))
    await client.aclose()

    accounts = vault.list_accounts()
    assert len(accounts) == 1
    assert accounts[0].uuid == account_id
    assert accounts[0].opencode_provider_name == provider_name
    assert accounts[0].status.value == "active"


@pytest.mark.anyio
async def test_secondary_account_writes_pool_configs_and_encrypted_vault(tmp_path: Path) -> None:
    """
    验证二级账号同时写入 OpenCode、OMO 与加密账号库
    """

    paths = _paths(tmp_path)
    _write_existing_auth(paths)
    paths.omo_path.write_text(
        '{"agents":{"build":{"model":"other/model","fallback_models":[]}}}\n',
        encoding="utf-8",
    )
    vault = AccountVaultService(tmp_path / "accounts.db")
    _unlock(vault)
    vault.add_account(
        AccountCreate(
            github_username="existing-primary",
            github_email="existing@example.test",
            github_password=SecretStr("Existing-GitHub-Password!"),
            opencode_provider_name="opencode-go",
            opencode_workspace_id="wrk_existing",
            opencode_api_key=SecretStr("sk-" + "z" * 64),
            email_provider="fake",
            temp_email="existing@example.test",
        )
    )
    service, client = _completion_service(tmp_path, vault)

    provider_name = await service.complete(_completion_data("b"))
    await client.aclose()

    opencode_document = json.loads(paths.opencode_path.read_text(encoding="utf-8"))
    omo_document = json.loads(paths.omo_path.read_text(encoding="utf-8"))
    assert provider_name == "opencode-go2"
    assert opencode_document["provider"]["opencode-go2"]["options"]["apiKey"].startswith("sk-")
    assert omo_document["agents"]["build"]["fallback_models"] == ["opencode-go2/kimi-k2.7-code"]
    assert vault.account_count() == 2


@pytest.mark.anyio
async def test_existing_auth_uses_secondary_provider_with_empty_vault(tmp_path: Path) -> None:
    """
    验证已有 OpenCode 首账号但新账号库为空时从二号 provider 开始
    """

    paths = _paths(tmp_path)
    _write_existing_auth(paths)
    paths.omo_path.write_text(
        '{"agents":{"build":{"model":"other/model","fallback_models":[]}}}\n',
        encoding="utf-8",
    )
    vault = AccountVaultService(tmp_path / "accounts.db")
    _unlock(vault)
    service, client = _completion_service(tmp_path, vault)

    provider_name = await service.complete(_completion_data("d"))
    await client.aclose()

    assert provider_name == "opencode-go2"
    assert vault.account_count() == 1
    assert vault.list_accounts()[0].opencode_provider_name == "opencode-go2"
    assert "opencode-go2" in json.loads(paths.opencode_path.read_text(encoding="utf-8"))["provider"]


@pytest.mark.anyio
async def test_disabled_automatic_configuration_persists_account_without_writing_files(tmp_path: Path) -> None:
    """
    验证关闭自动配置后账号仍入库并记录两类待应用状态
    """

    vault = AccountVaultService(tmp_path / "accounts.db")
    _unlock(vault)
    vault.update_automatic_configuration(
        AutomaticConfigurationSettings(auto_configure_opencode=False, auto_configure_omo=False)
    )
    service, client = _completion_service(tmp_path, vault)

    provider_name = await service.complete(_completion_data("n"))
    await client.aclose()

    account = vault.list_complete_accounts()[0]
    assert provider_name == "opencode-go"
    assert account.opencode_configured is False
    assert account.omo_configured is False
    assert not _paths(tmp_path).auth_path.exists()
    assert not _paths(tmp_path).opencode_path.exists()
    assert not _paths(tmp_path).omo_path.exists()


@pytest.mark.anyio
async def test_apply_pending_configuration_writes_enabled_configs_and_updates_status(tmp_path: Path) -> None:
    """
    验证重新开启自动配置后可为已有账号补写配置
    """

    vault = AccountVaultService(tmp_path / "accounts.db")
    _unlock(vault)
    vault.update_automatic_configuration(
        AutomaticConfigurationSettings(auto_configure_opencode=False, auto_configure_omo=False)
    )
    service, client = _completion_service(tmp_path, vault)
    await service.complete(_completion_data("q"))
    vault.update_automatic_configuration(AutomaticConfigurationSettings())

    applied_count = await service.apply_pending_configuration()
    await client.aclose()

    account = vault.list_complete_accounts()[0]
    assert applied_count == 1
    assert account.opencode_configured is True
    assert account.omo_configured is True
    assert "opencode-go" in json.loads(_paths(tmp_path).auth_path.read_text(encoding="utf-8"))


@pytest.mark.anyio
async def test_secondary_account_can_skip_omo_configuration(tmp_path: Path) -> None:
    """
    验证只开启 OpenCode 自动配置时不会修改 Oh My OpenCode
    """

    paths = _paths(tmp_path)
    _write_existing_auth(paths)
    vault = AccountVaultService(tmp_path / "accounts.db")
    _unlock(vault)
    vault.add_account(
        AccountCreate(
            github_username="settings-primary",
            github_email="settings-primary@example.test",
            github_password=SecretStr("Settings-Primary-Password!"),
            opencode_provider_name="opencode-go",
            opencode_workspace_id="wrk_settings_primary",
            opencode_api_key=SecretStr("sk-" + "s" * 64),
            email_provider="fake",
            temp_email="settings-primary@example.test",
        )
    )
    vault.update_automatic_configuration(
        AutomaticConfigurationSettings(auto_configure_opencode=True, auto_configure_omo=False)
    )
    service, client = _completion_service(tmp_path, vault)

    await service.complete(_completion_data("r"))
    await client.aclose()

    account = vault.list_complete_accounts()[1]
    assert account.opencode_configured is True
    assert account.omo_configured is False
    assert paths.opencode_path.exists()
    assert not paths.omo_path.exists()


@pytest.mark.anyio
async def test_locked_vault_does_not_write_config(tmp_path: Path) -> None:
    """
    验证账号库未解锁时不会写入任何配置
    """

    vault = AccountVaultService(tmp_path / "accounts.db")
    service, client = _completion_service(tmp_path, vault)

    with pytest.raises(AccountCompletionError, match="尚未解锁"):
        await service.complete(_completion_data())
    await client.aclose()

    paths = _paths(tmp_path)
    assert not paths.auth_path.exists()
    assert not paths.opencode_path.exists()
    assert not paths.omo_path.exists()


@pytest.mark.anyio
async def test_config_failure_does_not_write_vault(tmp_path: Path) -> None:
    """
    验证配置冲突时不会新增加密账号记录
    """

    paths = _paths(tmp_path)
    original_auth = '{"opencode-go":"invalid"}\n'
    paths.auth_path.write_text(original_auth, encoding="utf-8")
    vault = AccountVaultService(tmp_path / "accounts.db")
    _unlock(vault)
    service, client = _completion_service(tmp_path, vault)

    with pytest.raises(AccountCompletionError, match="首账号配置检查失败"):
        await service.complete(_completion_data())
    await client.aclose()

    assert paths.auth_path.read_text(encoding="utf-8") == original_auth
    assert vault.account_count() == 0


@pytest.mark.anyio
async def test_vault_conflict_rolls_back_both_secondary_configs(tmp_path: Path) -> None:
    """
    验证账号库冲突会回滚 OpenCode 与 OMO 的本次修改
    """

    paths = _paths(tmp_path)
    _write_existing_auth(paths)
    original_opencode = '{"theme":"system","provider":{}}\n'
    original_omo = '{"agents":{"build":{"model":"other/model","fallback_models":[]}}}\n'
    paths.opencode_path.write_text(original_opencode, encoding="utf-8")
    paths.omo_path.write_text(original_omo, encoding="utf-8")
    vault = AccountVaultService(tmp_path / "accounts.db")
    _unlock(vault)
    conflict_data = _completion_data("c")
    vault.add_account(
        AccountCreate(
            github_username="conflicting-account",
            github_email="conflict@example.test",
            github_password=SecretStr("Conflict-GitHub-Password!"),
            opencode_provider_name="opencode-go2",
            opencode_workspace_id="wrk_conflict",
            opencode_api_key=SecretStr("sk-" + "y" * 64),
            email_provider="fake",
            temp_email="conflict@example.test",
        )
    )
    service, client = _completion_service(tmp_path, vault)

    with pytest.raises(AccountCompletionError, match="二级账号加密持久化失败"):
        await service.complete(conflict_data)
    await client.aclose()

    assert json.loads(paths.opencode_path.read_text(encoding="utf-8")) == json.loads(original_opencode)
    assert json.loads(paths.omo_path.read_text(encoding="utf-8")) == json.loads(original_omo)
    assert vault.account_count() == 1


@pytest.mark.anyio
async def test_secondary_rollback_attempts_both_files_after_first_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    验证 OMO 回滚失败时仍继续尝试恢复 OpenCode 配置
    """

    paths = _paths(tmp_path)
    _write_existing_auth(paths)
    paths.opencode_path.write_text('{"provider":{}}\n', encoding="utf-8")
    paths.omo_path.write_text(
        '{"agents":{"build":{"model":"other/model","fallback_models":[]}}}\n',
        encoding="utf-8",
    )
    vault = AccountVaultService(tmp_path / "accounts.db")
    _unlock(vault)
    vault.add_account(
        AccountCreate(
            github_username="rollback-conflict",
            github_email="rollback-conflict@example.test",
            github_password=SecretStr("Rollback-Conflict-Password!"),
            opencode_provider_name="opencode-go2",
            opencode_workspace_id="wrk_rollback_conflict",
            opencode_api_key=SecretStr("sk-" + "x" * 64),
            email_provider="fake",
            temp_email="rollback-conflict@example.test",
        )
    )
    attempted_targets: List[str] = []

    def controlled_rollback(result: ConfigWriteResult) -> None:
        attempted_targets.append(result.target_path.name)
        if result.target_path == paths.omo_path:
            raise ConfigFileError("模拟 OMO 回滚失败")

    monkeypatch.setattr("engine.completion.rollback_write", controlled_rollback)
    service, client = _completion_service(tmp_path, vault)

    with pytest.raises(AccountCompletionError, match="配置回滚不完整"):
        await service.complete(_completion_data("e"))
    await client.aclose()

    assert attempted_targets == ["oh-my-openagent.json", "opencode.json"]


@pytest.mark.anyio
async def test_import_rebuilds_primary_and_secondary_configs_before_committing_vault(tmp_path: Path) -> None:
    """
    验证导入完整账号会在目标机器重建三份配置并保留整批账号
    """

    source = AccountVaultService(tmp_path / "source.db")
    _unlock(source)
    first = _completion_data("i")
    second = _completion_data("j")
    source.add_account(
        AccountCreate(
            github_username=first.github_username,
            github_email=first.github_email,
            github_password=first.github_password,
            opencode_provider_name="opencode-go",
            opencode_workspace_id=first.opencode_workspace_id,
            opencode_api_key=first.opencode_api_key,
            email_provider=first.email_provider,
            temp_email=first.temp_email,
        )
    )
    source.add_account(
        AccountCreate(
            github_username=second.github_username,
            github_email=second.github_email,
            github_password=second.github_password,
            opencode_provider_name="opencode-go2",
            opencode_workspace_id=second.opencode_workspace_id,
            opencode_api_key=second.opencode_api_key,
            email_provider=second.email_provider,
            temp_email=second.temp_email,
        )
    )
    bundle_password = SecretStr("configuration rebuild bundle password")
    bundle = source.export_accounts(bundle_password)

    target = AccountVaultService(tmp_path / "target.db")
    _unlock(target)
    service, client = _completion_service(tmp_path / "target-config", target)
    imported_count = await service.import_bundle(bundle, bundle_password)
    await client.aclose()

    paths = _paths(tmp_path / "target-config")
    assert imported_count == 2
    assert target.account_count() == 2
    assert json.loads(paths.auth_path.read_text(encoding="utf-8"))["opencode-go"]["key"].startswith("sk-")
    assert "opencode-go2" in json.loads(paths.opencode_path.read_text(encoding="utf-8"))["provider"]
    assert json.loads(paths.omo_path.read_text(encoding="utf-8"))["model_fallback"] is True


@pytest.mark.anyio
async def test_import_vault_conflict_rolls_back_all_rebuilt_configs(tmp_path: Path) -> None:
    """
    验证 SQLite 导入冲突会恢复本次对目标配置的全部修改
    """

    target = AccountVaultService(tmp_path / "target.db")
    _unlock(target)
    existing = AccountCreate(
        uuid="00000000-0000-4000-8000-000000000080",
        github_username="existing-import-user",
        github_email="existing-import@example.test",
        github_password=SecretStr("Existing-Import-GitHub-Password!"),
        opencode_provider_name="opencode-go",
        opencode_workspace_id="wrk_existing_import",
        opencode_api_key=SecretStr("sk-" + "z" * 64),
        email_provider="fake",
        temp_email="existing-import@example.test",
    )
    target.add_account(existing)
    config_root = tmp_path / "target-config"
    paths = _paths(config_root)
    paths.auth_path.parent.mkdir(parents=True, exist_ok=True)
    _write_existing_auth(paths)
    paths.opencode_path.write_text('{"theme":"system","provider":{}}\n', encoding="utf-8")
    paths.omo_path.write_text(
        '{"agents":{"build":{"model":"other/model","fallback_models":[]}}}\n',
        encoding="utf-8",
    )
    original_opencode = paths.opencode_path.read_text(encoding="utf-8")
    original_omo = paths.omo_path.read_text(encoding="utf-8")

    source = AccountVaultService(tmp_path / "source.db")
    _unlock(source)
    source.add_account(existing)
    bundle_password = SecretStr("rollback import bundle password")
    bundle = source.export_accounts(bundle_password)
    service, client = _completion_service(config_root, target)

    with pytest.raises(AccountAlreadyExistsError):
        await service.import_bundle(bundle, bundle_password)
    await client.aclose()

    assert paths.opencode_path.read_text(encoding="utf-8") == original_opencode
    assert paths.omo_path.read_text(encoding="utf-8") == original_omo
    assert target.account_count() == 1
