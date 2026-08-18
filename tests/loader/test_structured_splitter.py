"""Structured splitting: table/code atomic blocks + embedding-only vector_text."""

from __future__ import annotations

from langchain_core.documents import Document

from deepsearcher.loader.splitter import Chunk, split_docs_to_chunks


def test_table_document_keeps_markdown_and_vector_text():
    document = Document(
        page_content="| 指标 | 数值 |\n| --- | --- |\n| 收入 | 100 |",
        metadata={
            "reference": "t.xlsx",
            "kind": "table",
            "_table_headers": ["指标", "数值"],
            "_table_rows": [["收入", "100"]],
        },
    )
    chunks = split_docs_to_chunks([document])
    assert len(chunks) == 1
    chunk = chunks[0]
    assert "| 指标 | 数值 |" in chunk.text
    assert chunk.vector_text == "指标: 收入; 数值: 100"
    assert "_table_headers" not in chunk.metadata
    assert "_table_rows" not in chunk.metadata
    assert "kind" not in chunk.metadata


def test_long_table_splits_into_row_groups_with_full_header():
    headers = ["c1", "c2"]
    rows = [[f"v{i}", "x" * 300] for i in range(40)]
    document = Document(
        page_content="table",
        metadata={
            "kind": "table",
            "reference": "t.xlsx",
            "_table_headers": headers,
            "_table_rows": rows,
        },
    )
    chunks = split_docs_to_chunks([document], chunk_size=500, chunk_overlap=0)
    assert len(chunks) > 1
    for chunk in chunks:
        assert chunk.text.startswith("| c1 | c2 |")
        assert "c1:" in chunk.vector_text


def test_code_document_keeps_fence_and_vector_text():
    document = Document(
        page_content="def foo():\n    return 1",
        metadata={"kind": "code", "reference": "a.py", "_code_language": "python"},
    )
    chunks = split_docs_to_chunks([document])
    assert len(chunks) == 1
    assert chunks[0].text.startswith("```python")
    assert chunks[0].vector_text == "def foo():\n    return 1"


def test_fenced_code_protected_before_section_split():
    markdown = (
        "## 正常标题\n\n"
        "说明文字。\n\n"
        "```python\n"
        "# 这不是 Markdown 标题\n"
        "def foo():\n"
        "    return 1\n"
        "```\n\n"
        "## 下一个正常标题\n\n"
        "结尾。"
    )
    document = Document(page_content=markdown, metadata={"reference": "notes.md"})
    chunks = split_docs_to_chunks([document], chunk_size=200, chunk_overlap=20)
    code_chunks = [chunk for chunk in chunks if chunk.vector_text is not None]
    assert len(code_chunks) >= 1
    for chunk in code_chunks:
        assert "# 这不是 Markdown 标题" in chunk.vector_text
        assert chunk.text.startswith("```python")


def test_plain_document_without_code_unchanged():
    text = "一段没有代码的普通文本。" * 60
    document = Document(page_content=text, metadata={"reference": "a.txt"})
    chunks = split_docs_to_chunks([document], chunk_size=200, chunk_overlap=20)
    assert len(chunks) > 1
    assert all(chunk.vector_text is None for chunk in chunks)
    assert "一段没有代码的普通文本" in "".join(chunk.text for chunk in chunks)


def test_chunk_vector_text_default_none():
    chunk = Chunk(text="x", reference="r")
    assert chunk.vector_text is None
