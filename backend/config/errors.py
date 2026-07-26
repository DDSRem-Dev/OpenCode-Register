class ConfigFileError(Exception):
    """
    配置文件无法安全读取、验证或写入异常
    """


class ConfigConflictError(ConfigFileError):
    """
    配置目标已存在或结构与所有权规则冲突异常
    """


class ModelCatalogError(Exception):
    """
    OpenCode Go 官方模型目录不可用或响应无效异常
    """
