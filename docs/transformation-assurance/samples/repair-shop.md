# 변환 보증 보고서 / Transformation assurance report - shop-repair-sample

## 결과 / Verdict

**선언한 범위 안에서 기능이 유지되었습니다** / Behaviour preserved within the declared envelope (`PASS`, decided by `preserved`)

## 세 가지 질문 / Three questions

### 전이랑 같아? / Same as before? - yes

- 확인한 입력 6건에서 비교한 값은 모두 이전과 같았습니다.
- 추가로 만들어 본 입력 80건에서도 차이를 찾지 못했습니다. (증명은 아닙니다)
- 성능은 검증하지 않았습니다: 성능 측정 규약(performance_envelope 클레임)이 선언되지 않았습니다.
- 참고: 비교 실행 6회의 부수적인 실행 시간 합계는 이전 0.760974초, 이후 0.636174초였습니다. 성능 검증이 아닙니다.

- On all 6 recorded input(s), the behaviour after the change matched the behaviour before it on every compared value.
- 80 additional generated input(s) were tried without finding a difference. This is evidence, not a proof.
- Performance was NOT VERIFIED: no performance_envelope claim declared a measurement protocol and tolerance.
- For reference only: incidental wall-clock of the 6 single comparison run(s) was before 0.760974s, after 0.636174s. This is not a performance verification.

### 이상한 점 있어? / Anything odd? - listed

- 할인 기준 정리 / tidy the discount threshold (되돌림: behaviour diverged at the discount boundary)

- 할인 기준 정리 / tidy the discount threshold - rejected: behaviour diverged at the discount boundary; divergence at /db/tables/order_lines/rows/0/line_total, /db/tables/orders/rows/0/total, /files/entries/receipt.txt/text, /out/value/lines/0/line_total, /out/value/total

### 모르면 솔직히 말해! / What could you not check? - listed

- 관찰된 값 54개 중 40개를 비교했습니다; 비교하지 않은 값: 선언으로 제외 1개, 정책으로 제거되거나 대체 10개, 비교를 끝내지 못함 3개.
- 선언으로 비교에서 제외한 값: /cli/stderr
- 정책으로 제거되거나 대체되어 비교하지 않은 값: /db/tables/order_lines/rows/0/id
- 정책으로 제거되거나 대체되어 비교하지 않은 값: /db/tables/order_lines/rows/0/order_row_id
- 정책으로 제거되거나 대체되어 비교하지 않은 값: /db/tables/order_lines/rows/1/id
- 정책으로 제거되거나 대체되어 비교하지 않은 값: /db/tables/order_lines/rows/1/order_row_id
- 정책으로 제거되거나 대체되어 비교하지 않은 값: /db/tables/orders/rows/0/created_at
- 정책으로 제거되거나 대체되어 비교하지 않은 값: /db/tables/orders/rows/0/id
- 정책으로 제거되거나 대체되어 비교하지 않은 값: /db/tables/orders/rows/0/order_id
- 정책으로 제거되거나 대체되어 비교하지 않은 값: /files/entries/receipt.txt/digest
- 선언한 값을 얻지 못해 비교하지 않은 곳: /out/parse_error
- 선언한 값을 얻지 못해 비교하지 않은 곳: /out/text_digest
- 선언한 값을 얻지 못해 비교하지 않은 곳: /out/text_head
- 정책으로 제거되거나 대체되어 비교하지 않은 값: /out/value/created_at
- 정책으로 제거되거나 대체되어 비교하지 않은 값: /out/value/order_id
- 순서가 바뀌어도 눈치채지 못했을 것입니다: /out/value/tags

