from __future__ import annotations


class PipelineError(Exception):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


class SchemaError(PipelineError):
    pass


class ValidationError(PipelineError):
    pass
