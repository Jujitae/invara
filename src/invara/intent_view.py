"""Offline views for recorded intent scope; no execution or authority inference."""
from __future__ import annotations

import html
import json


def esc(value):
    return html.escape(str(value), quote=True)


def pick(language, ko, en):
    return en if language == 'en' else ko


CSS = '''
label.promise{display:flex;gap:14px;align-items:flex-start;cursor:pointer}label.promise input{flex:0 0 23px}
:root{color-scheme:dark;--bg:#101411;--paper:#19201b;--ink:#f3f1e8;--muted:#b9c4b9;--line:#495649;--accent:#cbe0a0;--warn:#f2cf8b;--bad:#ffb4a5}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:17px/1.7 system-ui,-apple-system,"Malgun Gothic",sans-serif}main,header,footer{width:min(880px,calc(100% - 40px));margin:auto}header{padding:28px 0;border-bottom:1px solid var(--line)}header strong{letter-spacing:.08em}main{padding:40px 0 60px}h1{font-size:clamp(30px,5vw,46px);line-height:1.25;letter-spacing:-.035em;max-width:22ch;margin:0 0 22px}h2{font-size:24px;line-height:1.4;margin:44px 0 16px}h3{font-size:19px;line-height:1.5;margin:0 0 6px}p{margin:10px 0;max-width:70ch;word-break:keep-all;overflow-wrap:anywhere}.muted,small{color:var(--muted)}.request{padding:22px;background:var(--paper);border:1px solid var(--line);border-radius:6px;white-space:pre-wrap}.promise{padding:22px 0;border-bottom:1px solid var(--line)}.promise label{display:flex;gap:14px;align-items:flex-start;min-height:48px;cursor:pointer}.promise label span{min-width:0}.promise .detail{margin:8px 0 0 38px;font-size:15px;color:var(--muted)}input[type=checkbox]{width:23px;height:23px;flex-shrink:0;margin-top:5px;accent-color:var(--accent)}button,.download{font:inherit;font-weight:650;padding:13px 21px;min-height:48px;border-radius:5px;border:1px solid var(--accent);background:var(--accent);color:#152014;cursor:pointer;white-space:normal}button:disabled{opacity:.45;cursor:not-allowed}button:hover:enabled{background:#deedc0}button.secondary{background:transparent;color:var(--ink);border-color:var(--line)}button.secondary:hover{background:var(--paper)}textarea{width:100%;min-height:120px;display:block;font:inherit;background:var(--paper);color:var(--ink);border:1px solid var(--line);border-radius:5px;padding:14px;margin:12px 0;caret-color:var(--accent)}:focus-visible{outline:3px solid var(--accent);outline-offset:4px}::selection{background:#cbe0a0;color:#152014}.actions{display:flex;gap:12px;flex-wrap:wrap;margin:22px 0}.warning{color:var(--warn)}.problem{color:var(--bad)}.status{display:block;font-size:15px;color:var(--warn)}.state{padding:18px 0;border-block:1px solid var(--line)}details{margin:22px 0}summary{cursor:pointer;min-height:48px;padding:10px 0}pre{white-space:pre-wrap;overflow-wrap:anywhere;font:13px/1.6 ui-monospace,monospace;background:var(--paper);padding:18px;border-radius:5px}footer{padding:24px 0;border-top:1px solid var(--line);font-size:14px;color:var(--muted)}[hidden]{display:none!important}@media(max-width:540px){main{padding-top:28px}h2{margin-top:34px}button{width:100%}.request{padding:17px}.promise .detail{margin-left:0}body{font-size:16px}header{padding:20px 0}}@media(prefers-reduced-motion:reduce){*{scroll-behavior:auto}}
'''


def shell(title, content, language, script=''):
    lang = 'en' if language == 'en' else 'ko'
    foot = pick(lang, '이 파일은 내 컴퓨터에서 읽습니다. 웹사이트에 프로젝트를 보내지 않습니다. 검사 명령 자체의 권한과 통신은 별도로 확인해야 합니다.',
                'Read this file locally. This page does not upload your project. Review the permissions and network behavior of the actual check commands separately.')
    return f'''<!doctype html><html lang="{lang}" translate="no"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="google" content="notranslate"><meta name="referrer" content="no-referrer"><title>{esc(title)} · INVARA</title><style>{CSS}</style></head><body><header><strong>INVARA</strong></header><main><h1>{esc(title)}</h1>{content}</main><footer>{esc(foot)}</footer>{script}</body></html>'''


