import os


def greet(name, greeting="Hello", *args, **kwargs):
    x = 5
    s = "world" + name
    for i in range(3):
        print(i, s)
    return f"{greeting}, {s} {x}"


def calc(a, b):
    total = a * b + 100
    msg = "result"
    print(msg, total, "中文测试")
    return total


class Greeter:
    def __init__(self, prefix):
        self.prefix = prefix

    def say(self, who):
        return self.prefix + " " + who + "!"


print(greet("Alice"))
print(calc(3, 4))
g = Greeter("Hi")
print(g.say("Bob"))
