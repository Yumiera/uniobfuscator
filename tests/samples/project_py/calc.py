"""项目级测试样本：模块 A，定义跨文件引用的函数/类/常量。"""

RATE = 1.5


def scale(value, rate=RATE):
    total = value * rate
    label = "scaled"
    print(label, total)
    return total


class Counter:
    def __init__(self, start=0):
        self.value = start

    def inc(self, step=1):
        self.value += step
        return self.value

    def current(self):
        msg = "current"
        return f"{msg}={self.value}"
