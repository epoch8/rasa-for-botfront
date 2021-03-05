from pathlib import Path

import numpy as np
import pytest
from unittest.mock import Mock
from typing import List, Text, Dict, Any
from _pytest.monkeypatch import MonkeyPatch

import rasa.model
from rasa.shared.nlu.training_data.features import Features
from rasa.nlu import train
from rasa.nlu.classifiers import LABEL_RANKING_LENGTH
from rasa.nlu.config import RasaNLUModelConfig
from rasa.shared.nlu.constants import (
    TEXT,
    INTENT,
    ENTITIES,
    FEATURE_TYPE_SENTENCE,
    FEATURE_TYPE_SEQUENCE,
)
from rasa.utils.tensorflow.constants import (
    LOSS_TYPE,
    RANDOM_SEED,
    RANKING_LENGTH,
    EPOCHS,
    MASKED_LM,
    TENSORBOARD_LOG_LEVEL,
    TENSORBOARD_LOG_DIR,
    EVAL_NUM_EPOCHS,
    EVAL_NUM_EXAMPLES,
    CHECKPOINT_MODEL,
    BILOU_FLAG,
    ENTITY_RECOGNITION,
    INTENT_CLASSIFICATION,
    MODEL_CONFIDENCE,
)
from rasa.nlu.components import ComponentBuilder
from rasa.nlu.tokenizers.whitespace_tokenizer import WhitespaceTokenizer
from rasa.nlu.classifiers.diet_classifier import DIETClassifier
from rasa.nlu.model import Interpreter
from rasa.shared.nlu.training_data.message import Message
from rasa.shared.nlu.training_data.training_data import TrainingData
from rasa.utils import train_utils
from rasa.shared.constants import DIAGNOSTIC_DATA
from tests.conftest import DEFAULT_NLU_DATA
from tests.nlu.conftest import DEFAULT_DATA_PATH
from rasa.core.agent import Agent


def test_compute_default_label_features():
    label_features = [
        Message(data={TEXT: "test a"}),
        Message(data={TEXT: "test b"}),
        Message(data={TEXT: "test c"}),
        Message(data={TEXT: "test d"}),
    ]

    output = DIETClassifier._compute_default_label_features(label_features)

    output = output[0]

    for i, o in enumerate(output):
        assert isinstance(o, np.ndarray)
        assert o[0][i] == 1
        assert o.shape == (1, len(label_features))


@pytest.mark.parametrize(
    "messages, expected",
    [
        (
            [
                Message(
                    data={TEXT: "test a"},
                    features=[
                        Features(np.zeros(1), FEATURE_TYPE_SEQUENCE, TEXT, "test"),
                        Features(np.zeros(1), FEATURE_TYPE_SENTENCE, TEXT, "test"),
                    ],
                ),
                Message(
                    data={TEXT: "test b"},
                    features=[
                        Features(np.zeros(1), FEATURE_TYPE_SEQUENCE, TEXT, "test"),
                        Features(np.zeros(1), FEATURE_TYPE_SENTENCE, TEXT, "test"),
                    ],
                ),
            ],
            True,
        ),
        (
            [
                Message(
                    data={TEXT: "test a"},
                    features=[
                        Features(np.zeros(1), FEATURE_TYPE_SEQUENCE, INTENT, "test"),
                        Features(np.zeros(1), FEATURE_TYPE_SENTENCE, INTENT, "test"),
                    ],
                )
            ],
            False,
        ),
        (
            [
                Message(
                    data={TEXT: "test a"},
                    features=[
                        Features(np.zeros(2), FEATURE_TYPE_SEQUENCE, INTENT, "test")
                    ],
                )
            ],
            False,
        ),
    ],
)
def test_check_labels_features_exist(messages, expected):
    attribute = TEXT
    classifier = DIETClassifier()
    assert classifier._check_labels_features_exist(messages, attribute) == expected


