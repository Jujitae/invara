# INV-001 채택 게이트 — 실측 기록

- **대상 커밋**: `c3362fd38156c9301178747efb397ba74782c561` (INV-001, 2026-08-30 10:35:46 +0900)
- **측정 브랜치**: `company/invara/inv-gate-001-20260830-103405` (위 커밋에서 분기, 추가 변경 없음)
- **측정 환경**: Windows 11, Python 3.12.10
- **측정한 사람**: invara 레인 게이트 워커 (무인 실행)

이 문서는 INV-001 커밋 메시지가 주장한 두 가지 — (1) 플러그인 번들이 `src` 와
다시 동기화됐다, (2) 드리프트 테스트가 그 동기화를 지킨다 — 를 코드를 읽는 것이
아니라 직접 돌려서 잰 기록이다. 승인·병합 판단은 이 문서의 몫이 아니라
이 문서를 읽는 사람의 몫이다.

## [1] 전체 테스트

```
PYTHONPATH=src python -m pytest tests -q
```

```
...................................................................s.... [ 92%]
......                                                                   [100%]
77 passed, 1 skipped in 4.49s
```

`-rs` 로 스킵 사유를 확인:

```
SKIPPED [1] tests\test_invara.py:982: no installed distribution here; nothing to compare against
```

이 스킵은 이 워크트리에 `invara` 가 `pip install` 된 배포본으로 설치돼 있지
않아서 발생한다 — 버전 문자열을 배포 메타데이터와 대조하는 테스트라 소스
동기화나 드리프트 검증과는 무관하다. 커밋 메시지가 적어둔 "78 passed" 는
아마 배포본이 설치된 환경에서 잰 숫자다 (77 + 1 = 78, 스킵된 그 한 건이
설치된 환경에서는 실행됐다는 뜻과 맞는다).

**판정: 통과.** 실패 없음.

## [2] 주장 (1) — 번들이 src 와 일치하는가

파일 목록과 바이트를 직접 비교했다.

```
diff -rq src/invara plugin/src/invara
```

```
Only in src/invara: __pycache__
```

(`__pycache__` 는 빌드 산출물이지 소스가 아니다. 소스 7개 파일은 차이 없음.)

MCP 서버가 광고하는 도구 이름도 양쪽에서 직접 뽑아 비교했다:

```python
# PYTHONPATH=src
sorted(t['name'] for t in mcp._tools())
# ['invara_chain', 'invara_judge', 'invara_list', 'invara_log', 'invara_replay', 'invara_seal']

# PYTHONPATH=plugin/src
sorted(t['name'] for t in mcp._tools())
# ['invara_chain', 'invara_judge', 'invara_list', 'invara_log', 'invara_replay', 'invara_seal']
```

여섯 개, 완전히 동일. INV-001 이 고쳤다고 주장한 결함(`invara_replay` 가
번들에서 빠져 다섯 개만 나가던 것)이 지금은 없다.

**판정: 확인됨.** 지금 시점에서 `plugin/src/invara/` 는 `src/invara/` 의
바이트 단위 복사본이고, 광고하는 도구 목록도 일치한다.

## [3] 주장 (2) — 드리프트 테스트가 실제로 불일치를 잡는가

관련 테스트: `tests/test_invara.py` 의
`TheSilentAliasSpeaks::test_the_plugin_bundle_carries_the_store_alias_fix`
(src의 `*.py` 전체를 발견해 번들과 바이트 비교) 와
`test_the_plugin_readme_names_every_tool_it_ships`
(README 가 광고 도구 이름·개수와 일치하는지 비교).

**절차**: `plugin/src/invara/mcp.py` 에서 `invara_replay` 도구 정의 블록을
통째로 지워 INV-001 이 고쳤다는 그 결함을 그대로 재현했다. 그 상태에서
드리프트 관련 테스트만 돌렸다:

```
PYTHONPATH=src python -m pytest tests -q -k "plugin or drift or bundle"
```

```
.F..                                                                     [100%]
FAILED tests/test_invara.py::TheSilentAliasSpeaks::test_the_plugin_bundle_carries_the_store_alias_fix
1 failed, 3 passed, 74 deselected in 0.20s
```

실패 메시지: `plugin/src/invara/mcp.py differs from src/invara/mcp.py`.
바이트 비교 방식이라 도구 하나가 빠진 것을 정확히 그 파일 이름으로 짚어냈다.

