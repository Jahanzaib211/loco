"""Simple extractive summarizer — no eval, pure Python."""
import textwrap

def summarize(text: str, max_sentences: int = 3) -> str:
    """Return a short summary (first *max_sentences* sentences)."""
    sentences = [s.strip() for s in text.split(".") if s.strip()]
    limited = sentences[:max_sentences]
    return " ".join(limited) + ("." if limited else "")