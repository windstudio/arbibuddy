"""Process-level fake transport used only by installed-root black-box tests."""

from __future__ import annotations

from io import BytesIO
import urllib.request


_REAL_URLOPEN = urllib.request.urlopen
_OFFICIAL_PREFIXES = (
    "https://www.samr.gov.cn/",
    "https://www.gov.cn/",
    "https://flk.npc.gov.cn/",
)


class _ControlledResponse:
    def __init__(self, url: str) -> None:
        self._url = url
        self._body = BytesIO(
            (
                "《中华人民共和国劳动合同法》"
                "（2007年6月29日通过，根据2012年12月28日修正）"
                "第三十八条劳动者可以解除劳动合同。"
                "第四十六条劳动者依照第三十八条解除劳动合同的，用人单位应当支付经济补偿。"
                "第四十七条经济补偿按工作年限计算。"
            ).encode("utf-8")
        )

    def __enter__(self):
        return self

    def __exit__(self, *_args: object) -> None:
        self._body.close()

    def read(self, size: int = -1) -> bytes:
        return self._body.read(size)

    def geturl(self) -> str:
        return self._url


def _controlled_urlopen(request: object, *args: object, **kwargs: object):
    url = getattr(request, "full_url", str(request))
    if any(url.startswith(prefix) for prefix in _OFFICIAL_PREFIXES):
        return _ControlledResponse(url)
    return _REAL_URLOPEN(request, *args, **kwargs)


urllib.request.urlopen = _controlled_urlopen
