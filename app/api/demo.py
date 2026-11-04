"""Run the local PII review demo with `uv run python -m app.api.demo`."""

import argparse
from base64 import b64encode
from email import policy
from email.parser import BytesParser
from hashlib import sha256
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
from math import isfinite
from pathlib import Path
from tempfile import TemporaryDirectory

from app.core.config import load_config, load_ocr_config
from app.core.parsers import ParserRegistry
from app.core.pipeline import DeterministicPipeline
from app.ingestion.docx_adapter import DocxDocumentParser
from app.ingestion.ocr_adapter import OCRDocumentParser
from app.schemas.models import DetectedEntity, DocumentSegment, ProcessingStatus


_ROOT = Path(__file__).resolve().parents[2]
_MAX_UPLOAD = 10 * 1024 * 1024
_MAX_PREVIEW_PAGES = 20
_SUFFIXES = DocxDocumentParser.supported_suffixes | OCRDocumentParser.supported_suffixes
_TYPE_NAMES = {
    "EMAIL": "Электронная почта", "PHONE": "Телефон", "SOCIAL_SECURITY_ID": "СНИЛС",
    "TAX_ID": "ИНН", "PASSPORT": "Паспорт РФ", "ORG_REGISTRATION_ID": "ОГРН / ОГРНИП",
    "BANK_CARD": "Банковская карта", "KPP": "КПП",
}


def _location(segment: DocumentSegment) -> str:
    if segment.page_number is not None:
        return f"Страница {segment.page_number}"
    return f"Абзац {segment.source_locator['paragraph']}"


def _highlight(text: str, entities: list[DetectedEntity], segment_id: str) -> str:
    valid = [entity for entity in entities if entity.span.segment_id == segment_id
             and 0 <= entity.span.start < entity.span.end <= len(text)
             and text[entity.span.start:entity.span.end] == entity.span.text]
    boundaries = sorted({0, len(text)} | {point for entity in valid for point in (entity.span.start, entity.span.end)})
    parts: list[str] = []
    for start, end in zip(boundaries, boundaries[1:]):
        parts.extend(f'<span id="text-finding-{entity.entity_id}"></span>' for entity in valid if entity.span.start == start)
        chunk = escape(text[start:end])
        active = [entity for entity in valid if entity.span.start <= start and entity.span.end >= end]
        if active:
            decision = "accept" if any(entity.decision == "accept" for entity in active) else "review"
            labels = ", ".join(sorted({_TYPE_NAMES.get(entity.entity_type.value, entity.entity_type.value)
                                       for entity in active}))
            parts.append(f'<mark class="{decision}" title="{escape(labels, quote=True)}">{chunk}</mark>')
        else:
            parts.append(chunk)
    return "".join(parts)


def _digital_words(text: str, words: list[tuple]) -> list[tuple[int, int, tuple[float, float, float, float]]]:
    """Associate PDF words with exact offsets only when extraction order agrees."""
    mapped = []
    cursor = 0
    for x0, y0, x1, y1, word, *_ in words:
        start = text.find(word, cursor)
        if start < 0 or text[cursor:start].strip():
            break
        end = start + len(word)
        mapped.append((start, end, (x0, y0, x1, y1)))
        cursor = end
    return mapped


def _box_style(box: tuple[float, float, float, float], width: float, height: float) -> str | None:
    x0, y0, x1, y1 = box
    if (not all(isfinite(value) for value in (x0, y0, x1, y1, width, height))
            or width <= 0 or height <= 0 or x1 <= x0 or y1 <= y0):
        return None
    left, top = max(0.0, x0 / width), max(0.0, y0 / height)
    right, bottom = min(1.0, x1 / width), min(1.0, y1 / height)
    if right <= left or bottom <= top:
        return None
    return (f"left:{left * 100:.4f}%;top:{top * 100:.4f}%;"
            f"width:{(right - left) * 100:.4f}%;height:{(bottom - top) * 100:.4f}%")