- 40 of 54 observed values were compared; not compared: 1 excluded by declaration, 10 removed or replaced by policy, 3 could not be compared.
- excluded by declaration, not compared: /cli/stderr (excluded by stderr-text: diagnostic text: a traceback names files and lines that legitimately change under a refactor; the exit code and stdout are the contract; as declared, a change here would not have been noticed)
- not compared after policy: /db/tables/order_lines/rows/0/id (identity replaced by policy row-ids (generated_id); a different valid value here passes, only its relationships to other occurrences are compared; as declared, a change here would not have been noticed)
- not compared after policy: /db/tables/order_lines/rows/0/order_row_id (identity replaced by policy row-ids (generated_id); a different valid value here passes, only its relationships to other occurrences are compared; as declared, a change here would not have been noticed)
- not compared after policy: /db/tables/order_lines/rows/1/id (identity replaced by policy row-ids (generated_id); a different valid value here passes, only its relationships to other occurrences are compared; as declared, a change here would not have been noticed)
- not compared after policy: /db/tables/order_lines/rows/1/order_row_id (identity replaced by policy row-ids (generated_id); a different valid value here passes, only its relationships to other occurrences are compared)
- not compared after policy: /db/tables/orders/rows/0/created_at (replaced by policy timestamps (timestamp); as declared, a change here would not have been noticed)
- not compared after policy: /db/tables/orders/rows/0/id (identity replaced by policy row-ids (generated_id); a different valid value here passes, only its relationships to other occurrences are compared; as declared, a change here would not have been noticed)
- not compared after policy: /db/tables/orders/rows/0/order_id (identity replaced by policy order-ids (generated_id); a different valid value here passes, only its relationships to other occurrences are compared; as declared, a change here would not have been noticed)
- not compared after policy: /files/entries/receipt.txt/digest (removed by policy receipt-digest (ignore); as declared, a change here would not have been noticed)
- the declared value was not obtained on this input and its diagnostic was not compared: /out/parse_error (the declared observable was not obtained on this input (the declared JSON value was not obtained (parse error: Expecting value: line 1 column 1 (char 0))); a diagnostic of the attempt is not compared as behaviour; as declared, a change here would not have been noticed)
- the declared value was not obtained on this input and its diagnostic was not compared: /out/text_digest (the declared observable was not obtained on this input (the declared JSON value was not obtained (parse error: Expecting value: line 1 column 1 (char 0))); a diagnostic of the attempt is not compared as behaviour; as declared, a change here would not have been noticed)
- the declared value was not obtained on this input and its diagnostic was not compared: /out/text_head (the declared observable was not obtained on this input (the declared JSON value was not obtained (parse error: Expecting value: line 1 column 1 (char 0))); a diagnostic of the attempt is not compared as behaviour; as declared, a change here would not have been noticed)
- not compared after policy: /out/value/created_at (replaced by policy timestamps (timestamp); as declared, a change here would not have been noticed)
- not compared after policy: /out/value/order_id (identity replaced by policy order-ids (generated_id); a different valid value here passes, only its relationships to other occurrences are compared; as declared, a change here would not have been noticed)
- a change of order here would not have been noticed: /out/value/tags

## 요약 / Summary

### 기능 유지 / Behaviour preserved - yes

- 확인한 입력 6건에서 비교한 값은 모두 이전과 같았습니다.
- 추가로 만들어 본 입력 80건에서도 차이를 찾지 못했습니다. (증명은 아닙니다)

- On all 6 recorded input(s), the behaviour after the change matched the behaviour before it on every compared value.
- 80 additional generated input(s) were tried without finding a difference. This is evidence, not a proof.

### 성능 유지 / Performance - not_verified

- 성능은 검증하지 않았습니다: 성능 측정 규약(performance_envelope 클레임)이 선언되지 않았습니다.
- 참고: 비교 실행 6회의 부수적인 실행 시간 합계는 이전 0.760974초, 이후 0.636174초였습니다. 성능 검증이 아닙니다.

- Performance was NOT VERIFIED: no performance_envelope claim declared a measurement protocol and tolerance.
- For reference only: incidental wall-clock of the 6 single comparison run(s) was before 0.760974s, after 0.636174s. This is not a performance verification.

### 정리 완료 항목 / Cleanup completed - listed

- 가격 계산·저장 분리 / split pricing and storage out of app.py (반영됨) 중복 블록 11 -> 0

- 가격 계산·저장 분리 / split pricing and storage out of app.py - accepted as commit 970725a81559d50235eccd8ac5e726c1795c0986; duplicate blocks 11 -> 0

### 되돌린 변경 / Changes reverted - listed

- 할인 기준 정리 / tidy the discount threshold (되돌림: behaviour diverged at the discount boundary)

