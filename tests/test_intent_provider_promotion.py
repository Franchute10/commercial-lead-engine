from lead_engine.application.intent_discovery import (
    _BUYER_CONTEXT,
    _PROVIDER_LANGUAGE,
    _request_type,
    _service_matches,
)
from lead_engine.domain.intent_signal import RequestType


def test_mixed_adoption_and_cleaning_snippet_is_rejected_as_provider_promotion() -> None:
    excerpt = (
        "Please adopt these sweet pups. We are accepting NEW clients! "
        "Looking for reliable, professional, and detailed residential cleaning services?"
    )

    assert _request_type(excerpt) is RequestType.SERVICE_REQUEST
    assert _BUYER_CONTEXT.search(excerpt)
    assert _service_matches("house cleaning", excerpt)
    assert _PROVIDER_LANGUAGE.search(excerpt)


def test_genuine_house_cleaning_request_is_not_provider_language() -> None:
    excerpt = "I'm looking for a reliable house cleaning service for my home."

    assert _request_type(excerpt) is RequestType.SERVICE_REQUEST
    assert _BUYER_CONTEXT.search(excerpt)
    assert _service_matches("house cleaning", excerpt)
    assert not _PROVIDER_LANGUAGE.search(excerpt)
