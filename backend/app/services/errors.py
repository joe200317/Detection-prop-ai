class NotFoundError(Exception):
    pass


class RequestError(Exception):
    pass


class AIServiceError(Exception):
    def __init__(self, message: str, code: str = "qwen") -> None:
        super().__init__(message)
        self.code = code