@pytest.mark.parametrize(
    "messages, entity_expected",
    [
        (
            [
                Message(
                    data={
                        TEXT: "test a",
                        INTENT: "intent a",
                        ENTITIES: [
                            {"start": 0, "end": 4, "value": "test", "entity": "test"}
                        ],
                    },
                ),
                Message(
                    data={
                        TEXT: "test b",
                        INTENT: "intent b",
                        ENTITIES: [
                            {"start": 0, "end": 4, "value": "test", "entity": "test"}
                        ],
                    },
                ),
            ],
            True,
        ),
        (
            [
                Message(data={TEXT: "test a", INTENT: "intent a"},),
                Message(data={TEXT: "test b", INTENT: "intent b"},),
            ],
            False,
        ),
    ],
)
def test_model_data_signature_with_entities(
    messages: List[Message], entity_expected: bool
):
    classifier = DIETClassifier({"BILOU_flag": False})
    training_data = TrainingData(messages)

    # create tokens for entity parsing inside DIET
    tokenizer = WhitespaceTokenizer()
    tokenizer.train(training_data)

    model_data = classifier.preprocess_train_data(training_data)
    entity_exists = "entities" in model_data.get_signature().keys()
    assert entity_exists == entity_expected


async def _train_persist_load_with_different_settings(
    pipeline: List[Dict[Text, Any]],
    component_builder: ComponentBuilder,
    tmp_path: Path,
    should_finetune: bool,
):
    _config = RasaNLUModelConfig({"pipeline": pipeline, "language": "en"})

    (trainer, trained, persisted_path) = await train(
        _config,
        path=str(tmp_path),
        data="data/examples/rasa/demo-rasa-multi-intent.md",
        component_builder=component_builder,
    )

    assert trainer.pipeline
    assert trained.pipeline

    loaded = Interpreter.load(
        persisted_path,
        component_builder,
        new_config=_config if should_finetune else None,
    )

    assert loaded.pipeline
    assert loaded.parse("Rasa is great!") == trained.parse("Rasa is great!")


@pytest.mark.skip_on_windows
async def test_train_persist_load_with_different_settings_non_windows(
    component_builder: ComponentBuilder, tmp_path: Path
):
    pipeline = [
        {
            "name": "WhitespaceTokenizer",
            "intent_tokenization_flag": True,
            "intent_split_symbol": "+",
        },
        {"name": "CountVectorsFeaturizer"},
        {"name": "DIETClassifier", MASKED_LM: True, EPOCHS: 1},
    ]
    await _train_persist_load_with_different_settings(
        pipeline, component_builder, tmp_path, should_finetune=False
    )
    await _train_persist_load_with_different_settings(
        pipeline, component_builder, tmp_path, should_finetune=True
    )


async def test_train_persist_load_with_different_settings(component_builder, tmpdir):
    pipeline = [
        {"name": "WhitespaceTokenizer"},
        {"name": "CountVectorsFeaturizer"},
        {"name": "DIETClassifier", LOSS_TYPE: "margin", EPOCHS: 1},
    ]
    await _train_persist_load_with_different_settings(
        pipeline, component_builder, tmpdir, should_finetune=False
    )
    await _train_persist_load_with_different_settings(
        pipeline, component_builder, tmpdir, should_finetune=True
    )


async def test_train_persist_load_with_only_entity_recognition(
    component_builder, tmpdir
):
    pipeline = [
        {"name": "WhitespaceTokenizer"},
        {"name": "CountVectorsFeaturizer"},
        {
            "name": "DIETClassifier",
            ENTITY_RECOGNITION: True,
            INTENT_CLASSIFICATION: False,
            EPOCHS: 1,
        },
    ]
    await _train_persist_load_with_different_settings(
        pipeline, component_builder, tmpdir, should_finetune=False
    )
    await _train_persist_load_with_different_settings(
        pipeline, component_builder, tmpdir, should_finetune=True
    )


async def test_train_persist_load_with_only_intent_classification(
    component_builder, tmpdir
):
    pipeline = [
        {"name": "WhitespaceTokenizer"},
        {"name": "CountVectorsFeaturizer"},
        {
            "name": "DIETClassifier",
            ENTITY_RECOGNITION: False,
            INTENT_CLASSIFICATION: True,
            EPOCHS: 1,
        },
    ]
    await _train_persist_load_with_different_settings(
        pipeline, component_builder, tmpdir, should_finetune=False
    )
    await _train_persist_load_with_different_settings(
        pipeline, component_builder, tmpdir, should_finetune=True
    )