def _mapping_text(promise, language):
    mapping = promise.get('mapping', {})
    kind = mapping.get('kind')
    if kind == 'machine':
        if mapping.get('reason'):
            return pick(language, '확인 방법: ', 'How it is checked: ') + str(mapping['reason'])
        names = [*mapping.get('predicate_ids', []), *mapping.get('protected_paths', [])]
        return pick(language, '자동 검사에 연결됨', 'Connected to checks') + ': ' + ', '.join(map(str, names))
    return pick(language, '자동으로 확인하지 못합니다', 'Not checked automatically') + ' · ' + str(mapping.get('reason') or pick(language, '확인 방법이 정해지지 않았습니다', 'No checking method has been specified'))


def render_review(review, language='ko'):
    t = lambda ko, en: pick(language, ko, en)
    title = t('시작하기 전에, 약속을 확인해 주세요.', 'Before work starts, check the promises.')
    parts = [f'<p class="muted">{t("이 화면에서는 작업이나 검사를 실행하지 않습니다. 이 목록이 요청을 제대로 담았는지 먼저 확인해 주세요.", "This page has not started any work or checks. First check whether this list reflects what you asked for.")}</p>',
             f'<h2>{t("내가 요청한 일", "Your request")}</h2><div class="request">{esc(review.get("original_request", ""))}</div>']
    promises = review.get('promises', [])
    for kind, ko, en in [('change','이번에 바꿀 것','What will change'),('keep','그대로 둘 것','What will stay')]:
        rows = [p for p in promises if p.get('kind') == kind]
        if not rows:continue
        parts.append(f'<h2>{t(ko,en)}</h2>')
        for p in rows:
            origin=t('내 요청에서 가져옴','From your request') if p.get('origin')=='user' else t('AI가 추가로 제안함','Suggested by AI')
            parts.append(f'<div class="promise"><label><input type="checkbox" class="promise-check" data-id="{esc(p["id"])}"><span><strong>{esc(p["text"])}</strong><small class="status">{origin}</small></span></label><p class="detail">{esc(_mapping_text(p,language))}</p></div>')
    if review.get('suggestions'):
        parts.append(f'<h2>{t("아직 포함하지 않은 제안", "Suggestions not yet included")}</h2><p class="muted">{t("포함하고 싶다면 아래에 수정 요청을 적어 주세요. 지금 목록에 자동으로 추가되지는 않습니다.", "Ask for a revised list below if you want to include these. They are not automatically part of this scope.")}</p><ul>')
        parts.extend(f'<li>{esc(s["text"])}</li>' for s in review['suggestions']);parts.append('</ul>')
    parts.append(f'''<h2>{t('빠뜨린 것이 있나요?', 'Anything missing?')}</h2><p>{t('고칠 점이 있으면 먼저 전달하고 새 목록을 받아 확인해 주세요. AI가 모든 요구를 찾아냈다고 보증하는 목록은 아닙니다.', 'Send corrections and review a new list before continuing. This list does not establish that AI found every requirement.')}</p>
<label for="correction">{t('수정 요청', 'Request a correction')}</label><textarea id="correction" placeholder="{t('예: 가격과 모바일 화면도 그대로 유지해 주세요.', 'Example: Keep the price and mobile layout unchanged too.')}" maxlength="10000"></textarea><button type="button" class="secondary" id="request-change">{t('수정 요청 파일 받기','Download correction request')}</button>
<h2>{t('이 약속대로 진행할까요?', 'Continue with these promises?')}</h2><label class="promise"><input type="checkbox" id="confirm-scope"> {t('위 항목과 자동으로 확인할 수 없는 부분을 읽었습니다.', 'I have reviewed the items and what cannot be checked automatically.')}</label>
<div class="actions"><button type="button" id="download-confirmation" disabled>{t('확인 기록 파일 받기', 'Download confirmation record')}</button></div><p id="feedback" role="status" aria-live="polite">{t('각 약속을 체크하면 확인 기록을 받을 수 있습니다. 파일을 받는 것만으로 검사가 실행되지는 않습니다.', 'Select each promise to download your confirmation. Downloading a file does not run the checks.')}</p>
<p class="muted">{t('받은 확인 기록 파일을 이 작업을 준비한 Codex에 전달해 주세요. 원본 요청이나 검사 범위가 바뀌면 다시 확인해야 합니다.', 'Give the downloaded confirmation file to the Codex task that prepared this review. Changes to the request or checking scope require a new review.')}</p>
<details><summary>{t('검사 범위와 원문 보기', 'Checking scope and original record')}</summary><pre>{esc(json.dumps(review,ensure_ascii=False,indent=2))}</pre><p>{t('로컬 제출 기록은 사람의 신원이나 실제 이해를 인증하지 않습니다. 같은 컴퓨터의 프로그램이 파일을 만들 수 있다는 한계가 있습니다.', 'A local submission does not authenticate a person or establish understanding. Other programs with the same access can create files.')}</p></details>''')
    data = {'template':review.get('confirmation_template', {}),'ids':[p['id'] for p in promises], 'review_id':review.get('review_id'), 'en':language=='en'}
    encoded=json.dumps(data,ensure_ascii=False).replace('<','\\u003c').replace('>','\\u003e').replace('&','\\u0026')
    script='''<script>(()=>{'use strict';const data=__DATA__;const checks=[...document.querySelectorAll('.promise-check')];const final=document.getElementById('confirm-scope');const correction=document.getElementById('correction');const button=document.getElementById('download-confirmation');const feedback=document.getElementById('feedback');
function ready(){return checks.length===data.ids.length&&checks.length>0&&checks.every(c=>c.checked)&&final.checked&&!correction.value.trim();}
function sync(){button.disabled=!ready();if(correction.value.trim())feedback.textContent=data.en?'Send your correction and review a new list before confirming.':'수정 요청을 전달하고 새 목록을 받은 뒤 확인해 주세요.';}
checks.forEach(c=>c.addEventListener('change',sync));final.addEventListener('change',sync);correction.addEventListener('input',sync);
function download(value,name){const blob=new Blob([JSON.stringify(value,null,2)],{type:'application/json;charset=utf-8'});const url=URL.createObjectURL(blob);const link=document.createElement('a');link.href=url;link.download=name;document.body.append(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);}
button.addEventListener('click',()=>{if(!ready())return;try{download({...data.template,decision:'confirm',accepted_promise_ids:data.ids,reviewed_at:new Date().toISOString()},'INVARA-CONFIRMATION.json');feedback.textContent=data.en?'Download requested. Check your downloads, then attach the file to your Codex task. No work or check was started by this page.':'다운로드를 요청했습니다. 파일이 내려받아졌는지 확인하고 Codex에 첨부해 주세요. 이 화면에서는 작업이나 검사를 시작하지 않았습니다.';}catch(e){feedback.textContent=data.en?'The file could not be downloaded. Keep this page and ask Codex for help.':'파일을 받지 못했습니다. 이 화면을 유지하고 Codex에 도움을 요청해 주세요.';}});
document.getElementById('request-change').addEventListener('click',()=>{if(!correction.value.trim()){feedback.textContent=data.en?'Write what you want to change first.':'먼저 고칠 점을 적어 주세요.';correction.focus();return;}try{download({schema:'invara.intent-correction/1',review_id:data.review_id,request:correction.value.trim()},'INVARA-CORRECTION.json');feedback.textContent=data.en?'Correction download requested. Give it to Codex and ask for a new review.':'수정 요청 다운로드를 요청했습니다. Codex에 전달하고 새 목록을 받아 주세요.';}catch(e){feedback.textContent=data.en?'Download failed. Copy your correction into Codex.':'다운로드하지 못했습니다. 적은 내용을 복사해 Codex에 전달해 주세요.';}});sync();})();</script>'''.replace('__DATA__',encoded)
    return shell(title,''.join(parts),language,script)


