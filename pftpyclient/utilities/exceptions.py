class HandshakeRequiredException(Exception):
    """ This exception is raised when the full handshake protocol has not been completed between two addresses"""
    def __init__(self, source, counterparty):
        super().__init__(f"Cannot encrypt message: Handshake protocol not completed between {source} and {counterparty}")

class GoogleDocNotFoundException(Exception):
    """ This exception is raised when the Google Doc is not found """
    def __init__(self, google_url):
        self.google_url = google_url
        super().__init__(f"Google Doc not found: {google_url}")

class XRPAccountNotFoundException(Exception):
    """ This exception is raised when the XRP account is not found """
    def __init__(self, address):
        self.address = address
        super().__init__(f"XRP account not found: {address}")

class NoMatchingTaskException(Exception):
    """ This exception is raised when no matching task is found """
    def __init__(self, task_id):
        self.task_id = task_id
        super().__init__(f"No matching task found for task ID: {task_id}")

class NoMatchingMemoException(Exception):
    """ This exception is raised when no matching memo is found """
    def __init__(self, memo_id):
        self.memo_id = memo_id
        super().__init__(f"No matching memo found for memo ID: {memo_id}")

class WrongTaskStateException(Exception):
    # TODO: restricted_flag is a hack and is confusing
    """ This exception is raised when the most recent task status is not the expected status 
    Alternatively, it can be raised when the task status is restricted 
    """
    def __init__(self, expected_status, actual_status, restricted_flag=False):
        self.expected_status = expected_status
        self.actual_status = actual_status
        prefix = "Restricted" if restricted_flag else "Expected"
        super().__init__(f"{prefix} status: {expected_status}, actual status: {actual_status}")

class InvalidGoogleDocException(Exception):
    """ This exception is raised when the google doc is not valid """
    def __init__(self, google_url):
        self.google_url = google_url
        super().__init__(f"Invalid Google Doc URL: {google_url}")

class GoogleDocIsNotSharedException(Exception):
    """ This exception is raised when the google doc is not shared """
    def __init__(self, google_url):
        self.google_url = google_url
        super().__init__(f"Google Doc is not shared: {google_url}")
