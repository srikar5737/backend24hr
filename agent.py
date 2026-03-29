from __future__ import annotations

import json
import os
from enum import Enum
from typing import Final

from openai import AsyncOpenAI
from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import StrictStr

from models.schemas import QueryRequest
from models.schemas import VisualizationEncoding
from models.schemas import VisualizationType


ApiParameterValue = StrictStr | list[StrictStr]


class AggregationStrategy(str, Enum):
    TIME_SERIES_BY_YEAR = "time_series_by_year"
    TIME_SERIES_BY_MONTH = "time_series_by_month"
    GROUP_BY_PHASE_COUNT = "group_by_phase_count"
    GROUP_BY_STATUS_COUNT = "group_by_status_count"
    GROUP_BY_INTERVENTION_TYPE_COUNT = "group_by_intervention_type_count"
    GROUP_BY_SPONSOR_CLASS_COUNT = "group_by_sponsor_class_count"
    GROUP_BY_COUNTRY_RECRUITING_COUNT = "group_by_country_recruiting_count"
    HISTOGRAM_ENROLLMENT = "histogram_enrollment"
    SCATTER_ENROLLMENT_VS_START_YEAR = "scatter_enrollment_vs_start_year"
    NETWORK_SPONSOR_DRUG = "network_sponsor_drug"
    NETWORK_DRUG_CONDITION = "network_drug_condition"
    NETWORK_DRUG_CO_OCCURRENCE = "network_drug_co_occurrence"


class QueryPlan(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    api_parameters: dict[StrictStr, ApiParameterValue]
    visualization_type: VisualizationType
    encoding: VisualizationEncoding
    aggregation_strategy: AggregationStrategy


class QueryAnalysisError(RuntimeError):
    pass


class OpenAIConfigurationError(RuntimeError):
    pass


DEFAULT_OPENAI_MODEL: Final[str] = "gpt-4o"
SYSTEM_PROMPT: Final[str] = """
You are the planning layer for a backend service that answers clinical trial questions with visualization specifications backed by the ClinicalTrials.gov Data API.

Your task is to translate a user request into a deterministic QueryPlan for downstream Python execution.

You are not allowed to return clinical trial results, counts, estimates, sample rows, or synthetic data.
You must never hallucinate any trial facts.
You must only produce a blueprint that tells the backend:
1. which ClinicalTrials.gov API parameters to call
2. which visualization type to render
3. which fields belong on each encoding channel
4. which aggregation strategy the Python processor should execute

The output must match the QueryPlan schema exactly.

ClinicalTrials.gov API guidance:
- Build api_parameters for the v2 API search endpoint.
- Prefer keys such as query.term, query.cond, query.intr, query.locn, filter.overallStatus, filter.advanced, pageSize, countTotal, sort, fields, format.
- Use query.cond for disease or condition intent.
- Use query.intr for named drugs or interventions when the user specifies a drug.
- Use query.term when the intent is broader and requires combined text search.
- Use query.locn for geographic requests.
- Use filter.advanced only when a constraint cannot be expressed cleanly with simpler keys.
- Keep parameters minimal and semantically precise.
- Do not invent unsupported API parameter names.

Visualization policy:
- bar_chart for categorical comparisons or ranked counts
- time_series for temporal trend questions
- scatter_plot for relationship questions between two numeric or ordered measures
- histogram for bucketed distributions of a single numeric measure
- network_graph for entity relationship questions such as sponsor-drug or drug-drug networks

Aggregation strategy policy:
- time_series_by_year for yearly counts
- time_series_by_month for monthly counts
- group_by_phase_count for phase distributions
- group_by_status_count for status distributions
- group_by_intervention_type_count for intervention type distributions
- group_by_sponsor_class_count for sponsor category comparisons
- group_by_country_recruiting_count for recruiting-trials-by-country questions
- histogram_enrollment for enrollment distributions
- scatter_enrollment_vs_start_year for enrollment versus start year relationships
- network_sponsor_drug for sponsor-to-drug graphs
- network_drug_condition for drug-to-condition graphs
- network_drug_co_occurrence for combination-study drug networks

Encoding rules:
- Every selected channel must reference a field name that the downstream processor can produce.
- Use concise field names such as year, month, phase, trial_count, status, intervention_type, sponsor_class, country, enrollment, enrollment_bucket, condition, source, target, weight, node_id, node_label, node_group, study_label.
- Include aggregate=count on counted measures such as trial_count or weight when appropriate.
- Use time_granularity only for temporal axes.
- Leave irrelevant encoding channels null or omitted.

Grounding rules:
- Respect explicit user filters from structured fields in the request.
- If both the natural-language query and structured fields mention the same concept, preserve the more specific constraint.
- If the question is ambiguous, choose the safest plan that can be executed deterministically without fabricating meaning.
- Do not emit visualization data.
- Do not emit citations.
""".strip()


def get_openai_client() -> AsyncOpenAI:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise OpenAIConfigurationError("OPENAI_API_KEY is not set")
    return AsyncOpenAI(api_key=api_key)


def get_openai_model() -> str:
    model_name = os.getenv("OPENAI_MODEL", DEFAULT_OPENAI_MODEL).strip()
    return model_name or DEFAULT_OPENAI_MODEL


def build_request_payload(request: QueryRequest) -> str:
    request_payload = request.model_dump(exclude_none=True)
    return json.dumps(request_payload, indent=2, sort_keys=True)


def build_user_prompt(request: QueryRequest) -> str:
    request_payload = build_request_payload(request)
    return "\n".join(
        [
            "Analyze the following visualization request and return a QueryPlan.",
            "",
            "Request JSON:",
            request_payload,
            "",
            "Return only the structured plan.",
        ]
    )


async def analyze_query(request: QueryRequest) -> QueryPlan:
    client = get_openai_client()
    completion = await client.beta.chat.completions.parse(
        model=get_openai_model(),
        temperature=0.0,
        response_format=QueryPlan,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": build_user_prompt(request)},
        ],
    )
    message = completion.choices[0].message
    if message.parsed is not None:
        return message.parsed
    if message.refusal:
        raise QueryAnalysisError(message.refusal)
    raise QueryAnalysisError("OpenAI returned an unparseable query plan")
