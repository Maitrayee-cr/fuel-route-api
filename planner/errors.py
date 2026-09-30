class PlanningError(Exception):
    def __init__(self, code, message, status=422):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
