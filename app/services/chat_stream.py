"""Bounded SSE decoding and display-only extraction of a partial JSON answer."""
import json

MAX_STREAM_BYTES = 1_000_000


def sse_data(response):
    lines = []
    size = 0
    while True:
        line = response.readline(MAX_STREAM_BYTES + 1)
        if not line:
            break
        size += len(line)
        if size > MAX_STREAM_BYTES:
            raise ValueError("AI stream is too large")
        line = line.decode("utf-8").rstrip("\r\n")
        if not line:
            if lines:
                yield "\n".join(lines)
                lines.clear()
        elif line.startswith("data:"):
            lines.append(line[5:].removeprefix(" "))
    if lines:
        raise ValueError("Incomplete SSE event")


def answer_prefix(content: str) -> str:
    """Read only the root answer string; incomplete escapes never reach the UI."""
    decoder = json.JSONDecoder()
    source = content.lstrip()
    if not source.startswith("{"):
        return ""
    pos = 1
    try:
        while True:
            while pos < len(source) and source[pos].isspace():
                pos += 1
            key, pos = decoder.raw_decode(source, pos)
            while pos < len(source) and source[pos].isspace():
                pos += 1
            if source[pos] != ":":
                return ""
            pos += 1
            while pos < len(source) and source[pos].isspace():
                pos += 1
            if key == "answer":
                if source[pos] != '"':
                    return ""
                start = pos
                pos += 1
                end = pos
                while pos < len(source):
                    if source[pos] == '"':
                        return json.loads(source[start:pos + 1])
                    if source[pos] == "\\":
                        width = 6 if source[pos:pos + 2] == "\\u" else 2
                        if pos + width > len(source):
                            break
                        pos += width
                    else:
                        pos += 1
                    end = pos
                value = json.loads(source[start:end] + '"')
                # JSON may temporarily end between a UTF-16 surrogate pair.
                return value[:-1] if value and 0xD800 <= ord(value[-1]) <= 0xDBFF else value
            _, pos = decoder.raw_decode(source, pos)
            while pos < len(source) and source[pos].isspace():
                pos += 1
            if source[pos] != ",":
                return ""
            pos += 1
    except (ValueError, IndexError):
        return ""
