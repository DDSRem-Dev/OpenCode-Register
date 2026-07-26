import re
from html.parser import HTMLParser
from typing import List, Optional

GITHUB_CODE_PATTERN = re.compile(r"(?<!\d)(\d{8})(?!\d)")


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: List[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def html_to_text(html: str) -> str:
    """
    从 HTML 邮件正文提取纯文本

    :param html (str): HTML 邮件正文

    :return str: 合并后的纯文本
    """

    parser = _TextExtractor()
    parser.feed(html)
    return " ".join(parser.parts)


def extract_github_verification_code(subject: str, bodies: List[str]) -> Optional[str]:
    """
    从 GitHub 验证邮件中提取八位数字验证码

    :param subject (str): 邮件主题
    :param bodies (List): 邮件正文列表

    :return str: 验证码，未匹配时返回空值
    """

    combined = "\n".join([subject, *bodies])
    match = GITHUB_CODE_PATTERN.search(combined)
    if match is None:
        return None
    return match.group(1)
