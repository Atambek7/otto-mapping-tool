from __future__ import annotations

import json
import unittest

from integrations.openai_client import (
    HTTPResponse,
    OpenAIClientError,
    OpenAIResponsesClient,
    OpenAISettings,
    configuration_status,
)
from services.ai_service import (
    AISuggestionError,
    AISuggestionService,
    SuggestionContext,
    TaxonomyAttribute,
    VerifiedAttributeCandidate,
    VerifiedFact,
)


def response_document(text: str, *, status: str = "completed") -> bytes:
    return json.dumps(
        {
            "status": status,
            "output": [{"type": "message", "content": [{"type": "output_text", "text": text}]}],
        }
    ).encode()


class FakeProvider:
    def __init__(self, raw: str, *, model: str = "test-model", error: Exception | None = None):
        self.settings = OpenAISettings(api_key="never-print-this", model=model)
        self.raw = raw
        self.error = error
        self.calls: list[dict] = []

    def create_structured_response(self, **kwargs) -> str:
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return self.raw


def valid_output(*, title: str = "Brand Cotton Chair 45 cm", attributes: list | None = None) -> str:
    return json.dumps(
        {
            "title": title,
            "attributes": attributes if attributes is not None else [
                {"name": "Material", "value": "Cotton", "sourceRefs": ["otto:title:material"]}
            ],
        }
    )


def context() -> SuggestionContext:
    return SuggestionContext(
        source_title="Brand Chair Cotton 45 cm",
        verified_facts=(
            VerifiedFact("otto:title:material", "material", "Cotton", preserve_in_title=True),
            VerifiedFact("otto:title:size", "size", "45 cm", preserve_in_title=True),
        ),
        taxonomy=(TaxonomyAttribute("Material", ("Cotton", "Linen")),),
        attribute_candidates=(VerifiedAttributeCandidate("Material", "Cotton", "otto:title:material"),),
    )