def _preview_html(source: Path, segment: DocumentSegment, entities: list[DetectedEntity],
                  ocr_dpi: int) -> tuple[str, set[str]]:
    """Render a source page and only source-aligned word boxes."""
    if source.suffix.casefold() == ".pdf":
        import fitz

        with fitz.open(source) as document:
            page = document[segment.page_number - 1]
            image_bytes = page.get_pixmap(dpi=110, alpha=False).tobytes("png")
            width, height = page.rect.width, page.rect.height
            if segment.source_locator.get("extraction") == "digital_text":
                words = [(start, end, tuple(fitz.Rect(box) * page.rotation_matrix))
                         for start, end, box in _digital_words(segment.original_text, page.get_text("words"))]
            else:
                scale = 72 / ocr_dpi
                words = [(word["start"], word["end"], tuple(value * scale for value in word["bounding_box"]))
                         for word in segment.source_locator.get("words", [])]
    else:
        from PIL import Image

        with Image.open(source) as image:
            image.seek(segment.page_number - 1)
            width, height = image.size
            frame = image.convert("RGB")
            frame.thumbnail((1400, 2000))
            output = BytesIO()
            frame.save(output, format="JPEG", quality=80)
            image_bytes = output.getvalue()
        words = [(word["start"], word["end"], tuple(word["bounding_box"]))
                 for word in segment.source_locator.get("words", [])]
    boxes = []
    linked: set[str] = set()
    for entity in entities:
        if entity.span.segment_id != segment.segment_id:
            continue
        for start, end, box in words:
            if end <= entity.span.start or start >= entity.span.end:
                continue
            style = _box_style(box, width, height)
            if style is None:
                continue
            identifier = str(entity.entity_id)
            anchor = f' id="finding-{identifier}"' if identifier not in linked else ""
            label = f'{_TYPE_NAMES.get(entity.entity_type.value, entity.entity_type.value)}: {entity.span.text}'
            boxes.append(f'<a{anchor} class="bbox {entity.decision}" '
                         f'href="#text-finding-{identifier}" style="{style}" '
                         f'title="{escape(label, quote=True)}"></a>')
            linked.add(identifier)
    mime = "image/png" if source.suffix.casefold() == ".pdf" else "image/jpeg"
    image = b64encode(image_bytes).decode("ascii")
    return (f'<div class="page-preview"><img src="data:{mime};base64,{image}" alt="{escape(_location(segment))}">'
            + "".join(boxes) + '</div>'), linked


def _page(content: str = "", title: str = "Локальная демонстрация") -> bytes:
    return f'''<!doctype html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{escape(title)}</title>
<style>
:root{{--paper:#f6f5f1;--ink:#20262b;--muted:#596168;--line:#d9dddc;--blue:#174960}}
*{{box-sizing:border-box}}html{{scroll-behavior:smooth}}body{{margin:0;background:var(--paper);color:var(--ink);font:16px/1.5 system-ui,Segoe UI,Arial,sans-serif}}
header{{background:#173b4a;color:#fff;padding:24px max(24px,calc((100vw - 1180px)/2))}}h1{{font-size:1.55rem;margin:0 0 4px}}header p{{margin:0;color:#d1e0e3}}
main{{max-width:1180px;margin:28px auto;padding:0 20px}}.upload,.summary,.note,.finding,.segment{{background:white;border:1px solid var(--line);border-radius:12px}}
.upload{{padding:20px;display:flex;gap:16px;align-items:end;flex-wrap:wrap}}label{{font-weight:650}}input[type=file]{{display:block;margin-top:8px;max-width:100%}}
button{{border:0;border-radius:8px;padding:11px 20px;background:var(--blue);color:white;font-weight:700;cursor:pointer}}button:hover{{background:#0c3448}}
.hint,.muted{{color:var(--muted)}}.hint{{font-size:.9rem;margin:12px 0 0}}.summary,.note{{padding:14px 18px;margin-top:18px}}
.note{{border-left:5px solid #bc8335}}.error{{border-left:5px solid #bd493e}}.layout{{display:grid;grid-template-columns:330px minmax(0,1fr);gap:20px;margin-top:20px}}
.findings{{max-height:75vh;overflow:auto;position:sticky;top:16px;align-self:start}}.finding{{padding:12px 14px;margin-bottom:10px;overflow-wrap:anywhere}}
.finding a{{color:var(--blue);font-weight:700;text-decoration:none}}.finding a:hover{{text-decoration:underline}}.meta{{font-size:.85rem;color:var(--muted);margin-top:5px}}
.badge{{display:inline-block;border-radius:5px;padding:1px 6px;font-size:.78rem;font-weight:700}}.badge.accept{{background:#d7efdf;color:#165536}}.badge.review{{background:#fff0c4;color:#704d00}}
.segment{{padding:17px 20px;margin-bottom:15px;scroll-margin-top:16px}}.segment h3{{margin:0 0 10px;font-size:1rem}}.document-text{{white-space:pre-wrap;overflow-wrap:anywhere;font:15px/1.75 Georgia,'Times New Roman',serif}}
.page-preview{{position:relative;width:100%;margin:8px 0 16px;background:#edf0f1;border:1px solid var(--line)}}.page-preview img{{display:block;width:100%;height:auto}}
.bbox{{position:absolute;display:block;border:2px solid #16834b;background:#2ecc7140;border-radius:3px;scroll-margin-top:30px}}
.bbox.review{{border-color:#af7100;background:#f5b92855}}.bbox:hover,.bbox:focus,.bbox:target{{outline:3px solid #183d52;z-index:2}}
.text-copy{{border-top:1px solid var(--line);padding-top:10px}}.text-copy summary{{cursor:pointer;color:var(--blue);font-weight:650}}
.docx-page{{background:white;border:1px solid var(--line);box-shadow:0 7px 20px #20333d18;max-width:850px;min-height:900px;padding:48px clamp(25px,6vw,74px)}}
.docx-paragraph{{margin:0 0 1.1em}}.para-label{{display:block;color:var(--muted);font:12px/1.4 system-ui,Segoe UI,Arial,sans-serif;margin-bottom:3px}}
mark{{border-radius:2px;padding:2px 0}}mark.accept{{background:#a8e2bd}}mark.review{{background:#ffe09a}}mark+mark{{margin-left:0}}
@media(max-width:800px){{.layout{{grid-template-columns:1fr}}.findings{{position:static;max-height:none}}}}
</style></head><body><header><h1>Обнаружение персональных данных</h1><p>Локальная демонстрация детерминированного конвейера</p></header>
<main><form class="upload" method="post" enctype="multipart/form-data"><div><label for="document">Документ для проверки</label>
<input id="document" name="document" type="file" accept=".docx,.pdf,.png,.jpg,.jpeg,.tif,.tiff" required></div>
<button type="submit">Проверить документ</button></form>
<p class="hint">DOCX, PDF или изображение до 10 МБ. Файл обрабатывается локально и не сохраняется после запроса. Для сканов нужен установленный Tesseract.</p>
{content}</main></body></html>'''.encode("utf-8")


