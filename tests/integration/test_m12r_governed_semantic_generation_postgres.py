import json

import pytest
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.config import get_settings
from app.db.session import build_reader_engine
from evaluation.m12r_governed_semantic_generation import DATASET_PATH, prepare_dataset


def test_m12r_gold_dataset_is_complete_and_postgres_available() -> None:
    try:
        with build_reader_engine(get_settings()).connect() as connection:
            connection.execute(text("SELECT 1"))
    except SQLAlchemyError:
        pytest.skip("PostgreSQL is not available")
    dataset = prepare_dataset()
    assert len(dataset.cases) == 109
    assert dataset.historical_overlap_count == 0
    assert len(json.loads(DATASET_PATH.read_text())) >= 1 if DATASET_PATH.exists() else True
