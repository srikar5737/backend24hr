from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field
from datetime import datetime
from datetime import timezone
from typing import Any
from typing import Final

from models.schemas import Citation
from models.schemas import ResponseMeta
from models.schemas import VisualizationDataPoint
from models.schemas import VisualizationResponse
from models.schemas import VisualizationSpec
from services.agent import AggregationStrategy
from services.agent import QueryPlan


StudyRecord = dict[str, Any]
_MISSING: Final[object] = object()


class DataProcessingError(RuntimeError):
    pass


@dataclass
class CountBucket:
    count: int = 0
    citations: list[Citation] = field(default_factory=list)


def deep_get(payload: object, path: str, default: object | None = None) -> object | None:
    current: object = payload
    for part in path.split("."):
        if isinstance(current, dict):
            current = current.get(part, _MISSING)
        elif isinstance(current, list) and part.isdigit():
            index = int(part)
            if 0 <= index < len(current):
                current = current[index]
            else:
                return default
        else:
            return default
        if current is _MISSING:
            return default
    return current


def normalize_text(value: object) -> str | None:
    if isinstance(value, str):
        stripped_value = value.strip()
        return stripped_value or None
    if isinstance(value, (int, float, bool)):
        return str(value)
    return None


def normalize_string_list(value: object) -> list[str]:
    if isinstance(value, list):
        normalized_values: list[str] = []
        for item in value:
            normalized_item = normalize_text(item)
            if normalized_item is not None:
                normalized_values.append(normalized_item)
        return deduplicate_strings(normalized_values)
    normalized_value = normalize_text(value)
    if normalized_value is None:
        return []
    return [normalized_value]


def deduplicate_strings(values: list[str]) -> list[str]:
    seen_values: set[str] = set()
    ordered_values: list[str] = []
    for value in values:
        if value not in seen_values:
            seen_values.add(value)
            ordered_values.append(value)
    return ordered_values


def extract_first_text(study: StudyRecord, paths: list[str]) -> str | None:
    for path in paths:
        value = normalize_text(deep_get(study, path))
        if value is not None:
            return value
    return None


def extract_nct_id(study: StudyRecord) -> str:
    return extract_first_text(
        study,
        [
            "protocolSection.identificationModule.nctId",
            "identificationModule.nctId",
            "nctId",
        ],
    ) or "unknown_nct_id"


def extract_brief_title(study: StudyRecord) -> str | None:
    return extract_first_text(
        study,
        [
            "protocolSection.identificationModule.briefTitle",
            "identificationModule.briefTitle",
            "briefTitle",
            "protocolSection.identificationModule.officialTitle",
            "identificationModule.officialTitle",
            "officialTitle",
        ],
    )


def extract_overall_status(study: StudyRecord) -> str | None:
    raw_status = extract_first_text(
        study,
        [
            "protocolSection.statusModule.overallStatus",
            "statusModule.overallStatus",
            "overallStatus",
        ],
    )
    if raw_status is None:
        return None
    return raw_status.replace("_", " ").title()


def format_phase(raw_phase: str) -> str:
    phase_value = raw_phase.strip().upper().replace(" ", "")
    phase_map = {
        "EARLYPHASE1": "Early Phase 1",
        "PHASE1": "Phase 1",
        "PHASE2": "Phase 2",
        "PHASE3": "Phase 3",
        "PHASE4": "Phase 4",
        "NA": "Not Applicable",
    }
    return phase_map.get(phase_value, raw_phase.strip().title())


def extract_phases(study: StudyRecord) -> list[str]:
    raw_phases = normalize_string_list(
        deep_get(study, "protocolSection.designModule.phases")
        or deep_get(study, "designModule.phases")
        or deep_get(study, "phase")
    )
    if not raw_phases:
        return []
    return deduplicate_strings([format_phase(phase) for phase in raw_phases])


def extract_start_year(study: StudyRecord) -> int | None:
    raw_date = extract_first_text(
        study,
        [
            "protocolSection.statusModule.startDateStruct.date",
            "statusModule.startDateStruct.date",
            "startDate",
            "protocolSection.statusModule.studyFirstPostDateStruct.date",
        ],
    )
    if raw_date is None or len(raw_date) < 4 or not raw_date[:4].isdigit():
        return None
    return int(raw_date[:4])


