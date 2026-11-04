# Этап 10. Протокол проверок

Дата: 04.11.2026. Проверен текущий checkout на локальной Windows-среде. Тестовые документы синтетические; в этом протоколе приведены только типы сущностей, статусы и агрегированные метрики.

## Прогоны

```text
uv run --with pytest --with httpx --with python-docx --with pymupdf --with pillow --with pytesseract pytest -q tests/unit tests/integration
109 passed in 67.91s

$env:PII_LIVE_MODEL_TEST='1'; & .\.venv\Scripts\python.exe -m pytest -q tests/integration/test_system.py::test_live_person_and_deterministic_http_inference
1 passed, 1 warning in 20.86s (запуск вне управляемой песочницы)

uv run python -m evaluation.run --dataset data/dev_set --config configs/deterministic_v3.json
exit code 0; таблица TP/FP/FN/Precision/Recall/F1 по 12 классам
```

Отдельно выполнен `evaluation.benchmark_hybrid_replay.benchmark` на `data/pii_bench`: 910 документов `entity` и 900 документов `domain`. Micro F1: 0,9681 и 0,9716 соответственно. Это воспроизведение сохранённых предсказаний PERSON, а не новый запуск семантической модели. `data/dev_set` является набором для разработки, а не независимой финальной оценкой.

## Сценарии

| Сценарий | Статус | Фактическое подтверждение |
|---|---|---|
| Авторизация и отказ неавторизованному пользователю | PASS | `test_catalog_and_detection_scenario`: без учётных данных и с неверным паролем `/api/objects` возвращает 401; с правильными данными главная страница возвращает 200. |
| Инвентаризация каталога | PASS | `test_inventory_tracks_all_files_and_missing_sources`: сканирование создаёт объект и снимок `(1, размер)`; после удаления исходного файла следующий снимок `(0, 0)`, объект получает `missing`. |
| Создание, изменение, удаление и поиск записей | PASS | `test_roles_catalog_crud_and_upload`: создание 201, PATCH меняет имя и категорию, фильтр по типу и размеру возвращает нужную запись, DELETE убирает запись без удаления исходного файла. |
| Обнаружение дубликатов | PASS | `test_report_counts_each_candidate_once_and_separates_actions`: из трёх синтетических объектов найдена одна полная копия, 10 избыточных байт; общий объём кандидатов 30 байт без повторного счёта. |
| Расчёт показателей экологичности | PASS | `test_report_counts_each_candidate_once_and_separates_actions`, `test_unknown_values_do_not_become_dark_or_retention_violations`, `test_estimated_effect_uses_configured_coefficients_once`: проверены объём, неизвестные значения, индекс, стоимость и энергия; `/api/ecology` возвращает измеренный объём в `test_catalog_and_detection_scenario`. |
| Загрузка TXT/PDF и обнаружение ПДн | PASS | `test_txt_analysis_returns_highlightable_spans`: TXT даёт `EMAIL` и `PHONE` с исходными смещениями. `test_http_pdf_scan_clean_document_and_invalid_files`: синтетический PDF даёт один `EMAIL` с координатами `pdf_points`. |
| OCR на скане | PASS | `test_http_pdf_scan_clean_document_and_invalid_files`: реальный Tesseract обработал синтетический PNG, статус `completed`, один `EMAIL`, извлечение `tesseract`, координаты `image_pixels`. Модульные тесты также проверяют confidence и частичную ошибку страницы. |
| Гибридный inference PERSON + deterministic | PASS | `test_live_person_and_deterministic_http_inference`: локальная GLiNER2 через HTTP `/api/analyze` вернула 200, режим `hybrid`, `model_error=null`, классы `PERSON` и `EMAIL`; исходные смещения обеих сущностей совпадают с текстом синтетического TXT. Тест с тестовым семантическим адаптером отдельно проверяет композицию и provenance. Replay pii-bench не является свидетельством этого живого запуска. |
| Документ без ПДн | PASS | `test_http_pdf_scan_clean_document_and_invalid_files`: синтетический TXT возвращает `completed`, пустые `findings_by_type` и `entities`. |
| Недоступность семантической модели | PASS | `test_catalog_and_detection_scenario` и `test_txt_analysis_returns_highlightable_spans`: при подмене недоступной модели API переходит в `deterministic_only`, сохраняет `model_error` и находит структурированные сущности. |
| Архивирование и подтверждаемое удаление | PASS | `test_archive_backup_restore_and_confirmed_delete`: архивирование удаляет исходник из рабочего каталога; неверное подтверждение даёт 409; точная строка подтверждения удаляет архивный файл, статус становится `deleted`, операции видны в отчёте. |
| Полная и инкрементальная резервная копия | PASS | `test_archive_backup_restore_and_confirmed_delete`: оба запроса `/api/backups` возвращают 200; инкрементальная копия ссылается на созданную полную. |
| Восстановление и проверка целостности | PASS | Тот же тест: восстановлены SQLite, действующий файл и архивный файл; статус в восстановленной БД совпадает с файлами. Изменение байтов копии вызывает `checksum mismatch`, каталог неуспешного восстановления не остаётся. |
| Разграничение прав | PASS | `test_roles_catalog_crud_and_upload`: оператор получает 403 при запросе пользователей и изменении роли; после перевода в аудитора получает 403 на создание записи, сканирование и загрузку, но 200 на журнал. |
| Ошибки и некорректные файлы | PASS | `test_http_pdf_scan_clean_document_and_invalid_files`: пустой и неподдерживаемый файл дают 400; повреждённый PDF даёт явный статус `failed` и ошибку `source_open`. `test_txt_analysis_returns_highlightable_spans`: изменённый после инвентаризации файл даёт 409. |

## Ограничение

Первоначальный запуск внутри управляемой песочницы завершился ошибкой загрузки NumPy DLL (`WinError 5` при открытии `_multiarray_umath.pyd`). Та же `.venv` вне песочницы импортирует NumPy, PyTorch и GLiNER2 и проходит живой HTTP-тест, поэтому переустановка зависимостей не требовалась. Живой тест включается явно через `PII_LIVE_MODEL_TEST=1` и по умолчанию пропускается; для демонстрации нужен запуск в среде, разрешающей загрузку нативных библиотек. Прохождение одного синтетического примера не доказывает качество на документах организации. Предупреждения TorchScript и PyMuPDF не повлияли на результат тестов.
