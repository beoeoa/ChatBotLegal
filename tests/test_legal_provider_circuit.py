from api.legal_provider_circuit import StructuredProviderCircuit


def test_circuit_opens_per_model_and_allows_other_models():
    circuit = StructuredProviderCircuit(
        failure_threshold=1,
        cooldown_seconds=120,
    )

    circuit.record_failure("model-a", "provider_timeout", now=10)

    assert circuit.allow("model-a", now=20) is False
    assert circuit.reason("model-a") == "provider_timeout"
    assert circuit.allow("model-b", now=20) is True


def test_circuit_half_opens_after_cooldown_and_success_resets_it():
    circuit = StructuredProviderCircuit(
        failure_threshold=1,
        cooldown_seconds=120,
    )
    circuit.record_failure("model-a", "provider_rate_limit", now=10)

    assert circuit.allow("model-a", now=129.9) is False
    assert circuit.allow("model-a", now=130) is True
    circuit.record_success("model-a")

    assert circuit.allow("model-a", now=131) is True
    assert circuit.reason("model-a") is None


def test_circuit_environment_values_are_bounded_and_invalid_values_fallback():
    configured = StructuredProviderCircuit.from_environment(
        {
            "LEGAL_PROVIDER_CIRCUIT_FAILURE_THRESHOLD": "2",
            "LEGAL_PROVIDER_CIRCUIT_COOLDOWN_SECONDS": "30",
        }
    )
    fallback = StructuredProviderCircuit.from_environment(
        {
            "LEGAL_PROVIDER_CIRCUIT_FAILURE_THRESHOLD": "invalid",
            "LEGAL_PROVIDER_CIRCUIT_COOLDOWN_SECONDS": "invalid",
        }
    )

    assert configured.failure_threshold == 2
    assert configured.cooldown_seconds == 30
    assert fallback.failure_threshold == 2
    assert fallback.cooldown_seconds == 120
