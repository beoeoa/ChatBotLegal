from __future__ import annotations

from typing import TYPE_CHECKING

from loguru import logger

from open_notebook.ai.models import model_manager
from open_notebook.exceptions import ConfigurationError
from open_notebook.utils import token_count


def _apply_runtime_retry_budget(runtime_model, max_retries):
    """Apply an explicit retry budget to LangChain/OpenAI clients.

    Esperanto currently does not forward ``max_retries`` to ``ChatOpenAI``.
    Without this bridge, one paid timeout can become three HTTP requests.
    """
    if max_retries is None:
        return runtime_model
    retry_budget = max(0, int(max_retries))
    for target in (
        runtime_model,
        getattr(runtime_model, "root_client", None),
        getattr(runtime_model, "root_async_client", None),
    ):
        if target is None or not hasattr(target, "max_retries"):
            continue
        try:
            setattr(target, "max_retries", retry_budget)
        except (AttributeError, TypeError):
            pass
    return runtime_model


def _apply_openrouter_reasoning_budget(runtime_model, reasoning_budget):
    """Reserve bounded reasoning without hiding an unbounded token sink.

    OpenRouter counts reasoning as output tokens. A blanket ``exclude=true``
    merely hides those tokens and can leave no visible answer. Only attach a
    reasoning object when the caller provides an explicit positive budget.
    """

    extra_body = dict(getattr(runtime_model, "extra_body", None) or {})
    if reasoning_budget is None:
        # Some lower-level adapters add {exclude:true} unconditionally. That
        # hides reasoning tokens but does not stop the provider from spending
        # the output budget on them. No caller budget means provider default
        # with no injected reasoning object.
        extra_body.pop("reasoning", None)
        try:
            setattr(runtime_model, "extra_body", extra_body)
        except (AttributeError, TypeError):
            pass
        return runtime_model
    try:
        budget = max(0, int(reasoning_budget))
    except (TypeError, ValueError):
        return runtime_model
    if budget <= 0:
        extra_body.pop("reasoning", None)
        try:
            setattr(runtime_model, "extra_body", extra_body)
        except (AttributeError, TypeError):
            pass
        return runtime_model
    extra_body["reasoning"] = {
        "max_tokens": budget,
        "exclude": True,
    }
    try:
        setattr(runtime_model, "extra_body", extra_body)
    except (AttributeError, TypeError):
        pass
    return runtime_model


def _to_langchain_runtime_model(model):
    """Convert a provider model without losing its configured HTTP timeout.

    Esperanto's Ollama adapter currently forwards generation parameters to
    ``ChatOllama`` but does not forward its configured timeout.  LangChain
    therefore falls back to its short httpx default even when the API has
    granted the model a larger request budget, which turns a valid grounded
    answer into a source-only timeout fallback.  Recreate only the Ollama
    adapter here and pass the same timeout to both sync/async clients; all
    other providers retain their normal Esperanto conversion path.
    """

    if str(getattr(model, "provider", "") or "").strip().casefold() != "ollama":
        # Cloud/provider adapters already carry their configured credentials
        # and timeout through Esperanto.  Do not recurse back into this helper
        # for non-Ollama models; that turns every DeepSeek/OpenAI fallback into
        # an infinite recursion before the provider is ever called.
        return model.to_langchain()

    try:
        from langchain_ollama import ChatOllama

        model_name = str(model.get_model_name() or "").strip()
        if not model_name:
            raise ValueError("Model name is required for LangChain Ollama integration.")
        config = dict(getattr(model, "_config", {}) or {})
        timeout = float(model._get_timeout())
        kwargs = {
            "model": model_name,
            "temperature": model.temperature,
            "top_p": model.top_p,
            "num_predict": model.max_tokens,
            "num_ctx": config.get("num_ctx", 8192),
            "base_url": model.base_url,
            # ``timeout`` is an httpx client option in langchain-ollama 1.x.
            "client_kwargs": {"timeout": timeout},
        }
        keep_alive = config.get("keep_alive")
        if keep_alive is not None:
            kwargs["keep_alive"] = keep_alive
        structured = getattr(model, "structured", None)
        if isinstance(structured, dict) and structured.get("type") in {
            "json", "json_object"
        }:
            kwargs["format"] = "json"
        return ChatOllama(**kwargs)
    except ImportError:
        # Keep the existing provider error/fallback semantics if the optional
        # LangChain integration is not installed in a lightweight runtime.
        return model.to_langchain()

if TYPE_CHECKING:
    from esperanto import LanguageModel
    from langchain_core.language_models.chat_models import BaseChatModel