class OpenAIClientTests(unittest.TestCase):
    def test_environment_configuration_is_bounded_and_secret_is_not_represented(self):
        environment = {
            "OTTORESTART_OPENAI_API_KEY": "server-secret",
            "OTTORESTART_OPENAI_MODEL": "gpt-test-1",
            "OTTORESTART_OPENAI_TIMEOUT_SECONDS": "12",
            "OTTORESTART_OPENAI_MAX_OUTPUT_TOKENS": "800",
            "OTTORESTART_OPENAI_MAX_INPUT_BYTES": "2048",
        }
        settings = OpenAISettings.from_environment(environment)
        self.assertEqual(settings.timeout_seconds, 12)
        self.assertEqual(settings.max_output_tokens, 800)
        self.assertEqual(settings.max_input_bytes, 2048)
        self.assertNotIn("server-secret", repr(settings))
        self.assertNotIn("server-secret", repr(configuration_status(environment, enabled=True)))

    def test_missing_configuration_does_not_report_ready_or_expose_key(self):
        status = configuration_status({}, enabled=True)
        self.assertEqual(status, {
            "enabled": True, "configured": False, "available": False,
            "state": "NOT_CONFIGURED", "model": None,
        })

    def test_responses_request_uses_fixed_host_schema_and_bounded_request(self):
        captured = {}

        def transport(host, path, headers, body, timeout):
            captured.update(host=host, path=path, headers=dict(headers), body=json.loads(body), timeout=timeout)
            return HTTPResponse(200, response_document('{"title":"x","attributes":[]}'))

        settings = OpenAISettings("server-secret", "gpt-test-1", timeout_seconds=7, max_output_tokens=321)
        client = OpenAIResponsesClient(settings, enabled=True, transport=transport)
        result = client.create_structured_response(
            instructions="Return JSON.", input_data={"sourceTitle": "x"},
            schema_name="suggestion", schema={"type": "object", "additionalProperties": False},
        )
        self.assertEqual(result, '{"title":"x","attributes":[]}')
        self.assertEqual(captured["host"], "api.openai.com")
        self.assertEqual(captured["path"], "/v1/responses")
        self.assertEqual(captured["timeout"], 7)
        self.assertEqual(captured["headers"]["Authorization"], "Bearer server-secret")
        self.assertEqual(captured["body"]["model"], "gpt-test-1")
        self.assertEqual(captured["body"]["max_output_tokens"], 321)
        self.assertFalse(captured["body"]["store"])
        self.assertEqual(captured["body"]["text"]["format"]["type"], "json_schema")
        self.assertTrue(captured["body"]["text"]["format"]["strict"])

    def test_input_budget_blocks_before_transport_and_http_errors_do_not_retry(self):
        calls = []
        settings = OpenAISettings("server-secret", "gpt-test-1", max_input_bytes=512)
        client = OpenAIResponsesClient(settings, enabled=True, transport=lambda *args: calls.append(args))
        with self.assertRaises(OpenAIClientError) as raised:
            client.create_structured_response(
                instructions="Return JSON.", input_data={"x": "z" * 1000},
                schema_name="suggestion", schema={"type": "object"},
            )
        self.assertEqual(raised.exception.code, "OPENAI_INPUT_BUDGET_EXCEEDED")
        self.assertEqual(calls, [])

        calls.clear()
        client = OpenAIResponsesClient(
            OpenAISettings("server-secret", "gpt-test-1"),
            enabled=True,
            transport=lambda *args: (calls.append(args) or HTTPResponse(429, b"provider details")),
        )
        with self.assertRaises(OpenAIClientError) as raised:
            client.create_structured_response(
                instructions="Return JSON.", input_data={},
                schema_name="suggestion", schema={"type": "object"},
            )
        self.assertEqual(raised.exception.code, "OPENAI_HTTP_ERROR")
        self.assertEqual(len(calls), 1)
        self.assertNotIn("provider details", str(raised.exception))

    def test_refusal_incomplete_timeout_and_invalid_response_are_explicit(self):
        base = OpenAISettings("server-secret", "gpt-test-1")
        refusal = {
            "status": "completed",
            "output": [{"type": "message", "content": [{"type": "refusal", "refusal": "private reason"}]}],
        }
        for body, expected in [
            (json.dumps(refusal).encode(), "OPENAI_REFUSED"),
            (response_document("{}", status="incomplete"), "OPENAI_INCOMPLETE_RESPONSE"),
            (b"not-json", "OPENAI_INVALID_RESPONSE"),
        ]:
            client = OpenAIResponsesClient(base, enabled=True, transport=lambda *args, body=body: HTTPResponse(200, body))
            with self.subTest(code=expected), self.assertRaises(OpenAIClientError) as raised:
                client.create_structured_response(
                    instructions="Return JSON.", input_data={},
                    schema_name="suggestion", schema={"type": "object"},
                )
            self.assertEqual(raised.exception.code, expected)
            self.assertNotIn("private reason", str(raised.exception))

        def timeout(*_args):
            raise TimeoutError("contains private URL")

        client = OpenAIResponsesClient(base, enabled=True, transport=timeout)
        with self.assertRaises(OpenAIClientError) as raised:
            client.create_structured_response(
                instructions="Return JSON.", input_data={},
                schema_name="suggestion", schema={"type": "object"},
            )
        self.assertEqual(raised.exception.code, "OPENAI_TIMEOUT")
        self.assertNotIn("private URL", str(raised.exception))

    def test_client_is_disabled_by_default_and_does_not_call_transport(self):
        calls = []
        client = OpenAIResponsesClient(
            OpenAISettings("server-secret", "gpt-test-1"),
            transport=lambda *args: calls.append(args),
        )
        with self.assertRaises(OpenAIClientError) as raised:
            client.create_structured_response(
                instructions="Return JSON.", input_data={},
                schema_name="suggestion", schema={"type": "object"},
            )
        self.assertEqual(raised.exception.code, "OPENAI_DISABLED")
        self.assertEqual(calls, [])