def extract_start_month(study: StudyRecord) -> str | None:
    raw_date = extract_first_text(
        study,
        [
            "protocolSection.statusModule.startDateStruct.date",
            "statusModule.startDateStruct.date",
            "startDate",
            "protocolSection.statusModule.studyFirstPostDateStruct.date",
        ],
    )
    if raw_date is None or len(raw_date) < 7:
        return None
    month_value = raw_date[:7]
    if len(month_value) == 7 and month_value[4] == "-" and month_value[:4].isdigit() and month_value[5:7].isdigit():
        return month_value
    return None


def extract_enrollment_count(study: StudyRecord) -> int | None:
    raw_enrollment = deep_get(study, "protocolSection.designModule.enrollmentInfo.count")
    if raw_enrollment is None:
        raw_enrollment = deep_get(study, "designModule.enrollmentInfo.count")
    if raw_enrollment is None:
        raw_enrollment = deep_get(study, "enrollmentCount")
    if isinstance(raw_enrollment, int):
        return raw_enrollment
    if isinstance(raw_enrollment, float):
        return int(raw_enrollment)
    if isinstance(raw_enrollment, str) and raw_enrollment.isdigit():
        return int(raw_enrollment)
    return None


def extract_sponsor_name(study: StudyRecord) -> str | None:
    return extract_first_text(
        study,
        [
            "protocolSection.sponsorCollaboratorsModule.leadSponsor.name",
            "sponsorCollaboratorsModule.leadSponsor.name",
            "leadSponsor.name",
            "protocolSection.identificationModule.organization.fullName",
        ],
    )


def extract_sponsor_class(study: StudyRecord) -> str | None:
    raw_sponsor_class = extract_first_text(
        study,
        [
            "protocolSection.sponsorCollaboratorsModule.leadSponsor.class",
            "sponsorCollaboratorsModule.leadSponsor.class",
            "leadSponsor.class",
            "protocolSection.identificationModule.organization.class",
        ],
    )
    if raw_sponsor_class is None:
        return None
    return raw_sponsor_class.replace("_", " ").title()


def normalize_intervention_label(value: str) -> str:
    if ":" in value:
        _, _, label = value.partition(":")
        normalized_label = label.strip()
        if normalized_label:
            return normalized_label
    return value.strip()


def extract_intervention_names(study: StudyRecord) -> list[str]:
    intervention_module = deep_get(study, "protocolSection.armsInterventionsModule.interventions")
    if not isinstance(intervention_module, list):
        intervention_module = deep_get(study, "armsInterventionsModule.interventions")
    intervention_names: list[str] = []
    if isinstance(intervention_module, list):
        for item in intervention_module:
            if isinstance(item, dict):
                name = normalize_text(item.get("name"))
                if name is not None:
                    intervention_names.append(name)
    if intervention_names:
        return deduplicate_strings(intervention_names)
    arm_groups = deep_get(study, "protocolSection.armsInterventionsModule.armGroups")
    if not isinstance(arm_groups, list):
        arm_groups = deep_get(study, "armsInterventionsModule.armGroups")
    if isinstance(arm_groups, list):
        for group in arm_groups:
            if isinstance(group, dict):
                for value in normalize_string_list(group.get("interventionNames")):
                    intervention_names.append(normalize_intervention_label(value))
    return deduplicate_strings(intervention_names)


def extract_intervention_types(study: StudyRecord) -> list[str]:
    intervention_module = deep_get(study, "protocolSection.armsInterventionsModule.interventions")
    if not isinstance(intervention_module, list):
        intervention_module = deep_get(study, "armsInterventionsModule.interventions")
    intervention_types: list[str] = []
    if isinstance(intervention_module, list):
        for item in intervention_module:
            if isinstance(item, dict):
                raw_type = normalize_text(item.get("type"))
                if raw_type is not None:
                    intervention_types.append(raw_type.replace("_", " ").title())
    return deduplicate_strings(intervention_types)


def extract_conditions(study: StudyRecord) -> list[str]:
    return normalize_string_list(
        deep_get(study, "protocolSection.conditionsModule.conditions")
        or deep_get(study, "conditionsModule.conditions")
        or deep_get(study, "condition")
    )


def extract_countries(study: StudyRecord) -> list[str]:
    locations = deep_get(study, "protocolSection.contactsLocationsModule.locations")
    if not isinstance(locations, list):
        locations = deep_get(study, "contactsLocationsModule.locations")
    country_values: list[str] = []
    if isinstance(locations, list):
        for location in locations:
            if isinstance(location, dict):
                country = normalize_text(location.get("country"))
                if country is not None:
                    country_values.append(country)
    return deduplicate_strings(country_values)