async def test_raise_error_on_incorrect_pipeline(component_builder, tmp_path: Path):
    _config = RasaNLUModelConfig(
        {
            "pipeline": [
                {"name": "WhitespaceTokenizer"},
                {"name": "DIETClassifier", EPOCHS: 1},
            ],
            "language": "en",
        }
    )

    with pytest.raises(Exception) as e:
        await train(
            _config,
            path=str(tmp_path),
            data=DEFAULT_DATA_PATH,
            component_builder=component_builder,
        )

    assert "'DIETClassifier' requires 'Featurizer'" in str(e.value)


def as_pipeline(*components):
    return [{"name": c} for c in components]


@pytest.mark.parametrize(
    "classifier_params, data_path, output_length, output_should_sum_to_1",
    [
        (
            {RANDOM_SEED: 42, EPOCHS: 1},
            "data/test/many_intents.md",
            10,
            True,
        ),  # default config
        (
            {RANDOM_SEED: 42, RANKING_LENGTH: 0, EPOCHS: 1},
            "data/test/many_intents.md",
            LABEL_RANKING_LENGTH,
            False,
        ),  # no normalization
        (
            {RANDOM_SEED: 42, RANKING_LENGTH: 3, EPOCHS: 1},
            "data/test/many_intents.md",
            3,
            True,
        ),  # lower than default ranking_length
        (
            {RANDOM_SEED: 42, RANKING_LENGTH: 12, EPOCHS: 1},
            "data/test/many_intents.md",
            LABEL_RANKING_LENGTH,
            False,
        ),  # higher than default ranking_length
        (
            {RANDOM_SEED: 42, EPOCHS: 1},
            DEFAULT_NLU_DATA,
            7,
            True,
        ),  # less intents than default ranking_length
    ],
)
async def test_softmax_normalization(
    component_builder,
    tmp_path,
    classifier_params,
    data_path,
    output_length,
    output_should_sum_to_1,
):
    pipeline = as_pipeline(
        "WhitespaceTokenizer", "CountVectorsFeaturizer", "DIETClassifier"
    )
    assert pipeline[2]["name"] == "DIETClassifier"
    pipeline[2].update(classifier_params)

    _config = RasaNLUModelConfig({"pipeline": pipeline})
    (trained_model, _, persisted_path) = await train(
        _config, path=str(tmp_path), data=data_path, component_builder=component_builder
    )
    loaded = Interpreter.load(persisted_path, component_builder)

    parse_data = loaded.parse("hello")
    intent_ranking = parse_data.get("intent_ranking")
    # check that the output was correctly truncated after normalization
    assert len(intent_ranking) == output_length

    # check whether normalization had the expected effect
    output_sums_to_1 = sum(
        [intent.get("confidence") for intent in intent_ranking]
    ) == pytest.approx(1)
    assert output_sums_to_1 == output_should_sum_to_1

    # check whether the normalization of rankings is reflected in intent prediction
    assert parse_data.get("intent") == intent_ranking[0]