- 할인 기준 정리 / tidy the discount threshold - rejected: behaviour diverged at the discount boundary; divergence at /db/tables/order_lines/rows/0/line_total, /db/tables/orders/rows/0/total, /files/entries/receipt.txt/text, /out/value/lines/0/line_total, /out/value/total

### 확인하지 못한 영역 / Not verified - listed

- 관찰된 값 54개 중 40개를 비교했습니다; 비교하지 않은 값: 선언으로 제외 1개, 정책으로 제거되거나 대체 10개, 비교를 끝내지 못함 3개.
- 선언으로 비교에서 제외한 값: /cli/stderr
- 정책으로 제거되거나 대체되어 비교하지 않은 값: /db/tables/order_lines/rows/0/id
- 정책으로 제거되거나 대체되어 비교하지 않은 값: /db/tables/order_lines/rows/0/order_row_id
- 정책으로 제거되거나 대체되어 비교하지 않은 값: /db/tables/order_lines/rows/1/id
- 정책으로 제거되거나 대체되어 비교하지 않은 값: /db/tables/order_lines/rows/1/order_row_id
- 정책으로 제거되거나 대체되어 비교하지 않은 값: /db/tables/orders/rows/0/created_at
- 정책으로 제거되거나 대체되어 비교하지 않은 값: /db/tables/orders/rows/0/id
- 정책으로 제거되거나 대체되어 비교하지 않은 값: /db/tables/orders/rows/0/order_id
- 정책으로 제거되거나 대체되어 비교하지 않은 값: /files/entries/receipt.txt/digest
- 선언한 값을 얻지 못해 비교하지 않은 곳: /out/parse_error
- 선언한 값을 얻지 못해 비교하지 않은 곳: /out/text_digest
- 선언한 값을 얻지 못해 비교하지 않은 곳: /out/text_head
- 정책으로 제거되거나 대체되어 비교하지 않은 값: /out/value/created_at
- 정책으로 제거되거나 대체되어 비교하지 않은 값: /out/value/order_id
- 순서가 바뀌어도 눈치채지 못했을 것입니다: /out/value/tags

- 40 of 54 observed values were compared; not compared: 1 excluded by declaration, 10 removed or replaced by policy, 3 could not be compared.
- excluded by declaration, not compared: /cli/stderr (excluded by stderr-text: diagnostic text: a traceback names files and lines that legitimately change under a refactor; the exit code and stdout are the contract; as declared, a change here would not have been noticed)
- not compared after policy: /db/tables/order_lines/rows/0/id (identity replaced by policy row-ids (generated_id); a different valid value here passes, only its relationships to other occurrences are compared; as declared, a change here would not have been noticed)
- not compared after policy: /db/tables/order_lines/rows/0/order_row_id (identity replaced by policy row-ids (generated_id); a different valid value here passes, only its relationships to other occurrences are compared; as declared, a change here would not have been noticed)
- not compared after policy: /db/tables/order_lines/rows/1/id (identity replaced by policy row-ids (generated_id); a different valid value here passes, only its relationships to other occurrences are compared; as declared, a change here would not have been noticed)
- not compared after policy: /db/tables/order_lines/rows/1/order_row_id (identity replaced by policy row-ids (generated_id); a different valid value here passes, only its relationships to other occurrences are compared)
- not compared after policy: /db/tables/orders/rows/0/created_at (replaced by policy timestamps (timestamp); as declared, a change here would not have been noticed)
- not compared after policy: /db/tables/orders/rows/0/id (identity replaced by policy row-ids (generated_id); a different valid value here passes, only its relationships to other occurrences are compared; as declared, a change here would not have been noticed)
- not compared after policy: /db/tables/orders/rows/0/order_id (identity replaced by policy order-ids (generated_id); a different valid value here passes, only its relationships to other occurrences are compared; as declared, a change here would not have been noticed)
- not compared after policy: /files/entries/receipt.txt/digest (removed by policy receipt-digest (ignore); as declared, a change here would not have been noticed)
- the declared value was not obtained on this input and its diagnostic was not compared: /out/parse_error (the declared observable was not obtained on this input (the declared JSON value was not obtained (parse error: Expecting value: line 1 column 1 (char 0))); a diagnostic of the attempt is not compared as behaviour; as declared, a change here would not have been noticed)
- the declared value was not obtained on this input and its diagnostic was not compared: /out/text_digest (the declared observable was not obtained on this input (the declared JSON value was not obtained (parse error: Expecting value: line 1 column 1 (char 0))); a diagnostic of the attempt is not compared as behaviour; as declared, a change here would not have been noticed)
- the declared value was not obtained on this input and its diagnostic was not compared: /out/text_head (the declared observable was not obtained on this input (the declared JSON value was not obtained (parse error: Expecting value: line 1 column 1 (char 0))); a diagnostic of the attempt is not compared as behaviour; as declared, a change here would not have been noticed)
- not compared after policy: /out/value/created_at (replaced by policy timestamps (timestamp); as declared, a change here would not have been noticed)
- not compared after policy: /out/value/order_id (identity replaced by policy order-ids (generated_id); a different valid value here passes, only its relationships to other occurrences are compared; as declared, a change here would not have been noticed)
- a change of order here would not have been noticed: /out/value/tags