def extract_excerpt(study: StudyRecord, primary_value: str | None = None) -> str:
    title = extract_brief_title(study)
    if primary_value and title:
        return f"{primary_value}: {title}"[:2000]
    if primary_value:
        return primary_value[:2000]
    if title:
        return title[:2000]
    description = extract_first_text(
        study,
        [
            "protocolSection.descriptionModule.briefSummary",
            "descriptionModule.briefSummary",
        ],
    )
    if description is not None:
        return description[:2000]
    return f"Supporting study record {extract_nct_id(study)}"


def build_citation(study: StudyRecord, primary_value: str | None = None) -> Citation:
    return Citation(
        nct_id=extract_nct_id(study),
        excerpt=extract_excerpt(study=study, primary_value=primary_value),
    )


def deduplicate_citations(citations: list[Citation]) -> list[Citation]:
    seen_values: set[tuple[str, str]] = set()
    ordered_citations: list[Citation] = []
    for citation in citations:
        citation_key = (citation.nct_id, citation.excerpt)
        if citation_key not in seen_values:
            seen_values.add(citation_key)
            ordered_citations.append(citation)
    return ordered_citations


def phase_sort_key(phase: str) -> tuple[int, str]:
    phase_order = {
        "Early Phase 1": 0,
        "Phase 1": 1,
        "Phase 2": 2,
        "Phase 3": 3,
        "Phase 4": 4,
        "Not Applicable": 5,
    }
    return (phase_order.get(phase, 99), phase)


def accumulate_count(bucket_map: dict[str, CountBucket], key: str, citation: Citation) -> None:
    bucket = bucket_map.setdefault(key, CountBucket())
    bucket.count += 1
    bucket.citations.append(citation)


def build_data_point(values: dict[str, str | int], citations: list[Citation]) -> VisualizationDataPoint:
    return VisualizationDataPoint(**values, citations=deduplicate_citations(citations))


def build_network_data_point(
    source: str,
    target: str,
    weight: int,
    citations: list[Citation],
) -> VisualizationDataPoint:
    return build_data_point(
        values={
            "source": source,
            "target": target,
            "weight": weight,
            "node_id": f"{source}::{target}",
            "node_label": f"{source} -> {target}",
            "node_group": "edge",
        },
        citations=citations,
    )


def build_scatter_data_point(
    year: int,
    enrollment: int,
    study: StudyRecord,
) -> VisualizationDataPoint:
    title = extract_brief_title(study) or extract_nct_id(study)
    return build_data_point(
        values={
            "year": year,
            "enrollment": enrollment,
            "study_label": title,
        },
        citations=[build_citation(study=study, primary_value=title)],
    )


def aggregate_phase_counts(studies: list[StudyRecord]) -> list[VisualizationDataPoint]:
    phase_buckets: dict[str, CountBucket] = {}
    for study in studies:
        phases = extract_phases(study)
        if not phases:
            continue
        for phase in phases:
            accumulate_count(
                bucket_map=phase_buckets,
                key=phase,
                citation=build_citation(study=study, primary_value=phase),
            )
    data_points: list[VisualizationDataPoint] = []
    for phase in sorted(phase_buckets, key=phase_sort_key):
        bucket = phase_buckets[phase]
        data_points.append(
            build_data_point(
                values={"phase": phase, "trial_count": bucket.count},
                citations=bucket.citations,
            )
        )
    return data_points


def aggregate_intervention_type_counts(studies: list[StudyRecord]) -> list[VisualizationDataPoint]:
    intervention_type_buckets: dict[str, CountBucket] = {}
    for study in studies:
        intervention_types = extract_intervention_types(study)
        if not intervention_types:
            continue
        for intervention_type in intervention_types:
            accumulate_count(
                bucket_map=intervention_type_buckets,
                key=intervention_type,
                citation=build_citation(study=study, primary_value=intervention_type),
            )
    data_points: list[VisualizationDataPoint] = []
    sorted_intervention_types = sorted(
        intervention_type_buckets.items(),
        key=lambda item: (-item[1].count, item[0]),
    )
    for intervention_type, bucket in sorted_intervention_types:
        data_points.append(
            build_data_point(
                values={"intervention_type": intervention_type, "trial_count": bucket.count},
                citations=bucket.citations,
            )
        )
    return data_points


