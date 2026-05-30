# AIDA Model Rule Collection

目的: アイダエンジニアリング配下の資料から、図面番号ごとの期待型式とOCR実測を集めて、`extract_models_from_drawing_pdfs.py` の `MODEL_RULES` に足す。

| 図面番号 | 期待型式 | 現在の抽出結果 | OCR実測メモ | 確認状況 |
| --- | --- | --- | --- | --- |
| 207-35987 | PMX-12000 | PMX-12000 |  | confirmed |
| 209-43161 | TMX | TMX |  | confirmed |
| 209-51360 | TMX | TMX |  | confirmed |
| 213-01647 | PL4-150 | PL4-150 |  | confirmed |
| 213-01648 | PL4-150 | PL4-150 |  | confirmed |
| 213-08859 | CW(特) | 共通 | CW(特) | needs confirmation |
| 213-09366 | FT | FT |  | confirmed |
| 213-10497 | T.C-378/317x64 | T.C-378/317x64 |  | confirmed |
| 213-11149 | FT | FT |  | confirmed |
| 309-42810 | FT | FT |  | confirmed |
| 309-66175 | TMX | TMX |  | confirmed |
| 309-86886 | FT | FT |  | confirmed |
| 309-88376 | FT | FT |  | confirmed |
| 309-88378 | TMX | TMX |  | confirmed |
| 309-92338 | TMX | TMX |  | confirmed |
| 313-12673 | FT | FT |  | confirmed |
| 358-17466 | UL-3000 | UL-3000 |  | confirmed |
| 409-85335 | TMX | TMX |  | confirmed |
| 409-85336 | TMX | TMX |  | confirmed |
| 409-87770 | TMX | TMX |  | confirmed |
| 409-95412 | TMX | TMX |  | confirmed |
| 413-25079 | FT | FT |  | confirmed |
| 430-12600 | A7365 | A7365 |  | confirmed |
| 509-37290 | TMX | TMX |  | confirmed |
| 509-44251 | TMX | TMX |  | confirmed |

## Next candidates to verify
- 213-10497 is already confirmed, but OCR text should be archived as evidence
- Any row currently marked `共通`

## Notes
- Add new rules to `MODEL_RULES` only after 2-3 confirmed examples of the same pattern.
- Keep OCR mistakes in `OCR実測メモ` so normalization rules stay traceable.
- If a row is ambiguous, keep `期待型式` blank until confirmed.