### 다음에 사람이 볼 것 / For a person to look at next - none

- 없음

- None.

## Technical assurance report

### Manifest

- digest: `55360cd327bfdf8a2aa6a0e3af727a7331b2fa62fdf6e1658ba81b30ce1a3976`
- history: `55360cd327bfdf8a2aa6a0e3af727a7331b2fa62fdf6e1658ba81b30ce1a3976`
- schema: invara.assurance.manifest/1
- source: `<python> app.py` at `$SOURCE_ROOT`
- target: `<python> app.py` at `$TARGET_ROOT`
- input domain: corpus via stdin_json, corpus 6

### Baseline

- baseline digest: `5e8a3329536cd16a4617a20de1f161e31584952fa94d5e62451ab3891c118af7`
- policy set digest: `caf364dde1718cb77dbbd316b4164ebf49ae8b8181c49516755f31150cc0cc51`
- bulk-gold: `6a9da92b104f779f597306e7d16e25e2bbc9d9e3d4c6d971a7c4af1161dec8ec`
- bulk-silver-coupon: `e4db1f9d973b4cb2e4f85c5383513d8620d6f7e10a4ab3cb7079ab5847184db5`
- coupon-too-small: `b6a0c2f936b0f44aff3dc877d9e89bea0793027d3a5b41346cb45cf298939f8a`
- invalid-qty: `a8379bb890080036ccff73226611c04db1e31a630882227718fba68ceac4ecb5`
- nine-gold: `7c370c9dbb3bd8b4542f1ab56fc6850d435939c65089221463c8f988247b5e4a`
- single: `9011f41844b772a17a22bbc501cd14a87fff8cbdea1123dc4883297a6c587e8b`

### Policies

- `order-ids` generated_id at `/out/value/order_id` (+2 more) - the order id is a fresh UUID on every run; the same id must appear in the summary, the receipt and the database row [declared, accepted]
- `row-ids` generated_id at `/db/tables/orders/rows/*/id` (+2 more) - autoincrement keys; lines must still point at their order [declared, accepted]
- `timestamps` timestamp at `/out/value/created_at` (+2 more) - created_at is the wall clock at run time [declared, accepted]
- `tags-order` unordered_multiset at `/out/value/tags` - the tag list is sorted by a hash of the fresh order id; the set of tags is behaviour, its order is not [declared, accepted]
- `receipt-digest` ignore at `/files/entries/*/digest` - the receipt text is compared in full under the id and timestamp policies; its byte digest necessarily follows the generated id and says nothing more [declared, accepted]

### Coverage

- exhaustively_proved: (none)
- tested_over_finite_corpus: corpus
- searched_without_divergence: stability, search
- diverged: (none)
- not_verified: (none)
- needs_human: (none)
- explicitly_excluded: stderr-text
- not_observed: (none)

### Coverage map

