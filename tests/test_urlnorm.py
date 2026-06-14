from ir_search.models import Hit, Query
from ir_search.pipeline import run_pipeline
from ir_search.urlnorm import canonicalize_url, wechat_url_aliases


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


def test_wechat_aliases_include_bmi_when_sn_is_present():
    aliases = wechat_url_aliases("https://mp.weixin.qq.com/s?__biz=MzA3&mid=1&idx=2&sn=abcdef")

    assert aliases == ["wechat:sn:abcdef", "wechat:bmi:MzA3:1:2"]


def test_pipeline_dedups_wechat_variants_by_article_key():
    result = run_pipeline(Query(text="公众号 最新", sources=["wechat"], count=10), {"wechat_opencli": DuplicateWechatAdapter()})

    assert len(result.hits) == 1
    assert result.hits[0].canonical_url == "wechat:sn:abcdef"
    assert result.hits[0].snippet == "longer snippet"


def test_pipeline_dedups_wechat_sn_and_bmi_aliases():
    class AliasAdapter:
        name = "wechat_opencli"
        mode = "test"

        def query(self, q: Query):
            return [
                Hit(
                    title="同一篇",
                    url="https://mp.weixin.qq.com/s?__biz=MzA3&mid=1&idx=1",
                    snippet="bmi only",
                    source=self.name,
                ),
                Hit(
                    title="同一篇",
                    url="https://mp.weixin.qq.com/s?__biz=MzA3&mid=1&idx=1&sn=abcdef",
                    snippet="sn and bmi",
                    source=self.name,
                ),
            ]

    result = run_pipeline(Query(text="公众号 最新", sources=["wechat"], count=10), {"wechat_opencli": AliasAdapter()})

    assert len(result.hits) == 1
    assert result.hits[0].canonical_url == "wechat:sn:abcdef"
