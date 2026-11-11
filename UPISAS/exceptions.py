class UPISASException(Exception):
    pass


class ServerNotReachable(UPISASException):
    pass


class EndpointNotReachable(UPISASException):
    pass


class IncompleteJSONSchema(UPISASException):
    pass