- 40 of 54 observed values were compared; not compared: 1 excluded by declaration, 10 removed or replaced by policy, 3 could not be compared.
- by state: TESTED 40, EXCLUDED 1, UNOBSERVED 10, UNVERIFIABLE 3
- probe `cli` (process, mandatory): TESTED (TESTED 2, EXCLUDED 1)
- probe `out` (json, mandatory): TESTED (TESTED 11, UNOBSERVED 2, UNVERIFIABLE 3)
- probe `files` (filesystem, mandatory): TESTED (TESTED 4, UNOBSERVED 1)
- probe `db` (sqlite, mandatory): TESTED (TESTED 23, UNOBSERVED 7)
  - `/cli/stderr`: EXCLUDED (excluded by stderr-text: diagnostic text: a traceback names files and lines that legitimately change under a refactor; the exit code and stdout are the contract; as declared, a change here would not have been noticed)
  - `/db/tables/order_lines/rows/0/id`: UNOBSERVED (identity replaced by policy row-ids (generated_id); a different valid value here passes, only its relationships to other occurrences are compared; as declared, a change here would not have been noticed)
  - `/db/tables/order_lines/rows/0/order_row_id`: UNOBSERVED (identity replaced by policy row-ids (generated_id); a different valid value here passes, only its relationships to other occurrences are compared; as declared, a change here would not have been noticed)
  - `/db/tables/order_lines/rows/1/id`: UNOBSERVED (identity replaced by policy row-ids (generated_id); a different valid value here passes, only its relationships to other occurrences are compared; as declared, a change here would not have been noticed)
  - `/db/tables/order_lines/rows/1/order_row_id`: UNOBSERVED (identity replaced by policy row-ids (generated_id); a different valid value here passes, only its relationships to other occurrences are compared)
  - `/db/tables/orders/rows/0/created_at`: UNOBSERVED (replaced by policy timestamps (timestamp); as declared, a change here would not have been noticed)
  - `/db/tables/orders/rows/0/id`: UNOBSERVED (identity replaced by policy row-ids (generated_id); a different valid value here passes, only its relationships to other occurrences are compared; as declared, a change here would not have been noticed)
  - `/db/tables/orders/rows/0/order_id`: UNOBSERVED (identity replaced by policy order-ids (generated_id); a different valid value here passes, only its relationships to other occurrences are compared; as declared, a change here would not have been noticed)
  - `/files/entries/receipt.txt/digest`: UNOBSERVED (removed by policy receipt-digest (ignore); as declared, a change here would not have been noticed)
  - `/out/parse_error`: UNVERIFIABLE (the declared observable was not obtained on this input (the declared JSON value was not obtained (parse error: Expecting value: line 1 column 1 (char 0))); a diagnostic of the attempt is not compared as behaviour; as declared, a change here would not have been noticed)
  - `/out/text_digest`: UNVERIFIABLE (the declared observable was not obtained on this input (the declared JSON value was not obtained (parse error: Expecting value: line 1 column 1 (char 0))); a diagnostic of the attempt is not compared as behaviour; as declared, a change here would not have been noticed)
  - `/out/text_head`: UNVERIFIABLE (the declared observable was not obtained on this input (the declared JSON value was not obtained (parse error: Expecting value: line 1 column 1 (char 0))); a diagnostic of the attempt is not compared as behaviour; as declared, a change here would not have been noticed)
  - `/out/value/created_at`: UNOBSERVED (replaced by policy timestamps (timestamp); as declared, a change here would not have been noticed)
  - `/out/value/order_id`: UNOBSERVED (identity replaced by policy order-ids (generated_id); a different valid value here passes, only its relationships to other occurrences are compared; as declared, a change here would not have been noticed)
- claim `corpus` (corpus_equivalence): TESTED
- claim `search` (counterexample_search): SEARCHED
- claim `stability` (baseline_stability): OBSERVED

### Blind-spot scan