def aggregate_status_counts(studies: list[StudyRecord]) -> list[VisualizationDataPoint]:
    status_buckets: dict[str, CountBucket] = {}
    for study in studies:
        status = extract_overall_status(study)
        if status is None:
            continue
        accumulate_count(
            bucket_map=status_buckets,
            key=status,
            citation=build_citation(study=study, primary_value=status),
        )
    data_points: list[VisualizationDataPoint] = []
    sorted_statuses = sorted(
        status_buckets.items(),
        key=lambda item: (-item[1].count, item[0]),
    )
    for status, bucket in sorted_statuses:
        data_points.append(
            build_data_point(
                values={"status": status, "trial_count": bucket.count},
                citations=bucket.citations,
            )
        )
    return data_points


def aggregate_time_series_by_year(studies: list[StudyRecord]) -> list[VisualizationDataPoint]:
    year_buckets: dict[int, CountBucket] = {}
    for study in studies:
        year = extract_start_year(study)
        if year is None:
            continue
        year_key = str(year)
        bucket = year_buckets.setdefault(year, CountBucket())
        bucket.count += 1
        bucket.citations.append(build_citation(study=study, primary_value=year_key))
    data_points: list[VisualizationDataPoint] = []
    for year in sorted(year_buckets):
        bucket = year_buckets[year]
        data_points.append(
            build_data_point(
                values={"year": year, "trial_count": bucket.count},
                citations=bucket.citations,
            )
        )
    return data_points


def aggregate_time_series_by_month(studies: list[StudyRecord]) -> list[VisualizationDataPoint]:
    month_buckets: dict[str, CountBucket] = {}
    for study in studies:
        month = extract_start_month(study)
        if month is None:
            continue
        accumulate_count(
            bucket_map=month_buckets,
            key=month,
            citation=build_citation(study=study, primary_value=month),
        )
    data_points: list[VisualizationDataPoint] = []
    for month in sorted(month_buckets):
        bucket = month_buckets[month]
        data_points.append(
            build_data_point(
                values={"month": month, "trial_count": bucket.count},
                citations=bucket.citations,
            )
        )
    return data_points


def aggregate_sponsor_class_counts(studies: list[StudyRecord]) -> list[VisualizationDataPoint]:
    sponsor_class_buckets: dict[str, CountBucket] = {}
    for study in studies:
        sponsor_class = extract_sponsor_class(study)
        if sponsor_class is None:
            continue
        accumulate_count(
            bucket_map=sponsor_class_buckets,
            key=sponsor_class,
            citation=build_citation(study=study, primary_value=sponsor_class),
        )
    data_points: list[VisualizationDataPoint] = []
    sorted_sponsor_classes = sorted(
        sponsor_class_buckets.items(),
        key=lambda item: (-item[1].count, item[0]),
    )
    for sponsor_class, bucket in sorted_sponsor_classes:
        data_points.append(
            build_data_point(
                values={"sponsor_class": sponsor_class, "trial_count": bucket.count},
                citations=bucket.citations,
            )
        )
    return data_points


def aggregate_country_recruiting_counts(studies: list[StudyRecord]) -> list[VisualizationDataPoint]:
    country_buckets: dict[str, CountBucket] = {}
    for study in studies:
        countries = extract_countries(study)
        if not countries:
            continue
        for country in countries:
            accumulate_count(
                bucket_map=country_buckets,
                key=country,
                citation=build_citation(study=study, primary_value=country),
            )
    data_points: list[VisualizationDataPoint] = []
    sorted_countries = sorted(
        country_buckets.items(),
        key=lambda item: (-item[1].count, item[0]),
    )
    for country, bucket in sorted_countries:
        data_points.append(
            build_data_point(
                values={"country": country, "trial_count": bucket.count},
                citations=bucket.citations,
            )
        )
    return data_points


def enrollment_bucket_label(enrollment: int) -> str:
    if enrollment < 50:
        return "0-49"
    if enrollment < 100:
        return "50-99"
    if enrollment < 250:
        return "100-249"
    if enrollment < 500:
        return "250-499"
    if enrollment < 1000:
        return "500-999"
    return "1000+"


def aggregate_enrollment_histogram(studies: list[StudyRecord]) -> list[VisualizationDataPoint]:
    enrollment_buckets: dict[str, CountBucket] = {}
    for study in studies:
        enrollment = extract_enrollment_count(study)
        if enrollment is None or enrollment < 0:
            continue
        bucket_label = enrollment_bucket_label(enrollment)
        accumulate_count(
            bucket_map=enrollment_buckets,
            key=bucket_label,
            citation=build_citation(study=study, primary_value=str(enrollment)),
        )
    bucket_order = ["0-49", "50-99", "100-249", "250-499", "500-999", "1000+"]
    data_points: list[VisualizationDataPoint] = []
    for bucket_label in bucket_order:
        bucket = enrollment_buckets.get(bucket_label)
        if bucket is None:
            continue
        data_points.append(
            build_data_point(
                values={"enrollment_bucket": bucket_label, "trial_count": bucket.count},
                citations=bucket.citations,
            )
        )
    return data_points