def _result_html(name: str, parsed, result, source: Path | None = None, ocr_dpi: int = 300) -> str:
    visible = [entity for entity in result.entities if entity.decision in {"accept", "review"}]
    segment_by_id = {segment.segment_id: segment for segment in parsed.segments}
    visible = [entity for entity in visible if entity.span.segment_id in segment_by_id
               and segment_by_id[entity.span.segment_id].original_text[entity.span.start:entity.span.end] == entity.span.text]
    visible.sort(key=lambda entity: (list(segment_by_id).index(entity.span.segment_id), entity.span.start, entity.span.end))
    accepted = sum(entity.decision == "accept" for entity in visible)
    reviewed = len(visible) - accepted
    summary = (f'<section class="summary"><strong>{escape(name)}</strong> · {len(parsed.segments)} фрагментов · '
               f'{accepted} принято · {reviewed} на проверку · '
               f'{result.candidate_count - len(visible)} других кандидатов · конфигурация {escape(result.config_version)}</section>')
    notices = []
    if parsed.status != ProcessingStatus.COMPLETED:
        details = "; ".join(str(error.get("error", "ошибка обработки")) for error in parsed.errors)
        notices.append(f'<section class="note error">Обработка {escape(parsed.status.value)}: {escape(details)}</section>')
    notices.append('<section class="note">Зелёные рамки — принято, жёлтые — требуется проверка. '
                   'Рамка ставится только там, где найденное значение удалось связать с координатами слов. '
                   'Числовой балл не является калиброванной вероятностью. Колонтитулы DOCX не извлекаются.</section>')
    if not parsed.segments:
        return summary + "".join(notices) + '<p class="muted">Текст для проверки не получен.</p>'
    preview_ids: set[str] = set()
    previews: dict[str, str] = {}
    if source is not None and source.suffix.casefold() != ".docx":
        finding_segments = {entity.span.segment_id for entity in visible}
        prioritized = [segment for segment in parsed.segments if segment.segment_id in finding_segments]
        prioritized.extend(segment for segment in parsed.segments if segment.segment_id not in finding_segments)
        selected = {segment.segment_id for segment in prioritized[:_MAX_PREVIEW_PAGES]}
        for segment in parsed.segments:
            if segment.segment_id in selected:
                preview, linked = _preview_html(source, segment, visible, ocr_dpi)
                previews[segment.segment_id] = preview
                preview_ids.update(linked)
        if len(parsed.segments) > len(previews):
            notices.append(f'<section class="note">Показаны {len(previews)} визуальных страниц из '
                           f'{len(parsed.segments)}; остальные доступны в текстовом виде.</section>')
    cards = []
    for entity in visible:
        segment = segment_by_id[entity.span.segment_id]
        signals = ", ".join(dict.fromkeys(evidence.detector_id for evidence in entity.evidence))
        badge = "Принято" if entity.decision == "accept" else "Проверить"
        target = "finding" if str(entity.entity_id) in preview_ids else "text-finding"
        cards.append(f'<article class="finding"><a href="#{target}-{entity.entity_id}">'
                     f'{escape(_TYPE_NAMES.get(entity.entity_type.value, entity.entity_type.value))}: '
                     f'{escape(entity.span.text)}</a><div class="meta">{escape(_location(segment))} · символы '
                     f'{entity.span.start + 1}–{entity.span.end} · <span class="badge {entity.decision}">{badge}</span></div>'
                     f'<div class="meta">Детектор: {escape(signals)} · балл {entity.confidence:.2f}</div></article>')
    if not cards:
        cards.append('<p class="muted">Принятых и требующих проверки находок нет.</p>')
    segments = []
    is_docx = source is None or source.suffix.casefold() == ".docx"
    for segment in parsed.segments:
        heading = _location(segment)
        if segment.ocr_confidence is not None:
            heading += f" · OCR {segment.ocr_confidence:.0%}"
        highlighted = _highlight(segment.original_text, visible, segment.segment_id)
        if is_docx:
            segments.append(f'<p class="docx-paragraph"><span class="para-label">{escape(heading)}</span>'
                            f'<span class="document-text">{highlighted}</span></p>')
            continue
        if segment.segment_id in previews:
            needs_text = any(entity.span.segment_id == segment.segment_id and str(entity.entity_id) not in preview_ids
                             for entity in visible)
            opened = " open" if needs_text else ""
            content = (previews[segment.segment_id] + f'<details class="text-copy"{opened}>'
                       '<summary>Извлечённый текст страницы</summary>'
                       f'<div class="document-text">{highlighted}</div></details>')
        else:
            content = f'<div class="document-text">{highlighted}</div>'
        segments.append(f'<section class="segment"><h3>{escape(heading)}</h3>{content}</section>')
    heading = "Текстовое превью DOCX" if is_docx else "Превью документа"
    document = '<div class="docx-page">' + "".join(segments) + '</div>' if is_docx else "".join(segments)
    return summary + "".join(notices) + '<div class="layout"><aside class="findings"><h2>Находки</h2>' + "".join(cards) + \
        f'</aside><div><h2>{heading}</h2>' + document + '</div></div>'


