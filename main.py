from __future__ import annotations

from fastapi import FastAPI
from fastapi import Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from models.schemas import QueryRequest
from models.schemas import VisualizationResponse
from services.agent import OpenAIConfigurationError
from services.agent import QueryAnalysisError
from services.agent import analyze_query
from services.api_client import ClinicalTrialsAPIError
from services.api_client import ClinicalTrialsAPITimeoutError
from services.api_client import fetch_trials
from services.processor import DataProcessingError
from services.processor import process_data


app = FastAPI(
    title="ClinicalTrials.gov Query-to-Visualization Agent",
    description="AI-enabled backend that converts clinical trial questions into structured visualization specifications backed by ClinicalTrials.gov data.",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(QueryAnalysisError)
async def handle_query_analysis_error(_: Request, exc: QueryAnalysisError) -> JSONResponse:
    return JSONResponse(status_code=400, content={"detail": str(exc)})


@app.exception_handler(ClinicalTrialsAPITimeoutError)
async def handle_clinical_trials_timeout_error(
    _: Request,
    exc: ClinicalTrialsAPITimeoutError,
) -> JSONResponse:
    return JSONResponse(status_code=504, content={"detail": str(exc)})


@app.exception_handler(ClinicalTrialsAPIError)
async def handle_clinical_trials_api_error(_: Request, exc: ClinicalTrialsAPIError) -> JSONResponse:
    return JSONResponse(status_code=502, content={"detail": str(exc)})


@app.exception_handler(DataProcessingError)
async def handle_data_processing_error(_: Request, exc: DataProcessingError) -> JSONResponse:
    return JSONResponse(status_code=500, content={"detail": str(exc)})


@app.exception_handler(OpenAIConfigurationError)
async def handle_openai_configuration_error(_: Request, exc: OpenAIConfigurationError) -> JSONResponse:
    return JSONResponse(status_code=500, content={"detail": str(exc)})


@app.exception_handler(Exception)
async def handle_unexpected_error(_: Request, exc: Exception) -> JSONResponse:
    return JSONResponse(status_code=500, content={"detail": str(exc)})


@app.post("/api/v1/visualize", response_model=VisualizationResponse)
async def visualize(request: QueryRequest) -> VisualizationResponse:
    plan = await analyze_query(request)
    studies = await fetch_trials(plan.api_parameters, request.limit)
    return process_data(
        studies=studies,
        plan=plan,
        include_citations=request.include_citations,
    )
