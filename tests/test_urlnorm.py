from ir_search.models import Hit, Query
from ir_search.pipeline import run_pipeline
from ir_search.urlnorm import canonicalize_url


class DuplicateWechatAdapter:
    name = "wechat_opencli"
    mode = "test"

    def query(self, q: Query):
        return [
            Hit(
                title="同一篇",
                url="https://mp.weixin.qq.com/s?__biz=MzA3&mid=1&idx=1&sn=abcdef&chksm=old",
                snippet="a",
                source=self.name,
            ),
            Hit(
                title="同一篇",
                url="https://mp.weixin.qq.com/s?sn=abcdef&from=timeline&chksm=new",
                snippet="longer snippet",
                source=self.name,
            ),
        ]


def test_wechat_canonicalize_prefers_sn():
    left = canonicalize_url("https://mp.weixin.qq.com/s?__biz=MzA3&mid=1&idx=1&sn=abcdef&chksm=old")
    right = canonicalize_url("https://mp.weixin.qq.com/s?sn=abcdef&from=timeline&chksm=new")

    assert left == right == "wechat:sn:abcdef"


def test_pipeline_dedups_wechat_variants_by_article_key():
    result = run_pipeline(Query(text="公众号 最新", sources=["wechat"], count=10), {"wechat_opencli": DuplicateWechatAdapter()})

    assert len(result.hits) == 1
    assert result.hits[0].canonical_url == "wechat:sn:abcdef"
    assert result.hits[0].snippet == "longer snippet"
