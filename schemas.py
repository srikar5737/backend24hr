from __future__ import annotations

from typing import Annotated
from enum import Enum

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field
from pydantic import StrictBool
from pydantic import StrictFloat
from pydantic import StrictInt
from pydantic import StrictStr
from pydantic import StringConstraints
from pydantic import model_validator


NonEmptyString = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
ShortText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=50)]
MediumText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
LongText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
QueryText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)]
ExcerptText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]
ScalarValue = StrictStr | StrictInt | StrictFloat | StrictBool | None


class VisualizationType(str, Enum):
    BAR_CHART = "bar_chart"
    TIME_SERIES = "time_series"
    SCATTER_PLOT = "scatter_plot"
    HISTOGRAM = "histogram"
    NETWORK_GRAPH = "network_graph"


class TemporalGranularity(str, Enum):
    DAY = "day"
    MONTH = "month"
    QUARTER = "quarter"
    YEAR = "year"


class AggregationOp(str, Enum):
    COUNT = "count"
    DISTINCT_COUNT = "distinct_count"
    SUM = "sum"
    AVG = "avg"
    MIN = "min"
    MAX = "max"
    NONE = "none"


class QueryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    query: QueryText
    drug_name: LongText | None = None
    condition: LongText | None = None
    trial_phase: ShortText | None = None
    sponsor: LongText | None = None
    sponsor_class: MediumText | None = None
    country: MediumText | None = None
    state: MediumText | None = None
    city: MediumText | None = None
    study_type: MediumText | None = None
    intervention_type: MediumText | None = None
    recruitment_status: MediumText | None = None
    overall_status: MediumText | None = None
    primary_purpose: MediumText | None = None
    sex: ShortText | None = None
    age_group: ShortText | None = None
    start_year: StrictInt | None = Field(default=None, ge=1900, le=2100)
    end_year: StrictInt | None = Field(default=None, ge=1900, le=2100)
    limit: StrictInt | None = Field(default=None, ge=1, le=1000)
    include_citations: StrictBool = True

    @model_validator(mode="after")
    def validate_year_range(self) -> QueryRequest:
        if self.start_year is not None and self.end_year is not None and self.start_year > self.end_year:
            raise ValueError("start_year must be less than or equal to end_year")
        return self


class Citation(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    nct_id: ShortText
    excerpt: ExcerptText


class VisualizationFieldEncoding(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    field: MediumText
    label: MediumText | None = None
    data_type: ShortText
    aggregate: AggregationOp | None = None
    time_granularity: TemporalGranularity | None = None
    unit: ShortText | None = None


class VisualizationEncoding(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    x: VisualizationFieldEncoding | None = None
    y: VisualizationFieldEncoding | None = None
    color: VisualizationFieldEncoding | None = None
    series: VisualizationFieldEncoding | None = None
    size: VisualizationFieldEncoding | None = None
    label: VisualizationFieldEncoding | None = None
    node_id: VisualizationFieldEncoding | None = None
    node_label: VisualizationFieldEncoding | None = None
    node_group: VisualizationFieldEncoding | None = None
    source: VisualizationFieldEncoding | None = None
    target: VisualizationFieldEncoding | None = None
    weight: VisualizationFieldEncoding | None = None
    tooltip: list[VisualizationFieldEncoding] = Field(default_factory=list)


class VisualizationDataPoint(BaseModel):
    model_config = ConfigDict(extra="allow", strict=True)

    citations: list[Citation] = Field(default_factory=list)


class VisualizationSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    type: VisualizationType
    title: LongText
    encoding: VisualizationEncoding
    data: list[VisualizationDataPoint] = Field(default_factory=list, max_length=5000)


class ResponseMeta(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    filters: dict[str, ScalarValue | list[ScalarValue]] = Field(default_factory=dict)
    sources: list[NonEmptyString] = Field(default_factory=lambda: ["clinicaltrials.gov"])
    notes: list[NonEmptyString] = Field(default_factory=list)
    record_count: StrictInt | None = Field(default=None, ge=0)
    generated_at: MediumText | None = None


class VisualizationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    visualization: VisualizationSpec
    meta: ResponseMeta
