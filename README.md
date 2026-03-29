# ClinicalTrials.gov Query-to-Visualization Agent (Backend)

## 1) Project Overview

This project is an AI-enabled backend that converts natural-language clinical-trial questions into structured visualization JSON backed by real ClinicalTrials.gov API data.

The service performs four steps:
1. Interpret the user question and optional structured filters.
2. Retrieve relevant studies from the ClinicalTrials.gov Data API.
3. Select a suitable visualization type and encoding.
4. Return a frontend-ready visualization specification with traceable citations.

The architecture is intentionally split:
- **LLM planner (`services/agent.py`)**: returns a typed `QueryPlan` only.
- **Deterministic executor (`services/api_client.py`, `services/processor.py`)**: fetches real data, aggregates, builds chart data, and attaches citations.

This design avoids hallucinated counts because computed values are produced only in deterministic Python from API responses.

## 2) Data Source

- Authoritative source: [ClinicalTrials.gov Data API](https://clinicaltrials.gov/data-api/api)
- Endpoint used: `https://clinicaltrials.gov/api/v2/studies`
- The backend can select any needed fields and query parameters, and applies bounded pagination for reliable response times.

## 3) How To Run

### Requirements
- Python `3.11+`

### Setup
```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### Environment
```bash
export OPENAI_API_KEY="your_key"
# optional
export OPENAI_MODEL="gpt-4o"
```

### Start API server
```bash
uvicorn main:app --reload
```

### Generate assignment example runs
```bash
python examples/generate_examples.py
```

### Run test suite
```bash
python3 -m unittest discover -s tests -v
```

## 4) API Contract

### Endpoint
- `POST /api/v1/visualize`

### 4.1 Request Schema (Inputs)

The request must include:
- **Required**: `query` (natural-language question)
- **Optional**: structured fields below

All request fields are defined in `models/schemas.py` with `extra="forbid"` and `strict=True`.

| Field | Type | Required | Validation / Allowed Range |
|---|---|---:|---|
| `query` | string | yes | 1..1000 chars, non-empty |
| `drug_name` | string | no | 1..200 chars |
| `condition` | string | no | 1..200 chars |
| `trial_phase` | string | no | 1..50 chars |
| `sponsor` | string | no | 1..200 chars |
| `sponsor_class` | string | no | 1..100 chars |
| `country` | string | no | 1..100 chars |
| `state` | string | no | 1..100 chars |
| `city` | string | no | 1..100 chars |
| `study_type` | string | no | 1..100 chars |
| `intervention_type` | string | no | 1..100 chars |
| `recruitment_status` | string | no | 1..100 chars |
| `overall_status` | string | no | 1..100 chars |
| `primary_purpose` | string | no | 1..100 chars |
| `sex` | string | no | 1..50 chars |
| `age_group` | string | no | 1..50 chars |
| `start_year` | integer | no | 1900..2100 |
| `end_year` | integer | no | 1900..2100, must be `>= start_year` when both provided |
| `limit` | integer | no | 1..1000 (internally capped to 500 records by API client) |
| `include_citations` | boolean | no | default `true` |

Example request:
```json
{
  "query": "How has the number of trials for Pembrolizumab changed per year since 2015?",
  "drug_name": "Pembrolizumab",
  "start_year": 2015,
  "include_citations": true
}
```

### 4.2 Response Schema (Outputs)

Top-level response:
- `visualization` (required): frontend rendering spec
- `meta` (required): execution metadata

#### `visualization` object

| Field | Type | Required | Description |
|---|---|---:|---|
| `type` | enum | yes | one of `bar_chart`, `time_series`, `scatter_plot`, `histogram`, `network_graph` |
| `title` | string | yes | human-readable chart title |
| `encoding` | object | yes | mapping from data fields to visual channels |
| `data` | array | yes | list of data points for rendering (max 5000) |

#### `encoding` channels

Supported channels: `x`, `y`, `color`, `series`, `size`, `label`, `node_id`, `node_label`, `node_group`, `source`, `target`, `weight`, `tooltip[]`.

Each channel uses:
- `field` (string)
- `data_type` (string)
- optional `label` (string)
- optional `aggregate` (`count`, `distinct_count`, `sum`, `avg`, `min`, `max`, `none`)
- optional `time_granularity` (`day`, `month`, `quarter`, `year`)
- optional `unit` (string)

#### `data[]` item

Each datum contains dynamic measure dimensions (for example: `phase`, `year`, `country`, `trial_count`, `source`, `target`, `weight`) and always includes:

- `citations`: array of citation objects, each containing:
  - `nct_id` (string)
  - `excerpt` (string, exact value/field snippet derived from API-backed record context)

If `include_citations=false` is sent in request, data points still include `citations`, but as empty arrays.

#### `meta` object

| Field | Type | Required | Description |
|---|---|---:|---|
| `filters` | object | yes | effective API/planning filters used |
| `sources` | array[string] | yes | source systems, defaults to `["clinicaltrials.gov"]` |
| `notes` | array[string] | yes | planner/execution notes |
| `record_count` | integer or null | no | number of source studies used |
| `generated_at` | string or null | no | UTC timestamp |

Example response shape:
```json
{
  "visualization": {
    "type": "bar_chart",
    "title": "Trials by Phase for Pembrolizumab",
    "encoding": {
      "x": { "field": "phase", "data_type": "nominal" },
      "y": { "field": "trial_count", "data_type": "quantitative", "aggregate": "count" }
    },
    "data": [
      {
        "phase": "Phase 3",
        "trial_count": 41,
        "citations": [
          {
            "nct_id": "NCT01234567",
            "excerpt": "Phase 3 randomized study evaluating pembrolizumab..."
          }
        ]
      }
    ]
  },
  "meta": {
    "filters": { "query.intr": "Pembrolizumab" },
    "sources": ["clinicaltrials.gov"],
    "notes": [],
    "record_count": 120,
    "generated_at": "2026-03-19T02:00:00"
  }
}
```

## 5) Visualization Coverage

This backend supports all visualization families requested in the assignment:

- `bar_chart`: phase/status/sponsor-class/country distributions and comparisons
- `time_series`: yearly/monthly trend counts
- `scatter_plot`: enrollment vs start year relationships
- `histogram`: enrollment bucket distributions
- `network_graph`: sponsor-drug, drug-condition, and drug co-occurrence relationships

Implemented deterministic strategies:
- `time_series_by_year`
- `time_series_by_month`
- `group_by_phase_count`
- `group_by_status_count`
- `group_by_intervention_type_count`
- `group_by_sponsor_class_count`
- `group_by_country_recruiting_count`
- `histogram_enrollment`
- `scatter_enrollment_vs_start_year`
- `network_sponsor_drug`
- `network_drug_condition`
- `network_drug_co_occurrence`

## 6) Deep Citations (Bonus Traceability)

Deep citation behavior:
- Every emitted visualized datum includes supporting references to trial records that contributed to that datum.
- Every reference includes:
  - `nct_id`
  - supporting `excerpt`
- Citations are attached during deterministic aggregation (not post-processed guesses).

This allows a frontend and reviewer to trace bars/points/edges directly back to contributing studies.

## 7) Example Runs (Actual JSON Outputs)

Generated outputs are in `examples/`:
- `examples/time_trends.json`
- `examples/distributions.json`
- `examples/comparisons.json`
- `examples/geographic.json`
- `examples/networks.json`

These are real outputs produced by the backend pipeline and satisfy the "3-5 example queries with actual JSON outputs" requirement.

## 8) Key Design Decisions and Tradeoffs

1. **Planner/executor split**  
   LLM produces only a typed `QueryPlan`; deterministic Python computes all factual outputs.

2. **Strict typed schemas**  
   Pydantic strict models (`extra="forbid"`) prevent ambiguous contracts and support frontend reliability.

3. **Bounded pagination**  
   Client limits pages/records to keep response latency predictable (`MAX_PAGES`, `MAX_RECORDS`).

4. **Flexible data point shape with required citations**  
   Output allows strategy-specific keys while enforcing citation structure.

5. **Single coherent processing router**  
   Multiple query classes share one strategy router rather than one-off hardcoded endpoints.

## 9) Validation Approach

Validation performed through:
- **Schema validation**: strict Pydantic validation for all request/response objects.
- **Real API grounding checks**: outputs generated from live ClinicalTrials.gov records.
- **Deterministic aggregation checks**: counts/grouping executed in code, not by LLM.
- **Manual inspection of example runs**: verified:
  - visualization type matches query intent,
  - encoding fields align with data payload fields,
  - each datum contains valid `citations` with `nct_id` and excerpt,
  - metadata captures filters/source/record count.

## 10) Limitations and Future Work

- Add automated tests (planner schema checks, API pagination behavior, aggregation correctness fixtures).
- Add caching for repeated queries.
- Improve advanced filter mapping for edge-case query phrasing.
- Add optional ranking/Top-K controls for very large network outputs.
- Add optional lightweight frontend demo (not required by assignment).

## 11) Integrity Note (AI Tools and Engineering Judgment)

### Tools used
- Python, FastAPI, Pydantic, httpx, OpenAI API client
- ClinicalTrials.gov Data API
- AI assistance for implementation acceleration and documentation drafting

### How correctness was validated
- Strict schema enforcement at request/response boundaries
- Deterministic data processing from real API records
- Multiple end-to-end example runs with actual JSON outputs
- Manual verification of citation traceability and chart/data consistency

### Deliberate design/implementation vs AI-adapted
- **Deliberately designed/implemented**:
  - planner/executor separation to reduce hallucination risk
  - aggregation strategy router and output schema contract
  - deep citation attachment model
  - bounded pagination and operational safety constraints
- **AI-generated/adapted with review**:
  - portions of boilerplate code and prompt phrasing
  - documentation drafting iterations
  - some implementation scaffolding, then manually reviewed and adjusted
