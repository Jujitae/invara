# 바꾸기 전에 약속부터 확인하기

이 문서는 공개된 `invara==0.3.0`의 약속 확인 경로입니다. 실제 설치 버전과
모듈 위치를 확인한 격리 환경에서 사용합니다.

## 사용자가 하는 일

Codex에 평소처럼 요청합니다.

> 설명을 쉽게 고쳐 줘. 우주 화면과 별을 눌러 기록을 여는 기능은 그대로 둬.
> 시작하기 전에 바꿀 것, 유지할 것, 자동으로 확인하지 못하는 것을 보여 줘.

Codex가 만든 `REVIEW.html`을 열어 **내 요청이 빠짐없이 들어갔는지** 확인합니다.
각 항목에는 자동 검사에 연결됐는지, 사람이 확인해야 하는지, 검사 방법이
아직 없는지가 표시됩니다. AI가 추가로 제안한 항목은 따로 표시합니다.

- 빠진 것이 있으면 수정 요청을 적고 `수정 요청 파일 받기`를 누릅니다.
  Codex에 파일을 전달해 새 목록을 받습니다. 수정 요청만으로 목록이 바뀌지는 않습니다.
- 맞으면 각 약속과 마지막 확인 항목을 직접 체크하고 `확인 기록 파일 받기`를
  누릅니다. 받은 JSON 파일을 **목록을 준비한 같은 Codex 작업**에 첨부합니다.
- Codex가 그 확인 기록에 연결된 조건을 고정한 뒤 작업하고 실제 검사를 실행합니다.
  `INVARA-RESULT.html`에서 항목별 결과를 봅니다.

파일을 받거나 지시문을 만드는 것은 검사 실행이 아닙니다. 연결되지 않은 항목은
그대로 미확인입니다. 이 목록이 모든 요구를 찾아냈다는 보증도 아닙니다.

## Codex 실행 절차

사용자 대신 확인 기록을 만들거나 체크하지 않습니다. 아래 명령은 실행자용이며,
사용자가 직접 JSON이나 터미널 명령을 작성할 필요는 없습니다.

1. 실제 프로젝트와 현재 화면을 확인합니다. 원본 요청을 그대로 보존합니다.
2. 요청을 `change`와 `keep` 약속으로 나눕니다. AI가 더한 항목은 `origin=agent`로
   표시합니다. 제안에 불과한 항목은 별도 `suggestions`에 둡니다.
3. 이미 실행 가능한 검사와 보호 파일을 연결합니다. 검사 준비가 안 됐거나 사람만
   확인할 수 있는 약속을 누락하지 말고 `unmapped`/`human`으로 남깁니다.
   일반적인 성공 명령이나 AI의 자기보고를 검사로 대신하지 않습니다.
4. 준비 파일을 만들고 아래 명령으로 확인 화면을 생성합니다. 출력 폴더는 프로젝트
   외부의 새 폴더를 권장합니다. 기존 증거 폴더를 덮어쓰지 않습니다.

```text
invara intent prepare proposal.json --root PROJECT --out REVIEW_DIR --language ko
```

5. `REVIEW_DIR/REVIEW.html`을 사용자에게 보여 주고 실제로 반환받은 확인 파일을 사용합니다.

```text
invara intent seal REVIEW_DIR --confirmation INVARA-CONFIRMATION.json --root PROJECT --db STATE.db
```

6. 확인된 범위 안에서 작업합니다. 원본 요청·약속·검사 범위를 바꿔야 하면 기존
   확인을 재사용하지 말고 새 목록을 만듭니다.
7. 실제 검사를 실행한 뒤 결과 파일을 사용자에게 보여 줍니다.

```text
invara intent judge REVIEW_DIR --root PROJECT --db STATE.db --language ko
```

저장된 결과만 다시 읽을 때는 다음 명령을 사용합니다. **재검사하지 않습니다.**

```text
invara intent report REVIEW_DIR --root PROJECT --db STATE.db --language ko
```

`--language en`은 영어 화면을 만듭니다. 원본 요청과 약속 문구를 자동 번역하거나
고쳐 쓰지 않습니다. 영어 사용 경로에서는 처음부터 영어 요청과 약속을 확인합니다.