def aggregate_enrollment_vs_start_year_scatter(studies: list[StudyRecord]) -> list[VisualizationDataPoint]:
    data_points: list[VisualizationDataPoint] = []
    for study in studies:
        year = extract_start_year(study)
        enrollment = extract_enrollment_count(study)
        if year is None or enrollment is None or enrollment < 0:
            continue
        data_points.append(build_scatter_data_point(year=year, enrollment=enrollment, study=study))
    return sorted(
        data_points,
        key=lambda item: (
            int(item.year) if isinstance(item.year, int) else 0,
            int(item.enrollment) if isinstance(item.enrollment, int) else 0,
        ),
    )


def aggregate_sponsor_drug_network(studies: list[StudyRecord]) -> list[VisualizationDataPoint]:
    edge_buckets: dict[tuple[str, str], CountBucket] = {}
    for study in studies:
        sponsor_name = extract_sponsor_name(study)
        intervention_names = extract_intervention_names(study)
        if sponsor_name is None or not intervention_names:
            continue
        for intervention_name in intervention_names:
            edge_key = (sponsor_name, intervention_name)
            bucket = edge_buckets.setdefault(edge_key, CountBucket())
            bucket.count += 1
            bucket.citations.append(
                build_citation(
                    study=study,
                    primary_value=f"{sponsor_name} -> {intervention_name}",
                )
            )
    data_points: list[VisualizationDataPoint] = []
    sorted_edges = sorted(
        edge_buckets.items(),
        key=lambda item: (-item[1].count, item[0][0], item[0][1]),
    )
    for (source, target), bucket in sorted_edges:
        data_points.append(build_network_data_point(source=source, target=target, weight=bucket.count, citations=bucket.citations))
    return data_points


def aggregate_drug_condition_network(studies: list[StudyRecord]) -> list[VisualizationDataPoint]:
    edge_buckets: dict[tuple[str, str], CountBucket] = {}
    for study in studies:
        intervention_names = extract_intervention_names(study)
        conditions = extract_conditions(study)
        if not intervention_names or not conditions:
            continue
        for intervention_name in intervention_names:
            for condition in conditions:
                edge_key = (intervention_name, condition)
                bucket = edge_buckets.setdefault(edge_key, CountBucket())
                bucket.count += 1
                bucket.citations.append(
                    build_citation(
                        study=study,
                        primary_value=f"{intervention_name} -> {condition}",
                    )
                )
    data_points: list[VisualizationDataPoint] = []
    sorted_edges = sorted(
        edge_buckets.items(),
        key=lambda item: (-item[1].count, item[0][0], item[0][1]),
    )
    for (source, target), bucket in sorted_edges:
        data_points.append(build_network_data_point(source=source, target=target, weight=bucket.count, citations=bucket.citations))
    return data_points


def aggregate_drug_co_occurrence_network(studies: list[StudyRecord]) -> list[VisualizationDataPoint]:
    edge_buckets: dict[tuple[str, str], CountBucket] = {}
    for study in studies:
        intervention_names = sorted(extract_intervention_names(study))
        if len(intervention_names) < 2:
            continue
        for index, source in enumerate(intervention_names[:-1]):
            for target in intervention_names[index + 1 :]:
                edge_key = (source, target)
                bucket = edge_buckets.setdefault(edge_key, CountBucket())
                bucket.count += 1
                bucket.citations.append(
                    build_citation(
                        study=study,
                        primary_value=f"{source} -> {target}",
                    )
                )
    data_points: list[VisualizationDataPoint] = []
    sorted_edges = sorted(
        edge_buckets.items(),
        key=lambda item: (-item[1].count, item[0][0], item[0][1]),
    )
    for (source, target), bucket in sorted_edges:
        data_points.append(build_network_data_point(source=source, target=target, weight=bucket.count, citations=bucket.citations))
    return data_points


