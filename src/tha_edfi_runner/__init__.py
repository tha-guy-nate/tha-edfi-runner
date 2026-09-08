"""tha-edfi-runner: typed Ed-Fi ODS API client with pipeline-friendly row processing."""

from tha_edfi_runner.base import ThaEdfiBase, resolve_api_spec_segment
from tha_edfi_runner.errors import EdfiError
from tha_edfi_runner.resources.student_assessment.runner import ThaStudentAssessment

__version__ = "0.1.12"
__all__ = [
    "EdfiError",
    "ThaEdfiBase",
    "ThaStudentAssessment",
    "resolve_api_spec_segment",
]
