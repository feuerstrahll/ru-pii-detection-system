from app.schemas.models import EntityType
from evaluation.metrics import Annotation, score_exact, score_overlap


def test_ground_truth_type_without_detector_configuration_is_counted_as_a_labelled_fn():
    expected = {Annotation("doc", "body", 0, 9, EntityType.KPP)}

    report = score_exact(expected, set(), {EntityType.EMAIL})

    assert report["per_class"]["KPP"]["fn"] == 1
    assert report["errors"] == [{
        "kind": "FN", "document_id": "doc", "segment_id": "body", "start": 0, "end": 9,
        "type": "KPP", "suggested_category": "unclassified", "review_required": True,
        "root_cause": "no_detector_configured",
    }]


def test_overlap_metrics_diagnose_boundary_errors_without_changing_exact_metrics():
    expected = {Annotation("doc", "body", 5, 15, EntityType.PERSON)}
    predicted = {Annotation("doc", "body", 5, 14, EntityType.PERSON)}

    assert score_exact(expected, predicted, {EntityType.PERSON})["per_class"]["PERSON"]["f1"] == 0.0
    assert score_overlap(expected, predicted, {EntityType.PERSON})["per_class"]["PERSON"]["f1"] == 1.0