class DemoHandler(BaseHTTPRequestHandler):
    def _respond(self, body: bytes, status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        self._respond(_page())

    def do_POST(self) -> None:
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= _MAX_UPLOAD + 4096:
                raise ValueError("Размер файла должен быть не больше 10 МБ")
            content_type = self.headers.get("Content-Type", "")
            if not content_type.startswith("multipart/form-data;"):
                raise ValueError("Ожидается загрузка файла")
            message = BytesParser(policy=policy.default).parsebytes(
                f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode("ascii") + self.rfile.read(length)
            )
            upload = next((part for part in message.iter_parts() if part.get_param("name", header="content-disposition") == "document"), None)
            if upload is None or not upload.get_filename():
                raise ValueError("Выберите документ")
            name = Path(upload.get_filename()).name
            suffix = Path(name).suffix.casefold()
            if suffix not in _SUFFIXES:
                raise ValueError("Поддерживаются DOCX, PDF, PNG, JPEG и TIFF")
            data = upload.get_payload(decode=True)
            if not data or len(data) > _MAX_UPLOAD:
                raise ValueError("Файл пуст или превышает 10 МБ")
            with TemporaryDirectory(prefix="pii-demo-") as directory:
                source = Path(directory) / f"upload{suffix}"
                source.write_bytes(data)
                registry = ParserRegistry()
                registry.register(DocxDocumentParser())
                ocr_config = load_ocr_config(_ROOT / "configs" / "ocr_v1.json")
                registry.register(OCRDocumentParser(ocr_config))
                parsed = registry.parse(sha256(data).hexdigest()[:16], source)
                result = DeterministicPipeline(load_config(_ROOT / "configs" / "deterministic_v3.json")).detect_parsed(parsed)
                content = _result_html(name, parsed, result, source, ocr_config.dpi)
            self._respond(_page(content, name))
        except (ValueError, OSError, UnicodeError) as error:
            self._respond(_page(f'<section class="note error">{escape(str(error))}</section>'), 400)


def main() -> None:
    parser = argparse.ArgumentParser(description="Local PII document review demo")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    with ThreadingHTTPServer(("127.0.0.1", args.port), DemoHandler) as server:
        print(f"Откройте http://127.0.0.1:{args.port}")
        server.serve_forever()


if __name__ == "__main__":
    main()
