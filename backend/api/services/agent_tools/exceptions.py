class ToolError(Exception):
    def __init__(self, message: str, error_code: str = "TOOL_EXECUTION_FAILED"):
        super().__init__(message)
        self.message = message
        self.error_code = error_code


class ToolRejectedError(ToolError):
    pass