def generate_title(plan: QueryPlan) -> str:
    subject_value = next(
        (
            value
            for key in ("query.intr", "query.cond", "query.term", "query.locn")
            if (value := plan.api_parameters.get(key))
        ),
        None,
    )
    if isinstance(subject_value, list):
        subject_label = ", ".join(subject_value)
    else:
        subject_label = subject_value
    title_map = {
        AggregationStrategy.GROUP_BY_PHASE_COUNT: "Trials by Phase",
        AggregationStrategy.GROUP_BY_STATUS_COUNT: "Trials by Status",
        AggregationStrategy.TIME_SERIES_BY_YEAR: "Trials Over Time",
        AggregationStrategy.TIME_SERIES_BY_MONTH: "Trials Over Time",
        AggregationStrategy.GROUP_BY_INTERVENTION_TYPE_COUNT: "Trials by Intervention Type",
        AggregationStrategy.GROUP_BY_SPONSOR_CLASS_COUNT: "Trials by Sponsor Class",
        AggregationStrategy.GROUP_BY_COUNTRY_RECRUITING_COUNT: "Recruiting Trials by Country",
        AggregationStrategy.HISTOGRAM_ENROLLMENT: "Enrollment Distribution",
        AggregationStrategy.SCATTER_ENROLLMENT_VS_START_YEAR: "Enrollment vs Start Year",
        AggregationStrategy.NETWORK_SPONSOR_DRUG: "Sponsor to Drug Network",
        AggregationStrategy.NETWORK_DRUG_CONDITION: "Drug to Condition Network",
        AggregationStrategy.NETWORK_DRUG_CO_OCCURRENCE: "Drug Co-occurrence Network",
    }
    base_title = title_map.get(plan.aggregation_strategy, "Clinical Trials Visualization")
    if isinstance(subject_label, str) and subject_label.strip():
        return f"{base_title} for {subject_label.strip()}"[:200]
    return base_title


def build_meta(plan: QueryPlan, studies: list[StudyRecord]) -> ResponseMeta:
    return ResponseMeta(
        filters=plan.api_parameters,
        sources=["clinicaltrials.gov"],
        notes=[],
        record_count=len(studies),
        generated_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
    )


def route_aggregation(studies: list[StudyRecord], plan: QueryPlan) -> list[VisualizationDataPoint]:
    match plan.aggregation_strategy:
        case AggregationStrategy.GROUP_BY_PHASE_COUNT:
            return aggregate_phase_counts(studies)
        case AggregationStrategy.GROUP_BY_STATUS_COUNT:
            return aggregate_status_counts(studies)
        case AggregationStrategy.TIME_SERIES_BY_YEAR:
            return aggregate_time_series_by_year(studies)
        case AggregationStrategy.TIME_SERIES_BY_MONTH:
            return aggregate_time_series_by_month(studies)
        case AggregationStrategy.GROUP_BY_INTERVENTION_TYPE_COUNT:
            return aggregate_intervention_type_counts(studies)
        case AggregationStrategy.GROUP_BY_SPONSOR_CLASS_COUNT:
            return aggregate_sponsor_class_counts(studies)
        case AggregationStrategy.GROUP_BY_COUNTRY_RECRUITING_COUNT:
            return aggregate_country_recruiting_counts(studies)
        case AggregationStrategy.HISTOGRAM_ENROLLMENT:
            return aggregate_enrollment_histogram(studies)
        case AggregationStrategy.SCATTER_ENROLLMENT_VS_START_YEAR:
            return aggregate_enrollment_vs_start_year_scatter(studies)
        case AggregationStrategy.NETWORK_SPONSOR_DRUG:
            return aggregate_sponsor_drug_network(studies)
        case AggregationStrategy.NETWORK_DRUG_CONDITION:
            return aggregate_drug_condition_network(studies)
        case AggregationStrategy.NETWORK_DRUG_CO_OCCURRENCE:
            return aggregate_drug_co_occurrence_network(studies)
        case _:
            raise DataProcessingError(
                f"Unsupported aggregation strategy: {plan.aggregation_strategy.value}"
            )


def process_data(
    studies: list[dict[str, Any]],
    plan: QueryPlan,
    include_citations: bool = True,
) -> VisualizationResponse:
    data_points = route_aggregation(studies=studies, plan=plan)
    if not include_citations:
        for data_point in data_points:
            data_point.citations = []
    visualization = VisualizationSpec(
        type=plan.visualization_type,
        title=generate_title(plan),
        encoding=plan.encoding,
        data=data_points,
    )
    return VisualizationResponse(
        visualization=visualization,
        meta=build_meta(plan=plan, studies=studies),
    )