async def provision_langchain_model(
    content, model_id, default_type, **kwargs
) -> BaseChatModel:
    """
    Returns the best model to use based on the context size and on whether there is a specific model being requested in Config.
    If context > 105_000, returns the large_context_model
    If model_id is specified in Config, returns that model
    Otherwise, returns the default model for the given type
    """
    # Direct RAG may explicitly disallow provider substitution: a selected
    # answer model is part of the request contract and silently falling back to
    # another provider makes latency, citations and audit metadata ambiguous.
    # Keep the historical fallback for callers that do not opt out.
    allow_fallback = bool(kwargs.pop("allow_fallback", True))
    # Public chat has one owned transport; no dependency-local modifications.
    if not allow_fallback and model_id:
        from open_notebook.ai.chat_gateway import provision_chat_adapter
        adapter = await provision_chat_adapter(str(model_id), dict(kwargs))
        if adapter is not None:
            return adapter
    reasoning_budget = kwargs.pop("reasoning_budget", None)

    # Esperanto imports optional local reranker/transformer providers.  Model
    # provisioning is the first point that needs those runtime classes.
    from esperanto import LanguageModel

    tokens = token_count(content)
    model = None
    selection_reason = ""

    if tokens > 105_000:
        selection_reason = f"large_context (content has {tokens} tokens)"
        logger.debug(
            f"Using large context model because the content has {tokens} tokens"
        )
        model = await model_manager.get_default_model("large_context", **kwargs)
    elif model_id:
        selection_reason = f"explicit model_id={model_id}"
        model = await model_manager.get_model(model_id, **kwargs)
    else:
        selection_reason = f"default for type={default_type}"
        model = await model_manager.get_default_model(default_type, **kwargs)

    # Provider model reprs may contain decrypted API keys. Log only safe
    # metadata and never stringify the live model instance.
    logger.debug(
        "Using model class={} selection_reason={}",
        type(model).__name__ if model is not None else "None",
        selection_reason,
    )

    try:
        if model is None:
            raise ConfigurationError(f"No model configured for {selection_reason}.")

        if not isinstance(model, LanguageModel):
            raise ConfigurationError(f"Model is not a LanguageModel: {model}.")

        runtime_model = model.to_langchain()
        if str(getattr(model, "provider", "") or "").strip().casefold() == "openrouter":
            runtime_model = _apply_openrouter_reasoning_budget(
                runtime_model,
                reasoning_budget,
            )
        return _apply_runtime_retry_budget(runtime_model, kwargs.get("max_retries"))
    except Exception as exc:
        if not allow_fallback:
            raise
        logger.warning(
            f"Error provisioning model ({model_id or default_type}): {exc}. "
            f"Attempting cloud model fallback..."
        )
        try:
            from open_notebook.database.repository import repo_query
            available = await repo_query("SELECT * FROM model WHERE type = 'language';")
            
            # 1. Try Gemini provider
            gemini_row = next((r for r in available if r.get("provider") == "gemini"), None)
            if gemini_row:
                logger.info(f"Fallback matched Gemini model: {gemini_row['id']}")
                gemini_model = await model_manager.get_model(gemini_row["id"])
                if gemini_model:
                    return _apply_runtime_retry_budget(
                        _to_langchain_runtime_model(gemini_model),
                        kwargs.get("max_retries"),
                    )
                
            # 2. Try Hugging Face provider
            hf_row = next((r for r in available if r.get("provider") == "huggingface"), None)
            if hf_row:
                logger.info(f"Fallback matched Hugging Face model: {hf_row['id']}")
                hf_model = await model_manager.get_model(hf_row["id"])
                if hf_model:
                    return _apply_runtime_retry_budget(
                        _to_langchain_runtime_model(hf_model),
                        kwargs.get("max_retries"),
                    )

            # 3. Try any model that is NOT ollama
            non_local_row = next((r for r in available if r.get("provider") != "ollama"), None)
            if non_local_row:
                logger.info(f"Fallback matched non-local model: {non_local_row['id']}")
                non_local_model = await model_manager.get_model(non_local_row["id"])
                if non_local_model:
                    return _apply_runtime_retry_budget(
                        _to_langchain_runtime_model(non_local_model),
                        kwargs.get("max_retries"),
                    )
        except Exception as fallback_exc:
            logger.error(f"Fallback resolution failed: {fallback_exc}")

        raise ConfigurationError(
            f"Failed to load configured model ({selection_reason}) and no active cloud fallback was available. "
            f"Please check your server internet connection or model settings. Details: {str(exc)}"
        )
