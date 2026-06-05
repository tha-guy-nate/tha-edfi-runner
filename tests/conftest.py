from unittest.mock import MagicMock

import pytest

BASE_URL = "https://edfi.example.com/api"
CLIENT_ID = "test_client"
CLIENT_SECRET = "test_secret"
TOKEN = "test_bearer_token"


def make_mock_response(status_code: int = 200, json_data=None):
    mock = MagicMock()
    mock.status_code = status_code
    mock.json.return_value = json_data
    if status_code >= 400:
        from requests import HTTPError
        mock.raise_for_status.side_effect = HTTPError(response=mock)
    else:
        mock.raise_for_status.return_value = None
    return mock


@pytest.fixture
def bearer_assessment():
    """ThaAssessment instance using a pre-provided bearer token."""
    from tha_edfi_runner.resources.assessment.runner import ThaAssessment
    return ThaAssessment(base_url=BASE_URL, bearer_token=TOKEN)
