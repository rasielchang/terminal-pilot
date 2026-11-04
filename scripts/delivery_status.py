"""Stage-specific diagnostics; only a proven pre-write failure permits retry."""
import re


def diagnostic(error, stage, *, not_started=False, retryable=False):
    message = str(error)
    message = re.sub(r'(?i)(?:bearer\s+\S+|(?:token|api[_-]?key|password)\s*[=:]\s*\S+|\b(?:sk-|ghp_)[\w-]+)', '[redacted]', message)
    message = re.sub(r'(?:/Users/|/private/|/tmp/|[A-Z]:\\)[^\s\"\']+', '[local path]', message)
    return {'stage':stage, 'type':type(error).__name__, 'message':message[:300],
            'delivery':'not_started' if not_started else 'unknown', 'retryable':retryable,
            **({'code':error.errno} if isinstance(error,OSError) and error.errno else {})}


class SendNotStarted(ValueError):
    def __init__(self, stage, cause, *, retryable=False):
        self.diagnostic = diagnostic(cause,stage,not_started=True,retryable=retryable)
        super().__init__(self.diagnostic['message'])


def preflight(stage, operation, *, retryable=False):
    try:return operation()
    except SendNotStarted:raise
    except (ValueError,OSError) as error:
        raise SendNotStarted(stage,error,retryable=retryable) from error