@pytest.mark.parametrize(
    "classifier_params, prediction_min, prediction_max, output_length",
    [
        (
            {RANDOM_SEED: 42, EPOCHS: 1, MODEL_CONFIDENCE: "cosine"},
            -1,
            1,
            LABEL_RANKING_LENGTH,
        ),
        (
            {RANDOM_SEED: 42, EPOCHS: 1, MODEL_CONFIDENCE: "inner"},
            -1e9,
            1e9,
            LABEL_RANKING_LENGTH,
        ),
    ],
)
async def test_cross_entropy_without_normalization(
    component_builder: ComponentBuilder,
    tmp_path: Path,
    classifier_params: Dict[Text, Any],
    prediction_min: float,
    prediction_max: float,
    output_length: int,
    monkeypatch: MonkeyPatch,
):
    pipeline = as_pipeline(
        "WhitespaceTokenizer", "CountVectorsFeaturizer", "DIETClassifier"
    )
    assert pipeline[2]["name"] == "DIETClassifier"
    pipeline[2].update(classifier_params)

    _config = RasaNLUModelConfig({"pipeline": pipeline})
    (trained_model, _, persisted_path) = await train(
        _config,
        path=str(tmp_path),
        data="data/test/many_intents.md",
        component_builder=component_builder,
    )
    loaded = Interpreter.load(persisted_path, component_builder)

    mock = Mock()
    monkeypatch.setattr(train_utils, "normalize", mock.normalize)

    parse_data = loaded.parse("hello")
    intent_ranking = parse_data.get("intent_ranking")

    # check that the output was correctly truncated
    assert len(intent_ranking) == output_length

    intent_confidences = [intent.get("confidence") for intent in intent_ranking]

    # check each confidence is in range
    confidence_in_range = [
        prediction_min <= confidence <= prediction_max
        for confidence in intent_confidences
    ]
    assert all(confidence_in_range)

    # normalize shouldn't have been called
    mock.normalize.assert_not_called()

    # check whether the normalization of rankings is reflected in intent prediction
    assert parse_data.get("intent") == intent_ranking[0]


@pytest.mark.parametrize(
    "classifier_params, output_length",
    [({LOSS_TYPE: "margin", RANDOM_SEED: 42, EPOCHS: 1}, LABEL_RANKING_LENGTH)],
)
async def test_margin_loss_is_not_normalized(
    monkeypatch, component_builder, tmpdir, classifier_params, output_length
):
    pipeline = as_pipeline(
        "WhitespaceTokenizer", "CountVectorsFeaturizer", "DIETClassifier"
    )
    assert pipeline[2]["name"] == "DIETClassifier"
    pipeline[2].update(classifier_params)

    mock = Mock()
    monkeypatch.setattr(train_utils, "normalize", mock.normalize)

    _config = RasaNLUModelConfig({"pipeline": pipeline})
    (trained_model, _, persisted_path) = await train(
        _config,
        path=str(tmpdir),
        data="data/test/many_intents.md",
        component_builder=component_builder,
    )
    loaded = Interpreter.load(persisted_path, component_builder)

    parse_data = loaded.parse("hello")
    intent_ranking = parse_data.get("intent_ranking")

    # check that the output was not normalized
    mock.normalize.assert_not_called()

    # check that the output was correctly truncated
    assert len(intent_ranking) == output_length

    # make sure top ranking is reflected in intent prediction
    assert parse_data.get("intent") == intent_ranking[0]


async def test_set_random_seed(component_builder, tmpdir):
    """test if train result is the same for two runs of tf embedding"""

    # set fixed random seed
    _config = RasaNLUModelConfig(
        {
            "pipeline": [
                {"name": "WhitespaceTokenizer"},
                {"name": "CountVectorsFeaturizer"},
                {"name": "DIETClassifier", RANDOM_SEED: 1, EPOCHS: 1},
            ],
            "language": "en",
        }
    )

    # first run
    (trained_a, _, persisted_path_a) = await train(
        _config,
        path=tmpdir.strpath + "_a",
        data=DEFAULT_DATA_PATH,
        component_builder=component_builder,
    )
    # second run
    (trained_b, _, persisted_path_b) = await train(
        _config,
        path=tmpdir.strpath + "_b",
        data=DEFAULT_DATA_PATH,
        component_builder=component_builder,
    )

    loaded_a = Interpreter.load(persisted_path_a, component_builder)
    loaded_b = Interpreter.load(persisted_path_b, component_builder)
    result_a = loaded_a.parse("hello")["intent"]["confidence"]
    result_b = loaded_b.parse("hello")["intent"]["confidence"]

    assert result_a == result_b


