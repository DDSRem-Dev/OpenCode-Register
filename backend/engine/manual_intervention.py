from typing import Dict, Tuple

from engine.models import ManualIntervention, ManualInterventionReason


def create_flow_manual_intervention(reason: ManualInterventionReason) -> ManualIntervention:
    """
    创建账号流程可安全展示的人工介入请求

    :param reason (ManualInterventionReason): 人工介入原因

    :return ManualIntervention: 对应标题和操作说明
    """

    messages: Dict[ManualInterventionReason, Tuple[str, str]] = {
        ManualInterventionReason.CAPTCHA: (
            "需要完成安全验证",
            "请在已打开的 GitHub 窗口中完成验证，然后返回继续。",
        ),
        ManualInterventionReason.PHONE_VERIFICATION: (
            "需要人工验证",
            "GitHub 要求额外验证，请在浏览器中亲自处理，然后返回继续。",
        ),
        ManualInterventionReason.UNKNOWN_BLOCK: (
            "注册流程已暂停",
            "页面状态无法安全判断，请检查 GitHub 窗口并在确认后继续。",
        ),
        ManualInterventionReason.TIMEOUT: ("等待操作超时", "请检查 GitHub 窗口当前状态，处理后返回继续。"),
        ManualInterventionReason.USER_PAUSED: ("流程已暂停", "流程已在安全步骤暂停，可以继续或中止。"),
        ManualInterventionReason.PAYMENT: (
            "等待手动支付",
            "请在 OpenCode Go 页面选择付款方式并亲自完成支付，完成后返回确认。",
        ),
        ManualInterventionReason.API_KEY_INPUT: (
            "需要手动复制 API Key",
            "请在 OpenCode 的 API 密钥页面复制 Default API Key，并在此提交。",
        ),
    }
    title, instruction = messages[reason]
    return ManualIntervention(reason=reason, title=title, instruction=instruction)