CLI 종료 코드: `0` 연결된 전체 조건 충족, `1` 조건 미충족, `2` 미확인/대기,
`3` 준비·기록·실행 경로 오류. 약속별 상태와 원래 판정은 함께 확인합니다.
원래 판정이 PASS여도 사람 확인이나 미연결 항목이 남으면 전체 요청은 미확인입니다.

## 제안 파일의 구조

```json
{
  "schema": "invara.intent-proposal/1",
  "task_id": "a-unique-task-id",
  "original_request": "사용자의 원문",
  "promises": [{
    "id": "keep-required-ui",
    "text": "우주 화면과 기록 선택 기능을 유지합니다",
    "kind": "keep",
    "origin": "user",
    "mapping": {
      "kind": "machine",
      "predicate_ids": ["existing-ui-check"],
      "protected_paths": []
    }
  }],
  "suggestions": [],
  "contract": {
    "task_id": "a-unique-task-id",
    "intent": "실제 요청과 검사 범위",
    "constraints": [{
      "kind": "paths_unchanged",
      "paths": ["existing-policy-file.txt"],
      "reason": "유지하기로 한 조건"
    }],
    "done_when": [{
      "id": "existing-ui-check",
      "command": ["ACTUAL_EXECUTABLE", "checks/existing_ui_check.py"],
      "expect_exit": 0,
      "reason": "실제 관측하는 동작을 설명"
    }]
  },
  "check_files": ["checks"]
}
```

위 JSON은 구조 설명이며 실행 예제가 아닙니다. 파일과 명령은 실제 프로젝트에서
확인한 것으로 바꿔야 합니다. `machine` 연결은 기존 검사 ID나 보호 파일을 반드시
가리켜야 합니다. `human`/`unmapped`는 비어 있는 두 연결 목록과 구체적인 `reason`을
갖습니다. `check_files`에는 검사 스크립트와 확인할 검사 설정 범위를 명시합니다.

## 결과가 의미하는 범위

- 약속의 문구와 연결한 검사가 정말 같은 뜻인지는 사람이 검토해야 합니다.
  관련 없는 검사를 연결했다고 문구가 증명되지는 않습니다.
- 저장된 검사 결과와 현재 파일의 적용·배포 상태는 다릅니다. 적용·공개 상태는
  이 기능이 자동 추정하지 않으며 `UNKNOWN`으로 남깁니다.
- 검사 기록은 특정 시점의 관측입니다. 검사 명령 실행 이후의 전체 파일 상태를
  보증하지 않습니다. 기존 검증기의 관측 순서와 판정 의미를 바꾸지 않습니다.
- 여러 약속이 같은 검사에 연결될 수 있습니다. 고유 검사 수와 공유 연결을 따로
  기록하며, 약속 개수를 안전 비율로 환산하지 않습니다.
- 확인 파일은 로컬 제출 기록입니다. 사람의 신원·이해·자유의사를 인증하지 않습니다.
  같은 접근 권한의 프로그램이 파일을 만들 수 있습니다.
- 화면은 로컬 파일이며 프로젝트를 업로드하지 않습니다. 실제 검사 명령은 사용자
  권한으로 실행되므로 권한·통신·간접 의존성은 별도 검토 대상입니다.

## English path

Ask Codex for the change in ordinary language. Before any work, have it show what
will change, what must stay, and what it cannot check. Open `REVIEW.html`, correct
anything missing, then select each promise and download the confirmation record.
Attach that file to the same Codex task. Codex freezes the reviewed scope, performs
the work, runs the real checks, and opens `INVARA-RESULT.html`.

No file download starts a check. A local confirmation does not authenticate a human.
Unlinked promises and human review stay unresolved even if the original machine
verdict passes. Results describe the linked observations, not overall safety,
current deployment, or whether AI captured every part of your request.

## 사람의 재사용 평가

별도 설명 없이 목적·다음 행동·결과·한계를 사용자가 자기 말로 설명하고 올바른
수락/보류 결정을 내리는지 관찰합니다. 실제 평가 전에는 `UX_PASS`로 기록하지 않습니다.
외부 사용자 3~5명 연구는 이번 로컬 구현으로 수행됐다고 주장하지 않습니다.
