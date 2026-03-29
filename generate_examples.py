from __future__ import annotations

import asyncio
import os
import sys
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from models.schemas import QueryRequest
from models.schemas import AggregationOp
from models.schemas import TemporalGranularity
from models.schemas import VisualizationEncoding
from models.schemas import VisualizationFieldEncoding
from models.schemas import VisualizationType
from services.agent import AggregationStrategy
from services.agent import OpenAIConfigurationError
from services.agent import QueryPlan
from services.agent import analyze_query
from services.api_client import fetch_trials
from services.processor import extract_overall_status
from services.processor import extract_start_year
from services.processor import process_data


@dataclass(frozen=True)
class ExampleCase:
    filename: str
    request: QueryRequest


def build_example_cases() -> list[ExampleCase]:
    return [
        ExampleCase(
            filename="time_trends.json",
            request=QueryRequest(
                query="How has the number of trials for Pembrolizumab changed per year since 2015?",
                drug_name="Pembrolizumab",
                start_year=2015,
            ),
        ),
        ExampleCase(
            filename="distributions.json",
            request=QueryRequest(
                query="How are Diabetes trials distributed across phases?",
                condition="Diabetes",
            ),
        ),
        ExampleCase(
            filename="comparisons.json",
            request=QueryRequest(
                query="Compare sponsor categories across Lung Cancer trials.",
                condition="Lung Cancer",
            ),
        ),
        ExampleCase(
            filename="geographic.json",
            request=QueryRequest(
                query="Which countries have the most recruiting trials for Alzheimer's?",
                condition="Alzheimer's",
                recruitment_status="RECRUITING",
            ),
        ),
        ExampleCase(
            filename="networks.json",
            request=QueryRequest(
                query="Show a network of sponsors to drugs for Melanoma trials.",
                condition="Melanoma",
            ),
        ),
    ]


def build_fallback_plan(case: ExampleCase) -> QueryPlan:
    if case.filename == "time_trends.json":
        return QueryPlan(
            api_parameters={
                "query.intr": "Pembrolizumab",
            },
            visualization_type=VisualizationType.TIME_SERIES,
            encoding=VisualizationEncoding(
                x=VisualizationFieldEncoding(
                    field="year",
                    data_type="temporal",
                    time_granularity=TemporalGranularity.YEAR,
                ),
                y=VisualizationFieldEncoding(
                    field="trial_count",
                    data_type="quantitative",
                    aggregate=AggregationOp.COUNT,
                ),
            ),
            aggregation_strategy=AggregationStrategy.TIME_SERIES_BY_YEAR,
        )
    if case.filename == "distributions.json":
        return QueryPlan(
            api_parameters={"query.cond": "Diabetes"},
            visualization_type=VisualizationType.BAR_CHART,
            encoding=VisualizationEncoding(
                x=VisualizationFieldEncoding(field="phase", data_type="nominal"),
                y=VisualizationFieldEncoding(
                    field="trial_count",
                    data_type="quantitative",
                    aggregate=AggregationOp.COUNT,
                ),
            ),
            aggregation_strategy=AggregationStrategy.GROUP_BY_PHASE_COUNT,
        )
    if case.filename == "comparisons.json":
        return QueryPlan(
            api_parameters={"query.cond": "Lung Cancer"},
            visualization_type=VisualizationType.BAR_CHART,
            encoding=VisualizationEncoding(
                x=VisualizationFieldEncoding(field="sponsor_class", data_type="nominal"),
                y=VisualizationFieldEncoding(
                    field="trial_count",
                    data_type="quantitative",
                    aggregate=AggregationOp.COUNT,
                ),
            ),
            aggregation_strategy=AggregationStrategy.GROUP_BY_SPONSOR_CLASS_COUNT,
        )
    if case.filename == "geographic.json":
        return QueryPlan(
            api_parameters={
                "query.cond": "Alzheimer's",
                "filter.overallStatus": "RECRUITING",
            },
            visualization_type=VisualizationType.BAR_CHART,
            encoding=VisualizationEncoding(
                x=VisualizationFieldEncoding(field="country", data_type="nominal"),
                y=VisualizationFieldEncoding(
                    field="trial_count",
                    data_type="quantitative",
                    aggregate=AggregationOp.COUNT,
                ),
            ),
            aggregation_strategy=AggregationStrategy.GROUP_BY_COUNTRY_RECRUITING_COUNT,
        )
    if case.filename == "networks.json":
        return QueryPlan(
            api_parameters={"query.cond": "Melanoma"},
            visualization_type=VisualizationType.NETWORK_GRAPH,
            encoding=VisualizationEncoding(
                source=VisualizationFieldEncoding(field="source", data_type="nominal"),
                target=VisualizationFieldEncoding(field="target", data_type="nominal"),
                weight=VisualizationFieldEncoding(
                    field="weight",
                    data_type="quantitative",
                    aggregate=AggregationOp.COUNT,
                ),
            ),
            aggregation_strategy=AggregationStrategy.NETWORK_SPONSOR_DRUG,
        )
    raise ValueError(f"Unsupported example case: {case.filename}")


async def build_plan(case: ExampleCase) -> QueryPlan:
    if os.getenv("OPENAI_API_KEY"):
        try:
            return await analyze_query(case.request)
        except OpenAIConfigurationError:
            return build_fallback_plan(case)
    return build_fallback_plan(case)


def apply_request_constraints(studies: list[dict], request: QueryRequest) -> list[dict]:
    filtered_studies: list[dict] = []
    for study in studies:
        start_year = extract_start_year(study)
        overall_status = extract_overall_status(study)
        if request.start_year is not None and (start_year is None or start_year < request.start_year):
            continue
        if request.end_year is not None and (start_year is None or start_year > request.end_year):
            continue
        if request.recruitment_status is not None:
            expected_status = request.recruitment_status.strip().replace("_", " ").title()
            if overall_status != expected_status:
                continue
        filtered_studies.append(study)
    return filtered_studies


async def generate_example(case: ExampleCase, output_directory: Path) -> Path:
    plan = await build_plan(case)
    studies = await fetch_trials(plan.api_parameters, case.request.limit)
    response = process_data(studies=apply_request_constraints(studies, case.request), plan=plan)
    output_path = output_directory / case.filename
    output_path.write_text(response.model_dump_json(indent=2), encoding="utf-8")
    return output_path


async def generate_all_examples() -> list[Path]:
    load_dotenv()
    output_directory = Path(__file__).resolve().parent
    created_paths: list[Path] = []
    for case in build_example_cases():
        created_paths.append(await generate_example(case=case, output_directory=output_directory))
    return created_paths


def main() -> None:
    created_paths = asyncio.run(generate_all_examples())
    for path in created_paths:
        print(path.relative_to(PROJECT_ROOT))


if __name__ == "__main__":
    main()
