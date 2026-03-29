from __future__ import annotations

from typing import Any
from typing import Final

import httpx


StudyRecord = dict[str, Any]
QueryParameterValue = str | list[str]


class ClinicalTrialsAPIError(RuntimeError):
    pass


class ClinicalTrialsAPITimeoutError(ClinicalTrialsAPIError):
    pass


BASE_URL: Final[str] = "https://clinicaltrials.gov/api/v2/studies"
DEFAULT_PAGE_SIZE: Final[int] = 100
MAX_PAGE_SIZE: Final[int] = 100
MAX_PAGES: Final[int] = 5
MAX_RECORDS: Final[int] = 500
DEFAULT_TIMEOUT: Final[httpx.Timeout] = httpx.Timeout(10.0, connect=5.0)
DEFAULT_FIELDS: Final[str] = ",".join(
    [
        "protocolSection.identificationModule.nctId",
        "protocolSection.identificationModule.briefTitle",
        "protocolSection.identificationModule.officialTitle",
        "protocolSection.descriptionModule.briefSummary",
        "protocolSection.conditionsModule.conditions",
        "protocolSection.designModule.phases",
        "protocolSection.statusModule.overallStatus",
        "protocolSection.armsInterventionsModule.interventions.name",
        "protocolSection.armsInterventionsModule.interventions.type",
        "protocolSection.sponsorCollaboratorsModule.leadSponsor.name",
        "protocolSection.sponsorCollaboratorsModule.leadSponsor.class",
        "protocolSection.contactsLocationsModule.locations.country",
        "protocolSection.statusModule.startDateStruct.date",
        "protocolSection.statusModule.primaryCompletionDateStruct.date",
        "protocolSection.statusModule.completionDateStruct.date",
        "protocolSection.designModule.enrollmentInfo.count",
        "protocolSection.designModule.studyType",
        "protocolSection.designModule.designInfo.primaryPurpose",
        "protocolSection.eligibilityModule.sex",
        "protocolSection.eligibilityModule.stdAges",
    ]
)


def resolve_record_limit(limit: int | None) -> int:
    if limit is None:
        return MAX_RECORDS
    return max(1, min(limit, MAX_RECORDS))


def resolve_page_size(api_parameters: dict[str, QueryParameterValue], record_limit: int) -> int:
    raw_page_size = api_parameters.get("pageSize")
    if isinstance(raw_page_size, str):
        try:
            parsed_page_size = int(raw_page_size)
        except ValueError:
            parsed_page_size = DEFAULT_PAGE_SIZE
    else:
        parsed_page_size = DEFAULT_PAGE_SIZE
    bounded_page_size = max(1, min(parsed_page_size, MAX_PAGE_SIZE, record_limit))
    return bounded_page_size


def serialize_parameter_value(value: QueryParameterValue) -> str:
    if isinstance(value, str):
        return value
    return ",".join(item for item in value if item)


def build_request_params(
    api_parameters: dict[str, QueryParameterValue],
    page_size: int,
    page_token: str | None,
) -> dict[str, str]:
    normalized_parameters: dict[str, str] = {}
    for key, value in api_parameters.items():
        if key in {"pageToken", "nextPageToken"}:
            continue
        normalized_parameters[key] = serialize_parameter_value(value)
    if not normalized_parameters.get("fields"):
        normalized_parameters["fields"] = DEFAULT_FIELDS
    normalized_parameters["pageSize"] = str(page_size)
    if page_token:
        normalized_parameters["pageToken"] = page_token
    return normalized_parameters


def extract_studies(payload: object) -> tuple[list[StudyRecord], str | None]:
    if not isinstance(payload, dict):
        raise ClinicalTrialsAPIError("ClinicalTrials.gov returned a non-object response")
    studies = payload.get("studies")
    if not isinstance(studies, list):
        raise ClinicalTrialsAPIError("ClinicalTrials.gov response is missing a studies array")
    validated_studies: list[StudyRecord] = []
    for study in studies:
        if isinstance(study, dict):
            validated_studies.append(study)
    next_page_token = payload.get("nextPageToken")
    if next_page_token is not None and not isinstance(next_page_token, str):
        raise ClinicalTrialsAPIError("ClinicalTrials.gov returned an invalid nextPageToken")
    return validated_studies, next_page_token


async def fetch_page(
    client: httpx.AsyncClient,
    api_parameters: dict[str, QueryParameterValue],
    page_size: int,
    page_token: str | None,
) -> tuple[list[StudyRecord], str | None]:
    request_params = build_request_params(
        api_parameters=api_parameters,
        page_size=page_size,
        page_token=page_token,
    )
    try:
        response = await client.get(BASE_URL, params=request_params)
        response.raise_for_status()
    except httpx.TimeoutException as exc:
        raise ClinicalTrialsAPITimeoutError("ClinicalTrials.gov request timed out") from exc
    except httpx.HTTPStatusError as exc:
        raise ClinicalTrialsAPIError(
            f"ClinicalTrials.gov returned HTTP {exc.response.status_code}"
        ) from exc
    except httpx.RequestError as exc:
        raise ClinicalTrialsAPIError("ClinicalTrials.gov request failed") from exc
    try:
        payload = response.json()
    except ValueError as exc:
        raise ClinicalTrialsAPIError("ClinicalTrials.gov returned invalid JSON") from exc
    return extract_studies(payload)


async def fetch_trials(
    api_parameters: dict[str, str | list[str]],
    limit: int | None = None,
) -> list[dict[str, Any]]:
    record_limit = resolve_record_limit(limit)
    page_size = resolve_page_size(api_parameters=api_parameters, record_limit=record_limit)
    studies: list[StudyRecord] = []
    next_page_token: str | None = None
    async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT, headers={"Accept": "application/json"}) as client:
        for _ in range(MAX_PAGES):
            page_studies, next_page_token = await fetch_page(
                client=client,
                api_parameters=api_parameters,
                page_size=page_size,
                page_token=next_page_token,
            )
            remaining_capacity = record_limit - len(studies)
            if remaining_capacity <= 0:
                break
            studies.extend(page_studies[:remaining_capacity])
            if len(studies) >= record_limit or not next_page_token:
                break
    return studies