- 54 value path(s) over 6 input(s), 263 of 263 site(s) mutated under the manifest in force; 41 sensitive, 5 blind, 8 replaced by a declared placeholder, 0 dulled, 0 fail closed
- boundary: tried 221, noticed 206, missed 15, unverifiable 0
- empty: tried 151, noticed 137, missed 14, unverifiable 0
- null: tried 221, noticed 207, missed 14, unverifiable 0
- order: tried 17, noticed 12, missed 5, unverifiable 0
- relationship: tried 113, noticed 112, missed 1, unverifiable 0
- same_shape: tried 37, noticed 4, missed 33, unverifiable 0
- type_flip: tried 221, noticed 207, missed 14, unverifiable 0
  - blind: `/cli/stderr` (tried boundary, empty, null, type_flip)
  - blind: `/files/entries/receipt.txt/digest` (tried boundary, empty, null, type_flip)
  - blind: `/out/parse_error` (tried boundary, empty, type_flip, null)
  - blind: `/out/text_digest` (tried boundary, empty, type_flip, null)
  - blind: `/out/text_head` (tried boundary, empty, type_flip, null)
  - placeholder: `/db/tables/order_lines/rows/0/id` (policy row-ids (generated_id); another value of the same shape passes)
  - placeholder: `/db/tables/order_lines/rows/0/order_row_id` (policy row-ids (generated_id); another value of the same shape passes)
  - placeholder: `/db/tables/order_lines/rows/1/id` (policy row-ids (generated_id); another value of the same shape passes)
  - placeholder: `/db/tables/orders/rows/0/created_at` (policy timestamps (timestamp); another value of the same shape passes)
  - placeholder: `/db/tables/orders/rows/0/id` (policy row-ids (generated_id); another value of the same shape passes)
  - placeholder: `/db/tables/orders/rows/0/order_id` (policy order-ids (generated_id); another value of the same shape passes)
  - placeholder: `/out/value/created_at` (policy timestamps (timestamp); another value of the same shape passes)
  - placeholder: `/out/value/order_id` (policy order-ids (generated_id); another value of the same shape passes)
- synthetic mutations of the frozen baseline under the manifest in force; a blind spot is a value no tested change would have flagged; a placeholder is a value the declaration replaces, where another value of the same shape passes by design; this measures the equivalence definition, not the transformation

### Claims

- `stability` (baseline_stability, mandatory): **NO_DIVERGENCE_FOUND** - 7 volatile path(s) over 2 run(s), every one covered by an accepted policy
- `corpus` (corpus_equivalence, mandatory): **PRESERVED_WITHIN_ENVELOPE** - 6 corpus input(s) compared equivalent under the declared policies
- `search` (counterexample_search, mandatory): **NO_DIVERGENCE_FOUND** - 80 candidate(s) compared without divergence; this is not a proof

### Divergences

- none

### Counterexamples

- none

### Repair units

- accepted `u1`: 가격 계산·저장 분리 / split pricing and storage out of app.py - commit `970725a81559d50235eccd8ac5e726c1795c0986`, tree `be183500559d63f0dbdffaf8daddd27e0feaef98`
- rejected `u2`: 할인 기준 정리 / tidy the discount threshold - behaviour diverged at the discount boundary, patch `b45ae7dd3a7e959dcc194e92fd6e1b13e35043b893fcd16389cea13406863b01`

### Findings and metrics

