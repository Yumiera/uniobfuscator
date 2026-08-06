"""项目级测试样本：入口，跨文件调用 calc 模块。"""

from calc import Counter, scale


def main():
    r = scale(10)
    c = Counter(3)
    print(c.inc(2))
    print(c.current())
    return r


if __name__ == "__main__":
    print("result", main())
