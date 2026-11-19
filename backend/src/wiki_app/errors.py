"""可向用户解释的业务错误，避免把密钥或底层堆栈返回浏览器。"""


class AppError(Exception):
    def __init__(self, message: str, code: str = "invalid_request", status: int = 400):
        super().__init__(message)
        self.message, self.code, self.status = message, code, status


class ConfigurationRequired(AppError):
    def __init__(self, message: str):
        super().__init__(message, "configuration_required", 409)


class BudgetExceeded(AppError):
    def __init__(self, message: str = "本月 Token 额度不足，原始资料和已完成结果已保留。"):
        super().__init__(message, "budget_exceeded", 409)
