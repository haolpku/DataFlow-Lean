"""Small import boundary around OpenDCAI DataFlow.

Production installations use the real DataFlow classes.  The fallback keeps the
pure control-plane unit tests importable without pulling the large PDF stack.
"""

from __future__ import annotations

try:  # pragma: no cover - exercised by the integration test/environment
    from dataflow.core import OperatorABC
    from dataflow.pipeline import PipelineABC
    from dataflow.utils.registry import OPERATOR_REGISTRY
    from dataflow.utils.storage import DataFlowStorage, FileStorage
    DATAFLOW_AVAILABLE = True
except ImportError:  # pragma: no cover - intentionally tiny test fallback
    from abc import ABC, abstractmethod

    DATAFLOW_AVAILABLE = False

    class _Registry(dict):
        def register(self):
            def decorate(cls):
                self[cls.__name__] = cls
                return cls
            return decorate

    OPERATOR_REGISTRY = _Registry()

    class OperatorABC(ABC):
        @abstractmethod
        def run(self, *args, **kwargs): ...

    class PipelineABC(ABC):
        def __init__(self):
            self.compiled = False

        @abstractmethod
        def forward(self): ...

        def compile(self):
            self.compiled = True

    class DataFlowStorage(ABC):
        @abstractmethod
        def read(self, output_type="dict"): ...

        @abstractmethod
        def write(self, data): ...

    FileStorage = None

