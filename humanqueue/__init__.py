from .client import HumanQueue, HumanQueueCreateError, HumanQueueError

# Preferred public primitive. HumanQueue remains an API-compatible name.
HumanBoundary = HumanQueue

__all__ = ["HumanBoundary", "HumanQueue", "HumanQueueError", "HumanQueueCreateError"]
__version__ = "0.7.0"
