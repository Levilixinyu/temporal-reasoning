class TemporalError(ValueError):
    """A query cannot be interpreted or executed under the declared semantics."""


class UnsupportedQuestion(TemporalError):
    pass


class PlanValidationError(TemporalError):
    pass


class ExecutionLimitError(TemporalError):
    pass