- duplicate_implementation `dup-pricing` (host-agent): quote() and commit_order() carry the same pricing loop - app.py
- mixed_responsibilities `mixed` (host-agent): pricing, storage and formatting share one function - app.py
- metrics before: {'duplicate_blocks': {'blocks': [{'digest': '55695f97b999d734a1cf44c16193503fc7e1140c53cc267e0953872a3c21a0d1', 'occurrences': [{'file': 'app.py', 'line': 40}, {'file': 'app.py', 'line': 60}]}, {'digest': '562a8131774fecc365f35f95b4afb29e724996fa2bcbe27d4821ee73123086c1', 'occurrences': [{'file': 'app.py', 'line': 31}, {'file': 'app.py', 'line': 51}]}, {'digest': '5d84ce2839f17f3debff115f22756c9190a330fffd6224687984337ef98f1692', 'occurrences': [{'file': 'app.py', 'line': 33}, {'file': 'app.py', 'line': 53}]}, {'digest': '5f80fa59b2abaca4aefdc9f020bb9249099844a10c043deb572351e59bf6e5a5', 'occurrences': [{'file': 'app.py', 'line': 38}, {'file': 'app.py', 'line': 58}]}, {'digest': '64853c6d8a2d79df855dc846b814e25cafab12da5d99dbbb7797a693c734eb7f', 'occurrences': [{'file': 'app.py', 'line': 36}, {'file': 'app.py', 'line': 56}]}, {'digest': '8f69e2cbeacb81b46263206c70331b64a394142dc0552a06c95633bd6fa0f4f2', 'occurrences': [{'file': 'app.py', 'line': 30}, {'file': 'app.py', 'line': 50}]}, {'digest': '9a22e7dbd52dcfe83e2dbf1d47750b8e3732cabb29d1f2bda1883065d2b3700a', 'occurrences': [{'file': 'app.py', 'line': 37}, {'file': 'app.py', 'line': 57}]}, {'digest': 'bbb4679bec2b39a786df46a310f6124008d523a8b52c1c93ed07057e5b0c5495', 'occurrences': [{'file': 'app.py', 'line': 32}, {'file': 'app.py', 'line': 52}]}, {'digest': 'ca0cebb537f584204871e8b7744ffbc993deaf690b2efcfe1ef9937ccdd1bc4b', 'occurrences': [{'file': 'app.py', 'line': 34}, {'file': 'app.py', 'line': 54}]}, {'digest': 'e3b9b59111d76d5687d1553bd085ab5289fd171c80d9b842f20d3b5f12004261', 'occurrences': [{'file': 'app.py', 'line': 39}, {'file': 'app.py', 'line': 59}]}, {'digest': 'e424f5d98d3963982c15379b210b4a51f94dd061a0e43c02d66fbb9f853aa18a', 'occurrences': [{'file': 'app.py', 'line': 35}, {'file': 'app.py', 'line': 55}]}], 'count': 11, 'window': 6}, 'files': 2, 'include': ['**/*.py'], 'largest': [{'file': 'app.py', 'lines': 127}, {'file': 'test_app.py', 'lines': 33}], 'lines': 160, 'per_file': {'app.py': 127, 'test_app.py': 33}, 'python': {'cycles': [], 'edges': [['test_app', 'app']], 'modules': ['app', 'test_app'], 'public_names': {'app': ['TIER_DISCOUNT', 'commit_order', 'main', 'quote'], 'test_app': ['Quote']}}, 'size_distribution': {'201-500': 0, '501-1000': 0, '51-200': 1, '<=50': 1, '>1000': 0}, 'tree_digest': '5581fedb1811f3701eb27f4951761c085c84dd1d63b638ab4842f41807c92d53', 'unparsed': [], 'window': 6}

### Amendments

- none

### Provenance

- repository: <sandbox>\shop
- base_commit: c8551e1ade9b7faa885e4db5b56b68416c79149d
- accepted_commit: 970725a81559d50235eccd8ac5e726c1795c0986
- workspace: <sandbox>\repair\shop-repair-sample
- roots: {'SOURCE_ROOT': '<sandbox>\\repair\\shop-repair-sample\\baseline', 'TARGET_ROOT': '<sandbox>\\repair\\shop-repair-sample\\baseline'}
- manifest_provenance: {}
- tool_versions: {'python': '3.12.10', 'invara': '0.1.3', 'git': 'see provenance.git'}
- platform: Windows-11-10.0.26200-SP0
- evidence_store: <sandbox>\verify.db
- evidence_chain_heads: {'assurance_manifest': '7155b51a29e21d64dda6e2df1f1cf016bda4987f887be52304ada426e995b2a9', 'assurance_observation': 'fc16b7361388434723f414e38fcd59d1b42c73f2e6a501ba7688bb4dc745e006', 'assurance_event': '58308d38825018dda989feae8bb8471d54dffd03bf7dac1481fc4fc32b477d9a'}

### Final verdict

- `PASS` decided by `preserved`: 1 mandatory claim(s) preserved and 2 with no divergence found under the declared envelope
- preserved: corpus
- no_divergence_found: search; stability

### Limitations

- Equivalence is decided only within the declared input domain and the declared probes; behaviour outside them is not observed.
- Only an explicitly finite domain is proved by exhaustion; corpus comparison and counterexample search are finite evidence, not proofs.
- Thread and process scheduling, wall-clock time and hardware randomness are not controlled; their effects are handled only through declared policies.
- External services are outside the verification boundary; any request to a non-loopback host makes the run unverifiable.
- Non-exact comparison policies relax equality where the manifest says so; each application is logged, and the policy set is part of every digest.
- Structural metrics are measured only for Python sources; findings for other languages are declared by the host agent and recorded as declarations.
- A repair unit is accepted from evidence about the verified tree; INVARA does not judge whether the engineering intent was met.
