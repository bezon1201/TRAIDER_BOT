def html_escape(s: str) -> str:
    return (s.replace("&", "&amp;")
             .replace("<", "&lt;")
             .replace(">", "&gt;"))

def mono(text: str) -> str:
    """Wrap text in monospaced block for Telegram HTML."""
    return f"<pre>{html_escape(text)}</pre>"
