from typing import Any, Dict, List

from content_core import extract_content
from content_core.common import ProcessSourceState
from langgraph.graph import END, START, StateGraph
from loguru import logger
from typing_extensions import TypedDict

from open_notebook.ai.models import Model, ModelManager
from open_notebook.domain.content_settings import ContentSettings
from open_notebook.domain.notebook import Asset, Source


class SourceState(TypedDict):
    content_state: ProcessSourceState
    source_id: str
    notebook_ids: List[str]
    source: Source
    embed: bool


async def content_process(state: SourceState) -> dict:
    content_settings = ContentSettings(
        default_content_processing_engine_doc="auto",
        default_content_processing_engine_url="auto",
        default_embedding_option="ask",
        auto_delete_files="yes",
        youtube_preferred_languages=[
            "en",
            "pt",
            "es",
            "de",
            "nl",
            "en-GB",
            "fr",
            "hi",
            "ja",
        ],
    )
    content_state: Dict[str, Any] = state["content_state"]  # type: ignore[assignment]

    content_state["url_engine"] = (
        content_settings.default_content_processing_engine_url or "auto"
    )
    content_state["document_engine"] = (
        content_settings.default_content_processing_engine_doc or "auto"
    )
    content_state["output_format"] = "markdown"

    # Add speech-to-text model configuration from Default Models
    try:
        model_manager = ModelManager()
        defaults = await model_manager.get_defaults()
        if defaults.default_speech_to_text_model:
            stt_model = await Model.get(defaults.default_speech_to_text_model)
            if stt_model:
                content_state["audio_provider"] = stt_model.provider
                content_state["audio_model"] = stt_model.name
                logger.debug(
                    f"Using speech-to-text model: {stt_model.provider}/{stt_model.name}"
                )
    except Exception as e:
        logger.warning(f"Failed to retrieve speech-to-text model configuration: {e}")
        # Continue without custom audio model (content-core will use its default)

    # Check if URL is VBPL, route through multi-stage reliable legal extractors
    url = content_state.get("url") or ""
    processed_state = None

    if url and "vbpl.vn" in url.lower():
        # 1. First attempt: Instant PostgreSQL local legal repository lookup
        try:
            import os
            import psycopg2
            db_url = os.getenv("LEGAL_DATABASE_URL", "postgresql+psycopg2://postgres:123456@127.0.0.1:5432/legal_chatbot").replace("postgresql+psycopg2://", "postgresql://")
            conn = psycopg2.connect(db_url)
            cur = conn.cursor()
            cur.execute(
                "SELECT id, title, law_number FROM legal_documents WHERE source_url = %s LIMIT 1;",
                (url,)
            )
            row = cur.fetchone()
            if not row:
                url_clean = url.rstrip("/").split("?")[0]
                cur.execute(
                    "SELECT id, title, law_number FROM legal_documents WHERE source_url ILIKE %s LIMIT 1;",
                    (f"%{url_clean}%",)
                )
                row = cur.fetchone()
            if row:
                doc_id, doc_title, law_number = row
                cur.execute(
                    "SELECT title, content FROM legal_articles WHERE document_id = %s ORDER BY id;",
                    (doc_id,)
                )
                articles = cur.fetchall()
                if articles:
                    article_texts = []
                    for a_title, a_content in articles:
                        if a_title and a_content:
                            article_texts.append(f"### {a_title}\n{a_content}")
                        elif a_content:
                            article_texts.append(a_content)
                    full_content = f"# {doc_title}\nSố hiệu: {law_number or 'N/A'}\n\n" + "\n\n".join(article_texts)
                    logger.info(f"Loaded legal document from local PostgreSQL: {doc_title} ({len(full_content)} chars)")
                    processed_state = ProcessSourceState(
                        url=url,
                        title=doc_title,
                        content=full_content,
                        file_path="",
                        source_type="url",
                        identified_type="webpage"
                    )
            conn.close()
        except Exception as e:
            logger.warning(f"PostgreSQL local legal lookup failed: {e}")

        # 2. Second attempt: Playwright crawler
        if not processed_state:
            try:
                logger.info(f"Custom routing VBPL URL through Playwright crawler: {url}")
                from open_notebook.utils.vbpl_crawler import crawl_vbpl_url
                crawl_res = await crawl_vbpl_url(url)
                processed_state = ProcessSourceState(
                    url=url,
                    title=crawl_res["title"],
                    content=crawl_res["content"],
                    file_path="",
                    source_type="url",
                    identified_type="webpage"
                )
            except Exception as e:
                logger.error(f"Custom VBPL Playwright crawl failed: {e}")

        # 3. Third attempt: Official HTTP pipeline
        if not processed_state:
            try:
                from api.crawlers.legal_document_pipeline import fetch_normalized_legal_document
                norm_doc = await fetch_normalized_legal_document(url, scope="Trung ương - toàn quốc")
                if norm_doc and norm_doc.get("clean_markdown") and len(norm_doc.get("clean_markdown", "")) > 100:
                    processed_state = ProcessSourceState(
                        url=url,
                        title=norm_doc.get("title") or "Văn bản pháp luật",
                        content=norm_doc.get("clean_markdown"),
                        file_path="",
                        source_type="url",
                        identified_type="webpage"
                    )
            except Exception as e:
                logger.warning(f"Normalized legal document pipeline fallback failed: {e}")

    if not processed_state:
        processed_state = await extract_content(content_state)

    content_str = (processed_state.content or "").strip()
    if not content_str or content_str.startswith("Failed to extract content:") or processed_state.title == "Error":
        url_target = processed_state.url or url or ""
        if url_target and ("youtube.com" in url_target or "youtu.be" in url_target):
            raise ValueError(
                "Could not extract content from this YouTube video. "
                "No transcript or subtitles are available."
            )
        raise ValueError(
            "Không thể trích xuất nội dung văn bản này từ máy chủ nguồn. Vui lòng kiểm tra lại URL hoặc chọn văn bản từ kho nội bộ."
        )

    return {"content_state": processed_state}


async def save_source(state: SourceState) -> dict:
    content_state = state["content_state"]

    # Get existing source using the provided source_id
    source = await Source.get(state["source_id"])
    if not source:
        raise ValueError(f"Source with ID {state['source_id']} not found")

    # Update the source with processed content
    source.asset = Asset(url=content_state.url, file_path=content_state.file_path)
    source.full_text = content_state.content

    # Preserve user-set title; only overwrite placeholder or empty titles
    if content_state.title and (not source.title or source.title == "Processing..."):
        source.title = content_state.title

    await source.save()

    # NOTE: Notebook associations are created by the API immediately for UI responsiveness
    # No need to create them here to avoid duplicate edges

    if state["embed"]:
        if source.full_text and source.full_text.strip():
            logger.debug("Embedding content for vector search")
            await source.vectorize()
        else:
            logger.warning(
                f"Source {source.id} has no text content to embed, skipping vectorization"
            )

    return {"source": source}


# Create and compile the workflow
workflow = StateGraph(SourceState)

# Add nodes
workflow.add_node("content_process", content_process)
workflow.add_node("save_source", save_source)
# Define the graph edges
workflow.add_edge(START, "content_process")
workflow.add_edge("content_process", "save_source")
workflow.add_edge("save_source", END)

# Compile the graph
source_graph = workflow.compile()
