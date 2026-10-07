from datetime import datetime, timezone
import pytest
from clipper.manager_config import ManagerConfig
from clipper.source_policy import SpeakingSource, verified_moments
from clipper.models import Moment
from clipper.trend_research import rank_sources


def source():
    return SpeakingSource(video_id='LBOMd0f-5Xg',start=35.3,end=68.7,verified_note='Original footage checked: Ronaldo himself answers')


def test_recommendations_exclude_pundits_even_when_title_matches():
    def item(id):
        return dict(id=id,snippet=dict(title='Cristiano Ronaldo interview',channelId='official',channelTitle='Real Madrid',publishedAt='2016-08-25T20:21:04Z',defaultAudioLanguage='es'),status=dict(privacyStatus='public',license='creativeCommon'),contentDetails=dict(duration='PT1M18S'),statistics=dict(viewCount='10000',likeCount='100'))
    cfg=ManagerConfig(source_format='ronaldo_speaking',speaking_sources=[source()])
    rows=rank_sources([item('LBOMd0f-5Xg'),item('punditvideo1')],cfg,[],None,set(),'owned')
    assert [r['id'] for r in rows]==['LBOMd0f-5Xg']
    assert rows[0]['archival'] is True


def test_answer_boundaries_reject_interviewer_and_outro():
    def moment(a,b):
        return Moment(start=a,end=b,title='Interview',hook_score=5,reason='test',keywords=[])
    valid=moment(35.4,68.6)
    assert verified_moments([moment(0,40),valid,moment(40,75)],source())==[valid]


def test_empty_catalog_recommends_nothing():
    assert rank_sources([],ManagerConfig(source_format='ronaldo_speaking'),[],None,set(),'own')==[]


def test_short_answer_cannot_be_approved():
    with pytest.raises(ValueError):
        SpeakingSource(video_id='LBOMd0f-5Xg',start=50,end=68,verified_note='Ronaldo himself visibly speaks in this answer')
