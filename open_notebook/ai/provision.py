from __future__ import annotations

from typing import TYPE_CHECKING

from loguru import logger

from open_notebook.ai.models import model_manager
from open_notebook.exceptions import ConfigurationError
from open_notebook.utils import token_count

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

        return model.to_langchain()
    except Exception as exc:
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
                    return gemini_model.to_langchain()
                
            # 2. Try Hugging Face provider
            hf_row = next((r for r in available if r.get("provider") == "huggingface"), None)
            if hf_row:
                logger.info(f"Fallback matched Hugging Face model: {hf_row['id']}")
                hf_model = await model_manager.get_model(hf_row["id"])
                if hf_model:
                    return hf_model.to_langchain()

            # 3. Try any model that is NOT ollama
            non_local_row = next((r for r in available if r.get("provider") != "ollama"), None)
            if non_local_row:
                logger.info(f"Fallback matched non-local model: {non_local_row['id']}")
                non_local_model = await model_manager.get_model(non_local_row["id"])
                if non_local_model:
                    return non_local_model.to_langchain()
        except Exception as fallback_exc:
            logger.error(f"Fallback resolution failed: {fallback_exc}")

        raise ConfigurationError(
            f"Failed to load configured model ({selection_reason}) and no active cloud fallback was available. "
            f"Please check your server internet connection or model settings. Details: {str(exc)}"
        )
