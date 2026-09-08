"""A local review must not become a confirmation or a machine pass by rendering."""
import importlib.util
import re
from html.parser import HTMLParser


def views():
    assert importlib.util.find_spec('invara.intent_view'), 'Local promise review/result renderer is not implemented'
    from invara import intent_view
    return intent_view


def review():
    return {'schema':'invara.intent-review/1','review_id':'review-1',
      'original_request':'설명을 고쳐줘. 우주 화면은 그대로 둬.',
      'promises':[
       {'id':'keep-sky','text':'우주 화면 유지','kind':'keep','origin':'user','mapping':{'kind':'machine','predicate_ids':['sky-visible'],'protected_paths':[]}},
       {'id':'clear','text':'처음 보는 사람도 이해','kind':'change','origin':'agent','mapping':{'kind':'human','reason':'사람이 직접 사용해 봐야 합니다','predicate_ids':[],'protected_paths':[]}},
       {'id':'unknown','text':'다른 화면 유지','kind':'keep','origin':'user','mapping':{'kind':'unmapped','reason':'아직 연결된 검사가 없습니다','predicate_ids':[],'protected_paths':[]}}],
      'suggestions':[{'id':'logo','text':'로고도 유지할까요?'}],
      'confirmation_template':{'schema':'invara.intent-confirmation/1','review_id':'review-1','proposal_digest':'p','contract_digest':'c','root_digest':'r'}}


class Inputs(HTMLParser):
    def __init__(self):super().__init__();self.inputs=[];self.remotes=[]
    def handle_starttag(self,tag,attrs):
        a=dict(attrs)
        if tag=='input':self.inputs.append(a)
        if a.get('src','').startswith(('http:','https:')):self.remotes.append(a['src'])


def test_review_needs_actual_selection_and_shows_all_unknowns():
    page=views().render_review(review())
    parser=Inputs();parser.feed(page)
    checks=[a for a in parser.inputs if a.get('type')=='checkbox']
    assert len(checks)==4  # three promises and the final explicit confirmation
    assert all('checked' not in a for a in checks)
    assert not parser.remotes
    assert '아직 연결된 검사가 없습니다' in page
    assert '로고도 유지할까요?' in page
    assert '수정 요청' in page


def test_user_text_cannot_become_markup_or_end_script():
    data=review();data['original_request']='</script><img src=x onerror=alert(1)>'
    page=views().render_review(data)
    assert '<img src=x' not in page
    assert '&lt;/script&gt;' in page


def test_result_preserves_gap_even_when_raw_verdict_passes():
    data={'task_id':'demo','original_request':'요청','raw_verdict':{'status':'PASS'},
      'promises':[{**review()['promises'][2],'status':'UNMAPPED','evidence':[]}],
      'coverage':{'total':1,'machine_mapped':0,'human':0,'unmapped':1,'checked':0,'met':0,'not_met':0,'unverifiable':0,'pending':1},
      'application_status':'UNKNOWN','publication_status':'UNKNOWN'}
    page=views().render_report(data)
    assert '검사가 연결되지 않았습니다' in page
    assert '적용 여부를 확인하지 못했습니다' in page
    assert 'PASS' in page
    visible = re.sub(r'<style>.*?</style>', '', page, flags=re.S)
    assert '100%' not in visible
    assert '80%' not in visible


def test_english_has_same_confirmation_and_unknown_boundary():
    page=views().render_review(review(),language='en')
    assert 'lang="en"' in page
    assert 'Not checked automatically' in page
    assert 'has not started' in page


def test_unknown_status_never_uses_success_copy():
    page=views().render_report({'promises':[{'id':'x','text':'約束','status':'FUTURE_STATUS','mapping':{'kind':'machine'}}],
                              'raw_verdict':{'status':'PASS'}})
    assert '확인하지 못했습니다' in page


def test_human_pending_and_historical_check_change_are_visible():
    page=views().render_report({'historical':True,'execution_performed':False,
      'check_drift':[{'path':'checks.py','expected':'before','actual':'after'}],
      'promises':[{**review()['promises'][1],'status':'PENDING_HUMAN'}]})
    visible=page.split('<details>')[0]
    assert '이 화면을 열면서 검사를 다시 실행하지 않았습니다' in visible
    assert '직접 확인이 필요합니다' in page
    assert '검사 파일이 달라졌습니다' in page
