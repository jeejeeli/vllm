# SPDX-License-Identifier: Apache-2.0

from functools import partial

import numpy as np
import pytest
from PIL import Image

from vllm.config import ModelConfig
from vllm.inputs import InputProcessingContext
from vllm.multimodal import MULTIMODAL_REGISTRY
from vllm.multimodal.processing import ProcessingCache
from vllm.multimodal.utils import cached_get_tokenizer

from ....multimodal.utils import random_audio, random_image, random_video
from ...registry import HF_EXAMPLE_MODELS


def _test_processing_correctness(
    model_id: str,
    hit_rate: float,
    num_batches: int,
    simplify_rate: float,
):
    model_info = HF_EXAMPLE_MODELS.find_hf_info(model_id)
    model_info.check_available_online(on_fail="skip")
    model_info.check_transformers_version(on_fail="skip")

    model_config = ModelConfig(
        model_id,
        task="auto",
        tokenizer=model_id,
        tokenizer_mode="auto",
        trust_remote_code=model_info.trust_remote_code,
        seed=0,
        dtype="float16",
        revision=None,
        hf_overrides=model_info.hf_overrides,
    )

    model_cls = MULTIMODAL_REGISTRY._get_model_cls(model_config)
    factories = MULTIMODAL_REGISTRY._processor_factories[model_cls]
    ctx = InputProcessingContext(
        model_config,
        tokenizer=cached_get_tokenizer(
            model_config.tokenizer,
            trust_remote_code=model_info.trust_remote_code,
        ),
    )
    # Ensure that it can fit all of the data
    cache = ProcessingCache(capacity=1 << 30)

    processing_info = factories.info(ctx)
    supported_mm_limits = processing_info.get_supported_mm_limits()
    limit_mm_per_prompt = {
        modality: 3 if limit is None else limit
        for modality, limit in supported_mm_limits.items()
    }

    model_config.get_multimodal_config().limit_per_prompt = limit_mm_per_prompt

    baseline_processor = factories.build_processor(ctx, cache=None)
    cached_processor = factories.build_processor(ctx, cache=cache)
    dummy_inputs = baseline_processor.dummy_inputs
    rng = np.random.RandomState(0)

    input_to_hit = {
        "image": Image.new("RGB", size=(128, 128)),
        "video": np.zeros((4, 128, 128, 3), dtype=np.uint8),
        "audio": (np.zeros((512, )), 16000),
    }
    input_factory = {
        "image":
        partial(random_image, rng, min_wh=128, max_wh=256),
        "video":
        partial(random_video,
                rng,
                min_frames=2,
                max_frames=8,
                min_wh=128,
                max_wh=256),
        "audio":
        partial(random_audio, rng, min_len=512, max_len=1024, sr=16000),
    }

    for batch_idx in range(num_batches):
        mm_data = {
            k:
            [(input_to_hit[k] if rng.rand() < hit_rate else input_factory[k]())
             for _ in range(limit)]
            for k, limit in limit_mm_per_prompt.items()
        }

        mm_counts = {k: len(vs) for k, vs in mm_data.items()}
        prompt = dummy_inputs.get_dummy_processor_inputs(
            model_config.max_model_len,
            mm_counts,
        ).prompt_text

        # Drop unnecessary keys and test single -> multi conversion
        if rng.rand() < simplify_rate:
            for k in list(mm_data.keys()):
                if not mm_data[k]:
                    del mm_data[k]
                elif len(mm_data[k]) == 1:
                    mm_data[k] = mm_data[k][0]

        baseline_result = baseline_processor.apply(
            prompt,
            mm_data=mm_data,
            hf_processor_mm_kwargs={},
        )
        cached_result = cached_processor.apply(
            prompt,
            mm_data=mm_data,
            hf_processor_mm_kwargs={},
        )

        assert baseline_result == cached_result, (
            f"Failed ({batch_idx=}, {prompt=}, {mm_data=})")


# yapf: disable
@pytest.mark.parametrize("model_id", ["THUDM/glm-4v-9b"])
@pytest.mark.parametrize("hit_rate", [0.3, 0.5, 1.0])
@pytest.mark.parametrize("num_batches", [32])
@pytest.mark.parametrize("simplify_rate", [1.0])
# yapf: enable
def test_processing_correctness(
    model_id: str,
    hit_rate: float,
    num_batches: int,
    simplify_rate: float,
):
    _test_processing_correctness(
        model_id,
        hit_rate=hit_rate,
        num_batches=num_batches,
        simplify_rate=simplify_rate,
    )
