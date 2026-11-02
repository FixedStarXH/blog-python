import re

SENSITIVE_WORDS=["傻逼","傻比","傻b","脑残","白痴","弱智","贱人","婊子","妈的","操你妈","去你妈","你妈逼","卧槽你妈","草泥马","滚蛋","去死","死全家","废物","垃圾货","sb","tmd","nmd","fuck","shit","加微信","加我微信","微信号","加vx","威信号","加qq","加群","私聊","代刷","刷单","兼职","转账","汇款","赌博","博彩","下注","赌球","色情","裸聊","约炮","一夜情","法轮功","六四","天安门事件","台独","藏独","疆独","港独"]


def filter_sensitive(text: str) -> str:
    for word in SENSITIVE_WORDS:
        text=re.sub(re.escape(word),"*"*len(word),text,flags=re.IGNORECASE)
    return text