async def test_train_tensorboard_logging(component_builder, tmpdir):
    from pathlib import Path

    tensorboard_log_dir = Path(tmpdir.strpath) / "tensorboard"

    assert not tensorboard_log_dir.exists()

    _config = RasaNLUModelConfig(
        {
            "pipeline": [
                {"name": "WhitespaceTokenizer"},
                {"name": "CountVectorsFeaturizer"},
                {
                    "name": "DIETClassifier",
                    EPOCHS: 3,
                    TENSORBOARD_LOG_LEVEL: "epoch",
                    TENSORBOARD_LOG_DIR: str(tensorboard_log_dir),
                    EVAL_NUM_EXAMPLES: 15,
                    EVAL_NUM_EPOCHS: 1,
                },
            ],
            "language": "en",
        }
    )

    await train(
        _config,
        path=tmpdir.strpath,
        data="data/examples/rasa/demo-rasa-multi-intent.md",
        component_builder=component_builder,
    )

    assert tensorboard_log_dir.exists()

    all_files = list(tensorboard_log_dir.rglob("*.*"))
    assert len(all_files) == 3


async def test_train_model_checkpointing(
    component_builder: ComponentBuilder, tmpdir: Path
):
    model_name = "nlu-checkpointed-model"
    best_model_file = Path(str(tmpdir), model_name)
    assert not best_model_file.exists()

    _config = RasaNLUModelConfig(
        {
            "pipeline": [
                {"name": "WhitespaceTokenizer"},
                {"name": "CountVectorsFeaturizer"},
                {
                    "name": "DIETClassifier",
                    EPOCHS: 5,
                    EVAL_NUM_EXAMPLES: 10,
                    EVAL_NUM_EPOCHS: 1,
                    CHECKPOINT_MODEL: True,
                },
            ],
            "language": "en",
        }
    )

    await train(
        _config,
        path=str(tmpdir),
        data="data/examples/rasa/demo-rasa.md",
        component_builder=component_builder,
        fixed_model_name=model_name,
    )

    assert best_model_file.exists()

    """
    Tricky to validate the *exact* number of files that should be there, however there must be at least the following:
        - metadata.json
        - checkpoint
        - component_1_CountVectorsFeaturizer (as per the pipeline above)
        - component_2_DIETClassifier files (more than 1 file)
    """
    all_files = list(best_model_file.rglob("*.*"))
    assert len(all_files) > 4


@pytest.mark.parametrize(
    "classifier_params",
    [
        {RANDOM_SEED: 1, EPOCHS: 1, BILOU_FLAG: False},
        {RANDOM_SEED: 1, EPOCHS: 1, BILOU_FLAG: True},
    ],
)
async def test_train_persist_load_with_composite_entities(
    classifier_params, component_builder, tmpdir
):
    pipeline = as_pipeline(
        "WhitespaceTokenizer", "CountVectorsFeaturizer", "DIETClassifier"
    )
    assert pipeline[2]["name"] == "DIETClassifier"
    pipeline[2].update(classifier_params)

    _config = RasaNLUModelConfig({"pipeline": pipeline, "language": "en"})

    (trainer, trained, persisted_path) = await train(
        _config,
        path=tmpdir.strpath,
        data="data/test/demo-rasa-composite-entities.md",
        component_builder=component_builder,
    )

    assert trainer.pipeline
    assert trained.pipeline

    loaded = Interpreter.load(persisted_path, component_builder)

    assert loaded.pipeline
    text = "I am looking for an italian restaurant"
    assert loaded.parse(text) == trained.parse(text)


async def test_process_gives_diagnostic_data(trained_nlu_moodbot_path: Text,):
    """Tests if processing a message returns attention weights as numpy array."""
    with rasa.model.unpack_model(trained_nlu_moodbot_path) as unpacked_model_directory:
        _, nlu_model_directory = rasa.model.get_model_subdirectories(
            unpacked_model_directory
        )
        interpreter = Interpreter.load(nlu_model_directory)

    message = Message(data={TEXT: "hello"})
    for component in interpreter.pipeline:
        component.process(message)

    diagnostic_data = message.get(DIAGNOSTIC_DATA)

    # The last component is DIETClassifier, which should add attention weights
    name = f"component_{len(interpreter.pipeline) - 1}_DIETClassifier"
    assert isinstance(diagnostic_data, dict)
    assert name in diagnostic_data
    assert "attention_weights" in diagnostic_data[name]
    assert isinstance(diagnostic_data[name].get("attention_weights"), np.ndarray)
    assert "text_transformed" in diagnostic_data[name]
    assert isinstance(diagnostic_data[name].get("text_transformed"), np.ndarray)
