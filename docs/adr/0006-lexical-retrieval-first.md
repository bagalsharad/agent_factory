# ADR-0006: Local lexical retrieval first

Status: accepted

The document research use case needs grounded retrieval with attribution, not semantic recall at scale. A BM25
retriever over chunks stored in the relational database gives deterministic, explainable results with zero extra
services. `Retriever` is an interface; pgvector/Qdrant/embedding retrievers can be added without touching the
runtime. Ingestion is restricted to approved roots.