**원복**: 지운 블록을 그대로 되돌리고 `git status --short` 로 원본과 바이트
단위로 다시 같아졌는지 확인 (출력 없음 = 차이 없음). 그 뒤 전체 테스트를
다시 돌렸다:

```
PYTHONPATH=src python -m pytest tests -q
```

```
...................................................................s.... [ 92%]
......                                                                   [100%]
77 passed, 1 skipped in 4.32s
```

원복 전과 동일한 결과(77 passed, 1 skipped)로 다시 초록.

**판정: 확인됨.** 드리프트 테스트는 실제로 도구 하나가 빠지는 것을 잡는다 —
이름을 나열하는 방식이 아니라 `src/invara/*.py` 를 발견해서 전부 비교하는
방식이라, 지금 없는 미래의 새 모듈이 번들에서 빠져도 같은 방식으로 잡힐
것으로 보인다 (이 부분은 새 모듈로 실측하지 않았고, 코드 구조로부터의
추론이다).

## [4] 플러그인 디렉터리 제출 패킷 — 저장소 안 인벤토리

제출은 하지 않았다. Console 계정 권한이 필요한 행위이고 Founder 승인 전이다.
아래는 저장소 안에 이미 존재하는 제출 관련 파일들의 위치와 채워진 내용을
그대로 옮긴 목록이다.

| 파일 | 용도 | 상태 |
|---|---|---|
| `.claude-plugin/marketplace.json` | Claude Code 플러그인 마켓플레이스 목록 파일 (`/plugin marketplace add` 가 읽는 것) | 채워짐 — name, description, owner, category(`security`), source(`./plugin`), homepage |
| `plugin/.claude-plugin/plugin.json` | 플러그인 매니페스트 | 채워짐 — name, description, version(`0.1.2`), author, homepage, repository, license(`Apache-2.0`), keywords 8개 |
| `plugin/.mcp.json` | 플러그인이 구동하는 MCP 서버 정의 | 채워짐 — stdio, `python -m invara.mcp`, `PYTHONPATH=${CLAUDE_PLUGIN_ROOT}/src` |
| `plugin/README.md` | 플러그인 전용 README (마켓플레이스에 노출되는 설명) | 채워짐 — 여섯 도구 이름 전부 명시, 설치법, 트러블슈팅(`doctor`) 링크 |
| `plugin/commands/doctor.md` | `/invara:doctor` 슬래시 커맨드 정의 | 채워짐 |
| `plugin/LICENSE`, `plugin/NOTICE` | `plugin/` 단독 배포(`git-subdir`) 시 함께 나가는 라이선스 | 채워짐 — 루트와 바이트 동일 (`test_the_plugin_bundle_carries_its_own_license` 로 지켜짐) |
| `server.json` | MCP 레지스트리(공식 MCP registry) 서버 서술자 — 플러그인 마켓플레이스와는 별개 채널 | 채워짐 — `io.github.Jujitae/invara`, pypi 패키지 `invara` 0.1.2, `uvx` runtimeHint |

버전 일치도 테스트가 지키고 있다 — `test_the_plugin_manifest_version_matches_the_package`
가 `plugin/.claude-plugin/plugin.json` 의 version 과 `pyproject.toml` 의
version 이 다르면 실패한다.

**판정: 인벤토리 완료, 제출 행위 없음.** 저장소 안 필드는 관측한 한 비어있지
않다. 실제로 Claude Code 마켓플레이스나 MCP 레지스트리에 제출됐는지, 심사가
어느 단계인지는 이 저장소 밖의 정보라 이 게이트가 확인할 수 없다.

## 종합

| 항목 | 판정 |
|---|---|
| 전체 테스트 | 통과 (77 passed, 1 skipped — 스킵은 환경 요인, 무관) |
| 주장 (1): 번들-src 동기화 | 확인됨 — 바이트 단위 일치, 도구 6개 일치 |
| 주장 (2): 드리프트 테스트의 유효성 | 확인됨 — 인위적 결함 주입 시 적색, 원복 시 다시 초록 |
| 제출 패킷 인벤토리 | 저장소 안 필드는 채워짐. 제출 여부는 저장소 밖 정보 |

main 병합 여부는 이 게이트가 결정하지 않는다. 이 문서는 그 결정에 필요한
실측 증거이고, 판단은 이 문서를 읽는 사람 몫이다.