def render_report(report, language='ko'):
    t=lambda ko,en:pick(language,ko,en)
    labels={
      'MET':t('연결한 검사를 통과했습니다','The linked checks passed'),
      'NOT_MET':t('지키지 못한 조건이 있습니다','A linked condition was not met'),
      'UNMAPPED':t('검사가 연결되지 않았습니다','No check is linked'),
      'HUMAN_REVIEW':t('직접 확인이 필요합니다','A person needs to check this'),
      'PENDING_HUMAN':t('직접 확인이 필요합니다','A person needs to check this'),
      'UNVERIFIABLE':t('확인하지 못했습니다','Could not check'),
      'PENDING':t('아직 검사하지 않았습니다','Not checked yet')}
    promises=report.get('promises',[])
    parts=[f'<p class="muted">{t("아래 결과는 기록된 검사 범위에 한정됩니다. 모든 요구를 찾아냈거나 프로그램 전체가 안전하다는 뜻은 아닙니다.", "These results cover the recorded checks. They do not establish that every requirement was found or that the whole program is safe.")}</p>',
      f'<h2>{t("지금 적용된 상태", "Current application state")}</h2><div class="state"><p>{t("적용 여부를 확인하지 못했습니다.", "Application status has not been verified.")}</p><p>{t("공개·배포 여부도 이 검사 결과만으로는 알 수 없습니다.", "This check alone does not establish publication or deployment.")}</p></div>',
      f'<h2>{t("약속별 확인 결과", "Results for each promise")}</h2><p>{t("이 목록의 약속", "Promises in this list")}: {len(promises)}</p>']
    if report.get('execution_performed') is False:
        parts.insert(2, f'<p class="warning">{t("이 화면을 열면서 검사를 다시 실행하지 않았습니다. 저장된 기록을 보여줍니다.", "Opening this page did not run the checks again. It displays a saved record.")}</p>')
    if not promises:parts.append(f'<p class="warning">{t("표시할 약속 기록이 없어 확인하지 못했습니다.", "No promise records are available; nothing is confirmed.")}</p>')
    for p in promises:
        status=labels.get(p.get('status'),t('확인하지 못했습니다','Could not check'))
        parts.append(f'<section class="promise"><h3>{esc(p.get("text",p.get("id","")))}</h3><strong class="status">{esc(status)}</strong><p class="muted">{esc(_mapping_text(p,language))}</p><details><summary>{t("이 항목의 근거", "Evidence for this item")}</summary><pre>{esc(json.dumps(p.get("evidence",[]),ensure_ascii=False,indent=2))}</pre></details></section>')
    failures=[p for p in promises if p.get('status')=='NOT_MET']
    parts.append(f'<h2>{t("발견한 문제", "Problems found")}</h2>')
    if failures:
        parts.append('<ul>'+''.join(f'<li>{esc(p.get("text",p.get("id","")))}</li>' for p in failures)+'</ul>')
    else:
        parts.append(f'<p>{t("이 기록에서 조건 위반으로 표시된 약속은 없습니다. 미확인 항목까지 통과했다는 뜻은 아닙니다.", "No promise in this record is marked as a failed condition. Unchecked items have not passed.")}</p>')
    if report.get('check_drift'):
        parts.append(f'<p class="problem">{t("검사 파일이 달라졌습니다. 이전 결과를 현재 변경의 확인 결과로 사용하지 마세요. 검사 범위를 다시 확인해야 합니다.", "Check files have changed. Do not use the earlier result to approve the current change. Review the checking scope again.")}</p>')
    parts.append(f'<h2>{t("확인하지 못한 것", "What remains unknown")}</h2><p>{t("사람 확인이나 미연결로 남은 항목은 통과한 항목에 포함하지 않습니다. 약속 개수를 안전 비율로 환산하지 않습니다. 기록 이후의 변경은 이 결과가 보증하지 않습니다.", "Human-review and unlinked items are not counted as passed. Promise counts are not a safety percentage. This record does not cover later changes.")}</p>')
    next_text=t('문제가 있거나 확인하지 못한 항목부터 Codex와 살펴보세요. 이미 바뀐 파일을 자동으로 되돌리지는 마세요.', 'Review failed or unchecked items with Codex first. Do not automatically undo files that have already changed.')
    parts.append(f'<h2>{t("지금 할 일", "What to do next")}</h2><p>{next_text}</p><details><summary>{t("원래 판정과 전체 근거", "Original verdict and full evidence")}</summary><pre>{esc(json.dumps(report,ensure_ascii=False,indent=2))}</pre></details>')
    return shell(t('요청한 내용을 확인한 결과', 'What the checks found'),''.join(parts),language)