class AISuggestionServiceTests(unittest.TestCase):
    def service(self, raw: str, *, error: Exception | None = None) -> tuple[AISuggestionService, FakeProvider]:
        provider = FakeProvider(raw, error=error)
        return AISuggestionService(provider, enabled=True), provider

    def test_returns_only_manual_review_suggestions_with_verified_refs(self):
        service, provider = self.service(valid_output())
        result = service.suggest(context())
        self.assertEqual(result.title, "Brand Cotton Chair 45 cm")
        self.assertEqual(result.attributes[0].source_refs, ("otto:title:material",))
        self.assertTrue(result.manual_review_required)
        self.assertEqual(result.review_reasons, ("AI_SUGGESTION_REQUIRES_REVIEW",))
        self.assertFalse(hasattr(result, "status"))
        self.assertFalse(hasattr(result, "gtin"))
        self.assertIn("additionalProperties", provider.calls[0]["schema"])

    def test_rejects_long_identical_and_unsupported_title_terms(self):
        cases = [
            ("Brand Chair Cotton 45 cm " + "x" * 60, "AI_TITLE_INVALID"),
            ("Brand Chair Cotton 45 cm", "AI_TITLE_UNCHANGED"),
            ("Brand Cotton Chair 45 cm 60", "AI_TITLE_UNVERIFIED_TERM"),
            ("Brand Cotton Chair 45 cm certified", "AI_TITLE_UNVERIFIED_TERM"),
            ("Brand Chair 45 cm", "AI_TITLE_FACT_DROPPED"),
        ]
        for title, expected in cases:
            service, _ = self.service(valid_output(title=title, attributes=[]))
            with self.subTest(code=expected, title=title), self.assertRaises(AISuggestionError) as raised:
                service.suggest(context())
            self.assertEqual(raised.exception.code, expected)

    def test_rejects_fabricated_enum_dimensions_certificates_and_refs(self):
        cases = [
            ([{"name": "Material", "value": "Polyester", "sourceRefs": ["otto:title:material"]}], "AI_ATTRIBUTE_UNVERIFIED"),
            ([{"name": "Dimension", "value": "60 cm", "sourceRefs": ["otto:title:size"]}], "AI_ATTRIBUTE_UNVERIFIED"),
            ([{"name": "Certificate", "value": "CE", "sourceRefs": ["otto:title:material"]}], "AI_ATTRIBUTE_UNVERIFIED"),
            ([{"name": "Material", "value": "Cotton", "sourceRefs": ["unverified"]}], "AI_ATTRIBUTE_SOURCE_REF_INVALID"),
        ]
        for attributes, expected in cases:
            service, _ = self.service(valid_output(attributes=attributes))
            with self.subTest(code=expected), self.assertRaises(AISuggestionError) as raised:
                service.suggest(context())
            self.assertEqual(raised.exception.code, expected)

    def test_invalid_json_refusal_timeout_and_disabled_mode_fail_closed(self):
        service, _ = self.service("not-json")
        with self.assertRaises(AISuggestionError) as raised:
            service.suggest(context())
        self.assertEqual(raised.exception.code, "AI_OUTPUT_INVALID_JSON")

        for error, expected in [
            (OpenAIClientError("OPENAI_REFUSED"), "OPENAI_REFUSED"),
            (OpenAIClientError("OPENAI_TIMEOUT"), "OPENAI_TIMEOUT"),
        ]:
            service, _ = self.service("", error=error)
            with self.subTest(code=expected), self.assertRaises(AISuggestionError) as raised:
                service.suggest(context())
            self.assertEqual(raised.exception.code, expected)

        disabled_provider = FakeProvider(valid_output())
        disabled = AISuggestionService(disabled_provider, enabled=False)
        with self.assertRaises(AISuggestionError) as raised:
            disabled.suggest(context())
        self.assertEqual(raised.exception.code, "OPENAI_DISABLED")
        self.assertEqual(disabled_provider.calls, [])

    def test_repeated_same_input_has_stable_hash_and_prompt_model_are_versioned(self):
        service, provider = self.service(valid_output(attributes=[]))
        first = service.suggest(context())
        second = service.suggest(context())
        self.assertEqual(first.input_hash, second.input_hash)
        self.assertEqual(first.prompt_version, second.prompt_version)
        self.assertEqual(first.schema_version, second.schema_version)
        changed_model = FakeProvider(valid_output(attributes=[]), model="another-model")
        self.assertNotEqual(first.input_hash, AISuggestionService(changed_model, enabled=True).suggest(context()).input_hash)
        self.assertEqual(len(provider.calls), 2)

    def test_dropped_title_fact_and_conflicting_attributes_are_rejected(self):
        service, _ = self.service(valid_output(title="Brand Chair 45 cm", attributes=[]))
        with self.assertRaises(AISuggestionError) as raised:
            service.suggest(context())
        self.assertEqual(raised.exception.code, "AI_TITLE_FACT_DROPPED")

        duplicate_values = json.dumps({
            "title": "Brand Cotton Chair 45 cm",
            "attributes": [
                {"name": "Material", "value": "Cotton", "sourceRefs": ["otto:title:material"]},
                {"name": "Material", "value": "Cotton", "sourceRefs": ["otto:title:material"]},
            ],
        })
        service, _ = self.service(duplicate_values)
        with self.assertRaises(AISuggestionError) as raised:
            service.suggest(context())
        self.assertEqual(raised.exception.code, "AI_ATTRIBUTE_CONFLICT")


if __name__ == "__main__":
    unittest.main()
