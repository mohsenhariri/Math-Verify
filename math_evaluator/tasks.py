from dataclasses import dataclass
from datetime import timedelta
import logging
from typing import Callable, Literal, Sequence
from lighteval.tasks.lighteval_task import LightevalTaskConfig
from lighteval.tasks.templates.qa import get_qa_prompt_function
from lighteval.tasks.lighteval_task import LightevalTask
from lighteval.utils.language import Language
from lighteval.tasks.requests import Doc


from .grader import compare_gold_target
from .parser import ExprExtractionConfig, ExtractionTarget, LatexExtractionConfig, extract_target_from_pred, get_extraction_regexes
from .utils import timeout


logger = logging.getLogger(__name__)


## Parser definition

def multilingual_extractive_match_metric(
    gold_extraction_target: Sequence[ExtractionTarget] = (ExprExtractionConfig(),),
    pred_extraction_target: Sequence[ExtractionTarget] = (ExprExtractionConfig(),),
    aggregation_function: Callable[[list[float]], float] = max,
    fallback_mode: Literal["no_fallback", "first_match"] = "first_match",
    precision: int = 6,
) -> Callable[[list[str], list[str]], float]:
    """Creates a language-aware extractive match metric that extracts answers from the model's output.

    Known issues:
    - If the task is to simplify an expression, the metric might overestimate the accuracy. This is because if the model doesn't output any anchor for the extraction (e.g final answer is..),
        it's possible that the the extracted prediction will be the expression to simplify. Because we do simplifications ourselves, it can thus happen that sympy will correctly simplify the expression,
        thus it will match gold, despite model not doing anything. PRs to fix this are welcome.

    - There is currently no StringExtractionConfig, so if the gold is \boxed{\text{Friday}} and model outputs Friday it will not match, because nothing will be extracted.

    Args:
        language: Language
            The language of the samples.
        gold_extraction_target: Sequence[ExtractionTarget]
            Extraction targets to use for gold answers. Defaults to extracting simple math expressions.
        pred_extraction_target: Sequence[ExtractionTarget]
            Extraction targets to use for predictions. Defaults to extracting simple math expressions.
        aggregation_function: Callable[[list[float]], float]
            Function to aggregate scores when multiple golds/predictions are present. Defaults to max.
        fallback_mode: Literal["no_fallback", "first_match"]
            How to perform extraction. Defaults to "first_match".
            - "no_fallback": Only use first successfully parsed matches
            - "first_match": Use the first successfully parsed match + first match irregardless the parsing success
        precision: int
            Number of decimal places to use when comparing numerical values. Defaults to 6.

    Returns:
        A sample level metric that extracts and compares mathematical expressions.

    """

    @timeout(2)
    def get_str_preds_with_timeout(
        extracted_predictions: list[list[str]], extracted_golds: list[list[str]]
    ) -> tuple[list[str], list[str]]:

        golds = [str(gold) for golds in extracted_golds for gold in golds]
        predictions = [str(pred) for preds in extracted_predictions for pred in preds]
        return (golds, predictions)

    def sample_level_fn(golds: list[str], predictions: list[str]) -> float:
        gold_extraction_regexes = get_extraction_regexes(gold_extraction_target)
        pred_extraction_regexes = get_extraction_regexes(pred_extraction_target)

        extracted_predictions = [
            extract_target_from_pred(pred, pred_extraction_regexes, fallback_mode) for pred in predictions
        ]
        extracted_golds = [extract_target_from_pred(gold, gold_extraction_regexes, fallback_mode) for gold in golds]

        # Assert on empty gold and warn on empty pred
        if any(len(g) == 0 for g in extracted_golds):
            raise ValueError(f"No gold targets found for at least one gold. Gold: {golds}, Pred: {predictions}")

        if all(len(p) == 0 for p in extracted_predictions):
            logger.warning(
                f"We did not manage to extract a prediction in the correct format. Gold: {golds}, Pred: {predictions}"
            )

        # We have to use timeout because the sypmy to str conversion can be very slow
        str_preds = []
        try:
            str_preds = get_str_preds_with_timeout(extracted_predictions, extracted_golds)
        except:  # noqa: E722
            logger.warning("Timeout when adding extracted predictions and golds to specific")

        return (
            aggregation_function(
                [
                    (1.0 if any(compare_gold_target(gold, pred, precision) for gold in extracted_golds) else 0.0)
                    for pred in extracted_predictions
                ]
            ),
            str_preds,
        )

    return sample_level_fn






## 









math_hard_lighteval = [
    LightevalTaskConfig(
        name=f"math_hard_cot:{subset}",
        suite=["lighteval", "math"],
        prompt_function=get_qa_prompt_function(
            language=Language.ENGLISH,
            adapter=lambda line: {
                "question": line["problem"],
                "choices": [line["solution"]],
            },
            cot=cot,
        ),
        hf_repo="lighteval/MATH-Hard",
        hf_subset=subset,
        hf_filter=lambda x: len(x["problem"].strip()) > 0 and len(x["solution"].strip()) > 0,
        evaluation_splits=["test"],
        few_shots_split="test",
        generation_size=1024,
        metric=[
            multilingual_extractive_match_metric(
                Language.ENGLISH,
                gold_extraction_target=(LatexExtractionConfig(),),
                pred_extraction_target=(LatexExtractionConfig(), ExprExtractionConfig()),
                fallback_mode="first_match",
                extraction_mode="first_match",
            ),
            # multilingual_quasi_exact_match_metric(Language.ENGLISH, "prefix"),
        ],
        stop_sequence=get_cot_stop_sequence(Language.ENGLISH, CFFormulation(cot=cot)),
        output_regex=None,
        frozen=False,
        trust_dataset=True,
        version=0,
    )
    for subset in [
        "algebra",
        "counting_and_probability",
        "geometry",
        "intermediate_algebra",
        "number_theory",
        "prealgebra",
        "precalculus",
    ]
    for cot in (False, True)
]


def math_prompt_function(x: dict, task_name: str) -> Doc:
    query = f"""\
Question: {x["problem"]}
Step-by-Step Answer:\
"""

    choices = [x["solution"]]
    print(x)
    return Doc(query=query, choices=choices, gold_index=0)

math_hard_lighteval = [
    LightevalTaskConfig(
        name=f"math_hard_cot:{subset}",
        suite=["lighteval", "math"],
        prompt_function=math_prompt_function,
        hf_repo="HuggingFaceTB/MATH",
        hf_subset=subset,
        hf_filter=lambda x: len(x["problem"].strip()) > 0 and len(x["solution"].strip()) > 0,
        evaluation_splits=["test"],
        few_shots_split="train",
        generation_size=1024,
        metric=[
            multilingual_extractive_match_metric(
                gold_extraction_target=(LatexExtractionConfig(),),
                pred_extraction_target=(LatexExtractionConfig(), ExprExtractionConfig()),
                fallback_mode="first_match",
            ),
        ],
        stop_sequence=["\nQuestion:", "\nProblem:", "\nquestion:", "\nproblem:"],
        trust_dataset=True,
        version=0,
    )
    for subset in [
        "algebra",
        "counting_and_probability",
        "geometry",
        "intermediate_algebra",
        "number_theory",
        "prealgebra",
        "precalculus",
    ]
]


TASKS_TABLE = [
    *math_hard_lighteval,
]




