from tools.wechat_search_sogou import parse_sogou_html


def test_parse_sogou_html_result_block():
    html = """
    <ul>
      <li>
        <h3><a href="https://mp.weixin.qq.com/s/example"><em>一凌</em>策略文章</a></h3>
        <p class="txt-info">摘要内容</p>
        <a class="account">一凌策略研究</a>
      </li>
    </ul>
    """

    rows = parse_sogou_html(html, count=5)

    assert rows == [
        {
            "title": "一凌策略文章",
            "url": "https://mp.weixin.qq.com/s/example",
            "snippet": "摘要内容",
            "published_at": "",
            "account_name": "一凌策略研究",
            "source_note": "sogou_wechat_public_search_candidate",
        }
    ]
