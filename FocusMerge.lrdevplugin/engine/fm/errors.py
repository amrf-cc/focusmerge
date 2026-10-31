"""Exceptions carrying the process exit code."""


class FocusMergeError(Exception):
    exit_code = 6


class UsageError(FocusMergeError):
    exit_code = 2


class NotEnoughFrames(FocusMergeError):
    exit_code = 3


class FrameError(FocusMergeError):
    """Unreadable, unsupported or mismatched input, or an output write failure."""
    exit_code = 4


class QueueTimeout(FrameError):
    pass


class Cancelled(FocusMergeError):
    exit_code = 5
